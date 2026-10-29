// Builds the home from assets/homes/<id>/manifest.json. The manifest is the only
// source of layout: rooms, walls, stairs, zones, objects and camera presets.
// Furniture is composed from primitives here, so there are no external assets.

import * as THREE from "./vendor/three.module.js";

const COLORS = {
  wall: 0xece6da,
  trim: 0xf6f2ea,
  oak: 0xc8a27c,
  oak_light: 0xd8bf98,
  tile_warm: 0xd9cbb5,
  tile_cool: 0xcfd6d6,
  wood: 0x9c7352,
  wood_dark: 0x6f4f37,
  fabric: 0x8a9a7b,
  fabric_warm: 0xb98a6a,
  linen: 0xeee7da,
  white: 0xf3f3f0,
  steel: 0xb9bcbf,
  black: 0x2b2b2b,
  rug: 0xa6604a,
  leaf: 0x5d7f4f,
  terracotta: 0xb4643f,
  glass: 0xbfd8e6,
  lamp: 0xfff1cc,
};

const materials = new Map();
function mat(name, extra = {}) {
  const key = name + JSON.stringify(extra);
  if (!materials.has(key)) {
    materials.set(key, new THREE.MeshStandardMaterial({ color: COLORS[name], roughness: 0.8, ...extra }));
  }
  return materials.get(key);
}

function box(group, w, h, d, x, y, z, material) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  m.position.set(x, y + h / 2, z);
  m.castShadow = m.receiveShadow = true;
  group.add(m);
  return m;
}

function cylinder(group, rTop, rBottom, h, x, y, z, material, segments = 20) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(rTop, rBottom, h, segments), material);
  m.position.set(x, y + h / 2, z);
  m.castShadow = m.receiveShadow = true;
  group.add(m);
  return m;
}

function legs(group, w, d, h, inset, material, r = 0.025) {
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    cylinder(group, r, r, h, sx * (w / 2 - inset), 0, sz * (d / 2 - inset), material, 8);
  }
}

// Local frame: footprint centred on the origin, the front faces +z.
const FURNITURE = {
  sofa(g) {
    box(g, 2.0, 0.42, 0.9, 0, 0, 0, mat("fabric"));
    box(g, 2.0, 0.5, 0.2, 0, 0.42, -0.35, mat("fabric"));
    for (const x of [-0.9, 0.9]) box(g, 0.2, 0.25, 0.9, x, 0.42, 0, mat("fabric"));
    for (const x of [-0.45, 0.45]) box(g, 0.85, 0.1, 0.6, x, 0.42, 0.08, mat("linen"));
  },
  armchair(g) {
    box(g, 0.9, 0.42, 0.85, 0, 0, 0, mat("fabric_warm"));
    box(g, 0.9, 0.5, 0.18, 0, 0.42, -0.33, mat("fabric_warm"));
    for (const x of [-0.38, 0.38]) box(g, 0.14, 0.22, 0.85, x, 0.42, 0, mat("fabric_warm"));
  },
  coffee_table(g) {
    box(g, 1.1, 0.05, 0.6, 0, 0.38, 0, mat("wood"));
    legs(g, 1.1, 0.6, 0.38, 0.06, mat("wood_dark"));
  },
  rug(g) {
    box(g, 2.4, 0.012, 1.7, 0, 0, 0, mat("rug"));
    box(g, 2.0, 0.014, 1.3, 0, 0, 0, mat("fabric_warm"));
  },
  tv_stand(g) {
    box(g, 1.6, 0.5, 0.4, 0, 0, 0, mat("wood_dark"));
    box(g, 1.2, 0.7, 0.05, 0, 0.6, -0.05, mat("black"));
    box(g, 0.2, 0.1, 0.1, 0, 0.5, -0.05, mat("black"));
  },
  floor_lamp(g) {
    cylinder(g, 0.18, 0.18, 0.03, 0, 0, 0, mat("black"));
    cylinder(g, 0.015, 0.015, 1.5, 0, 0.03, 0, mat("black"), 8);
    cylinder(g, 0.12, 0.22, 0.3, 0, 1.45, 0, mat("lamp", { emissive: 0xffe2a8, emissiveIntensity: 0.6 }));
  },
  table_lamp(g) {
    cylinder(g, 0.06, 0.08, 0.2, 0, 0, 0, mat("terracotta"));
    cylinder(g, 0.08, 0.13, 0.16, 0, 0.2, 0, mat("lamp", { emissive: 0xffe2a8, emissiveIntensity: 0.6 }));
  },
  plant(g) {
    cylinder(g, 0.2, 0.15, 0.35, 0, 0, 0, mat("terracotta"));
    const leaves = new THREE.Mesh(new THREE.IcosahedronGeometry(0.38, 0), mat("leaf", { flatShading: true }));
    leaves.position.set(0, 0.75, 0);
    leaves.castShadow = true;
    g.add(leaves);
  },
  counter(g, o) {
    const L = o.length ?? 3;
    box(g, L, 0.86, 0.6, 0, 0, 0, mat("white"));
    box(g, L, 0.04, 0.62, 0, 0.86, 0, mat("wood"));
    box(g, L, 0.6, 0.02, 0, 0.9, -0.3, mat("tile_cool"));
    box(g, L, 0.7, 0.35, 0, 1.5, -0.13, mat("white"));
  },
  sink(g) {
    box(g, 0.6, 0.02, 0.45, 0, 0.9, 0.02, mat("steel"));
    cylinder(g, 0.015, 0.015, 0.25, 0, 0.9, -0.2, mat("steel"), 8);
  },
  stove(g) {
    box(g, 0.6, 0.02, 0.55, 0, 0.9, 0.02, mat("black"));
    for (const x of [-0.14, 0.14]) for (const z of [-0.1, 0.14]) cylinder(g, 0.08, 0.08, 0.01, x, 0.92, z, mat("steel"));
  },
  fridge(g) {
    box(g, 0.7, 1.85, 0.65, 0, 0, 0, mat("steel"));
    box(g, 0.03, 0.6, 0.03, -0.28, 1.0, 0.34, mat("black"));
  },
  dining_table(g) {
    box(g, 1.4, 0.05, 0.9, 0, 0.72, 0, mat("wood"));
    legs(g, 1.4, 0.9, 0.72, 0.08, mat("wood_dark"), 0.03);
  },
  chair(g) {
    box(g, 0.44, 0.05, 0.44, 0, 0.44, 0, mat("wood"));
    legs(g, 0.44, 0.44, 0.44, 0.04, mat("wood_dark"), 0.018);
    box(g, 0.44, 0.45, 0.04, 0, 0.49, -0.2, mat("wood"));
  },
  bed(g) {
    box(g, 1.6, 0.3, 2.1, 0, 0, 0, mat("wood"));
    box(g, 1.55, 0.22, 2.0, 0, 0.3, 0.02, mat("linen"));
    box(g, 1.6, 0.9, 0.08, 0, 0, -1.03, mat("wood_dark"));
    for (const x of [-0.4, 0.4]) box(g, 0.6, 0.12, 0.35, x, 0.52, -0.75, mat("white"));
    box(g, 1.6, 0.06, 1.2, 0, 0.52, 0.35, mat("fabric"));
  },
  nightstand(g) {
    box(g, 0.45, 0.5, 0.4, 0, 0, 0, mat("wood"));
  },
  wardrobe(g) {
    box(g, 1.2, 2.0, 0.55, 0, 0, 0, mat("wood"));
    box(g, 0.02, 1.9, 0.01, 0, 0.05, 0.28, mat("wood_dark"));
  },
  toilet(g) {
    box(g, 0.4, 0.4, 0.5, 0, 0, 0.05, mat("white"));
    box(g, 0.4, 0.35, 0.18, 0, 0.4, -0.2, mat("white"));
  },
  bath_sink(g) {
    box(g, 0.7, 0.8, 0.45, 0, 0, 0, mat("wood"));
    box(g, 0.5, 0.06, 0.36, 0, 0.8, 0.02, mat("white"));
  },
  mirror(g) {
    box(g, 0.6, 0.8, 0.02, 0, 1.2, 0, mat("glass", { metalness: 0.6, roughness: 0.1 }));
  },
  shower(g) {
    box(g, 0.9, 0.05, 0.9, 0, 0, 0, mat("white"));
    const glass = mat("glass", { transparent: true, opacity: 0.35, roughness: 0.1 });
    box(g, 0.9, 1.9, 0.02, 0, 0.05, 0.45, glass);
    box(g, 0.02, 1.9, 0.9, -0.45, 0.05, 0, glass);
  },
  shoe_rack(g) {
    box(g, 0.8, 0.5, 0.3, 0, 0, 0, mat("wood"));
  },
  ceiling_lamp(g) {
    cylinder(g, 0.25, 0.25, 0.05, 0, 2.5, 0, mat("lamp", { emissive: 0xffe2a8, emissiveIntensity: 0.8 }));
  },
};

function buildFloors(manifest, root) {
  for (const room of manifest.rooms) {
    const [x0, x1] = room.rect.x, [z0, z1] = room.rect.z;
    const floor = new THREE.Mesh(new THREE.BoxGeometry(x1 - x0, 0.05, z1 - z0), mat(room.floor));
    floor.position.set((x0 + x1) / 2, -0.025, (z0 + z1) / 2);
    floor.receiveShadow = true;
    floor.userData.roomId = room.id;
    root.add(floor);
  }
}

function buildWalls(manifest) {
  const walls = new THREE.Group();
  const H = manifest.wall_height, T = 0.12;
  for (const w of manifest.walls) {
    const [ax, az] = w.from, [bx, bz] = w.to;
    const length = Math.hypot(bx - ax, bz - az);
    const seg = new THREE.Group();
    seg.position.set(ax, 0, az);
    seg.rotation.y = -Math.atan2(bz - az, bx - ax);
    const pieces = [];
    let cursor = 0;
    for (const o of [...(w.openings ?? [])].sort((p, q) => p.at - q.at)) {
      const start = o.at - o.width / 2, end = o.at + o.width / 2;
      pieces.push([cursor, start, 0, H]);
      if (o.kind === "window") {
        pieces.push([start, end, 0, o.sill], [start, end, o.head, H]);
        const pane = new THREE.Mesh(new THREE.BoxGeometry(o.width, o.head - o.sill, 0.02),
          mat("glass", { transparent: true, opacity: 0.45, roughness: 0.05 }));
        pane.position.set(o.at, (o.sill + o.head) / 2, 0);
        seg.add(pane);
      } else {
        pieces.push([start, end, 2.1, H]);
        if (o.kind === "door") {
          const leaf = new THREE.Group();
          leaf.position.set(start, 0, 0);
          leaf.rotation.y = -1.2;
          box(leaf, o.width, 2.05, 0.04, o.width / 2, 0, 0, mat("wood"));
          leaf.userData.objectId = o.id ?? null;
          seg.add(leaf);
        }
      }
      cursor = end;
    }
    pieces.push([cursor, length, 0, H]);
    for (const [s, e, y0, y1] of pieces) {
      if (e - s < 1e-3 || y1 - y0 < 1e-3) continue;
      box(seg, e - s, y1 - y0, T, (s + e) / 2, y0, 0, mat("wall"));
    }
    walls.add(seg);
  }
  return walls;
}

function buildStairs(spec) {
  const g = new THREE.Group();
  g.userData.objectId = spec.id;
  const [sx, sz] = spec.start;
  const zc = sz + spec.width / 2;
  for (let i = 0; i < spec.steps; i++) {
    const h = (i + 1) * spec.rise;
    box(g, spec.tread, h, spec.width, sx + (i + 0.5) * spec.tread, 0, zc, mat(i % 2 ? "oak" : "oak_light"));
  }
  const top = spec.steps * spec.rise;
  const L = spec.landing;
  const landing = box(g, L.x[1] - L.x[0], top, L.z[1] - L.z[0], (L.x[0] + L.x[1]) / 2, 0, (L.z[0] + L.z[1]) / 2, mat("oak"));
  landing.userData.objectId = L.id;
  const rail = new THREE.Group();
  rail.userData.objectId = spec.handrail.id;
  const railZ = spec.handrail.side === "north" ? sz + 0.05 : sz + spec.width - 0.05;
  const run = spec.steps * spec.tread;
  for (let i = 0; i <= spec.steps; i += 2) {
    const x = sx + i * spec.tread;
    cylinder(rail, 0.02, 0.02, spec.handrail.height, x, Math.min(i, spec.steps) * spec.rise, railZ, mat("wood_dark"), 8);
  }
  const bar = new THREE.Mesh(new THREE.BoxGeometry(Math.hypot(run, top), 0.05, 0.05), mat("wood_dark"));
  bar.position.set(sx + run / 2, top / 2 + spec.handrail.height, railZ);
  bar.rotation.z = Math.atan2(top, run);
  bar.castShadow = true;
  rail.add(bar);
  g.add(rail);
  return g;
}

function buildZones(manifest) {
  const zones = new THREE.Group();
  for (const z of manifest.zones.filter((z) => z.fall_exposure)) {
    const [x0, x1] = z.rect.x, [z0, z1] = z.rect.z;
    const m = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, z1 - z0),
      new THREE.MeshBasicMaterial({ color: 0xd9822b, transparent: true, opacity: 0.22, depthWrite: false }));
    m.rotation.x = -Math.PI / 2;
    m.position.set((x0 + x1) / 2, 0.02, (z0 + z1) / 2);
    m.userData.zoneId = z.id;
    zones.add(m);
  }
  return zones;
}

export function buildHome(manifest) {
  const root = new THREE.Group();
  const byId = new Map();
  buildFloors(manifest, root);
  const walls = buildWalls(manifest);
  root.add(walls);
  const stairs = buildStairs(manifest.stairs);
  root.add(stairs);
  byId.set(manifest.stairs.id, stairs);
  for (const o of manifest.objects) {
    const make = FURNITURE[o.kind];
    if (!make) throw new Error(`No furniture builder for kind ${o.kind}`);
    const g = new THREE.Group();
    make(g, o);
    const onTop = o.on ? byId.get(o.on) : null;
    const lift = onTop ? new THREE.Box3().setFromObject(onTop).max.y : 0;
    g.position.set(o.at[0], lift, o.at[1]);
    g.rotation.y = THREE.MathUtils.degToRad(o.rot ?? 0);
    g.userData.objectId = o.id;
    g.userData.zone = o.zone;
    root.add(g);
    byId.set(o.id, g);
  }
  const selectable = [stairs, ...manifest.objects.filter((o) => o.selectable).map((o) => byId.get(o.id))];
  const mounted = manifest.objects.filter((o) => o.wall_mounted).map((o) => byId.get(o.id));
  const zones = buildZones(manifest);
  root.add(zones);
  return { root, walls, zones, byId, selectable, mounted };
}

export function zoneAt(manifest, x, z) {
  const hits = manifest.zones.filter((zn) => x >= zn.rect.x[0] && x <= zn.rect.x[1] && z >= zn.rect.z[0] && z <= zn.rect.z[1]);
  return hits.find((zn) => zn.fall_exposure) ?? hits[0] ?? null;
}

export function addLights(scene) {
  scene.add(new THREE.HemisphereLight(0xfff6e5, 0x8a7a66, 1.1));
  const sun = new THREE.DirectionalLight(0xfff0dc, 1.6);
  sun.position.set(6, 14, 8);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -9, right: 9, top: 9, bottom: -9, near: 1, far: 40 });
  sun.shadow.bias = -0.0005;
  scene.add(sun);
}
