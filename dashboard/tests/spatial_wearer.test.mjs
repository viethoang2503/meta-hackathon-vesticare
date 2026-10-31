import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import * as THREE from "../spatial/vendor/three.module.js";
import { createWearer, animateWearer } from "../spatial/wearer.js";
import { buildHome } from "../spatial/scene.js";
const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url)));
const manifest = read("../../assets/homes/home1/manifest.json");
const home = buildHome(manifest);
home.root.updateMatrixWorld(true);

for (const id of ["S1", "S2"]) {
  test(`${id}: animated limbs and falling body clear furniture, walls, stairs and floor`, () => {
    const obstacles = [];
    const add = (group, id) => group.traverse((mesh) => {
      if (mesh.isMesh) obstacles.push({ id, bounds: new THREE.Box3().setFromObject(mesh) });
    });
    add(home.walls, "wall or door");
    add(home.byId.get("stairs"), "stairs or guardrail");
    for (const object of manifest.objects) {
      if (!object.wall_mounted && !["rug", "ceiling_lamp"].includes(object.kind)) add(home.byId.get(object.id), object.id);
    }
    const rig = createWearer();
    const { route, duration_ms } = read(`../../assets/scenarios/${id}/scenario.json`);
    for (let ms = 0; ms <= duration_ms; ms += 20) {
      animateWearer(rig, route, ms);
      rig.root.updateMatrixWorld(true);
      rig.root.traverse((mesh) => {
        if (!mesh.isMesh) return;
        const bounds = new THREE.Box3().setFromObject(mesh);
        assert.ok(bounds.min.y >= -1e-6, `body below floor at ${ms} ms`);
        for (const o of obstacles) assert.equal(bounds.intersectsBox(o.bounds), false, `${o.id} at ${ms} ms`);
      });
    }
  });
}

test("walking swings opposite arms and legs; stationary poses stop stepping", () => {
  const rig = createWearer();
  const route = [[0, 0, 1.6, 0, 0, 1.6, 1], [4000, 0, 1.6, 2, 0, 1.6, 3], [6000, 0, 1.6, 2, 0, 1.6, 3]];
  animateWearer(rig, route, 700);
  assert.ok(Math.abs(rig.legs[0].rotation.x) > 0.1);
  assert.ok(rig.legs[0].rotation.x * rig.legs[1].rotation.x < 0);
  assert.ok(rig.arms[0].rotation.x * rig.legs[0].rotation.x < 0);
  animateWearer(rig, route, 5000);
  assert.equal(Math.abs(rig.legs[0].rotation.x), 0);
  assert.equal(Math.abs(rig.legs[1].rotation.x), 0);
});

test("refresh or seeking produces the same pose at the same replay time", () => {
  const { route } = read("../../assets/scenarios/S2/scenario.json");
  const a = createWearer(), b = createWearer();
  animateWearer(a, route, 10000);
  animateWearer(a, route, 24500);
  animateWearer(b, route, 24500);
  assert.deepEqual(a.root.position.toArray(), b.root.position.toArray());
  assert.deepEqual(a.root.quaternion.toArray(), b.root.quaternion.toArray());
  animateWearer(a, route, 24400);
  const before = a.root.quaternion.clone();
  animateWearer(a, route, 24420);
  assert.ok(before.angleTo(a.root.quaternion) < 0.1, "fall tilts continuously");
});

test("hazard patches follow each tread and the elevated landing", () => {
  const patches = home.zones.children.filter((m) => m.userData.zoneId === "stairs_flight");
  assert.equal(patches.length, 8);
  patches.forEach((patch, i) => assert.ok(Math.abs(patch.position.y - ((i + 1) * 0.18 + 0.008)) < 1e-6));
  const landing = home.zones.children.find((m) => m.userData.zoneId === "stairs_landing");
  assert.ok(Math.abs(landing.position.y - 1.448) < 1e-6);
  assert.equal(home.ceiling.visible, false);
  assert.ok(home.ceiling.children.length > 0);
  assert.ok(home.byId.get("stairs").children.some((g) => g.userData.objectId === "landing_guardrail"));
});
