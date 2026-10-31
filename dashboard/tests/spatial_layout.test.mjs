import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import * as THREE from "../spatial/vendor/three.module.js";
import { buildHome } from "../spatial/scene.js";

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url)));
const manifest = read("../../assets/homes/home1/manifest.json");
const home = buildHome(manifest);
home.root.updateMatrixWorld(true);
const radius = 0.23;
const furniture = manifest.objects.filter((o) => !o.wall_mounted && !["rug", "ceiling_lamp"].includes(o.kind));

// Sweep the wearer's footprint along the entire segment, including between frames.
function crosses(a, b, bounds) {
  let enter = 0, leave = 1;
  for (const [axis, index] of [["x", 1], ["z", 3]]) {
    const low = bounds.min[axis] - radius, high = bounds.max[axis] + radius;
    const delta = b[index] - a[index];
    if (Math.abs(delta) < 1e-9) {
      if (a[index] < low || a[index] > high) return false;
    } else {
      const t0 = (low - a[index]) / delta, t1 = (high - a[index]) / delta;
      enter = Math.max(enter, Math.min(t0, t1));
      leave = Math.min(leave, Math.max(t0, t1));
      if (enter > leave) return false;
    }
  }
  return true;
}

for (const id of ["S1", "S2"]) {
  test(`${id}: the wearer can follow the whole route without crossing furniture`, () => {
    const { route } = read(`../../assets/scenarios/${id}/scenario.json`);
    const collisions = [];
    for (const object of furniture) {
      const bounds = new THREE.Box3().setFromObject(home.byId.get(object.id));
      for (let i = 1; i < route.length; i++) {
        if (crosses(route[i - 1], route[i], bounds)) collisions.push(`${object.id}: ${route[i - 1][0]}–${route[i][0]} ms`);
      }
    }
    assert.deepEqual(collisions, []);
  });
}

test("TV leaves the living-room doorway and its approach clear", () => {
  const bounds = new THREE.Box3().setFromObject(home.byId.get("tv_stand"));
  const doorway = new THREE.Box3(new THREE.Vector3(-3.7, 0, -0.8), new THREE.Vector3(-2.3, 2.1, 0.8));
  assert.equal(bounds.intersectsBox(doorway), false);
  assert.ok(bounds.max.x < -5, "TV stays against the west wall");
});

test("route check catches the original dining-table collision between waypoints", () => {
  const { route } = read("../../assets/scenarios/S2/scenario.json");
  const oldTable = new THREE.Box3(new THREE.Vector3(1.7, 0, 1.55), new THREE.Vector3(3.1, 0.77, 2.45));
  assert.equal(crosses(route[0], route[1], oldTable), true);
});

for (const id of ["S1", "S2"]) {
  test(`${id}: doors, handles and walls leave the walking route clear`, () => {
    const { route } = read(`../../assets/scenarios/${id}/scenario.json`);
    const obstacles = [];
    home.walls.traverse((o) => {
      if (o.isMesh) {
        const bounds = new THREE.Box3().setFromObject(o);
        if (bounds.min.y < 1.6 && bounds.max.y > 0) obstacles.push(bounds);
      }
    });
    const collisions = [];
    for (let i = 1; i < route.length; i++) {
      // The falling body needs a different envelope; these segments check walking.
      if (route[i - 1][2] < 1.6 || route[i][2] < 1.6) continue;
      for (const bounds of obstacles) {
        if (crosses(route[i - 1], route[i], bounds)) collisions.push({ from: route[i - 1][0], to: route[i][0], at: bounds.getCenter(new THREE.Vector3()).toArray() });
      }
    }
    assert.deepEqual(collisions, []);
  });
}

test("route check detects the former bathroom door swinging into the hallway", () => {
  const original = structuredClone(manifest);
  const partition = original.walls.find((w) => w.from[0] === -6 && w.from[1] === -1.2);
  delete partition.openings.find((o) => o.at === 6).swing_degrees;
  const blockedHome = buildHome(original);
  blockedHome.root.updateMatrixWorld(true);
  const { route } = read("../../assets/scenarios/S1/scenario.json");
  let blocked = false;
  blockedHome.walls.traverse((o) => {
    if (!o.isMesh) return;
    const bounds = new THREE.Box3().setFromObject(o);
    if (bounds.min.y < 1.6 && crosses(route[1], route[2], bounds)) blocked = true;
  });
  assert.equal(blocked, true);
});
