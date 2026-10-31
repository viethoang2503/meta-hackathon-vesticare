import * as THREE from "./vendor/three.module.js";
import { poseAt } from "./scene.js?v=home4";

// A joint hierarchy in metres. Route time and travelled distance own the animation,
// so polling, refreshes and frame rate do not change the gait.
export function createWearer() {
  const root = new THREE.Group();
  const skin = new THREE.MeshStandardMaterial({ color: 0xd9ad88, roughness: 0.8 });
  const shirt = new THREE.MeshStandardMaterial({ color: 0x477d95, roughness: 0.9 });
  const trousers = new THREE.MeshStandardMaterial({ color: 0x394651, roughness: 0.9 });
  const shoes = new THREE.MeshStandardMaterial({ color: 0x302f2d, roughness: 0.9 });
  function part(parent, geometry, material, x, y, z) {
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(x, y, z);
    mesh.castShadow = mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }
  function joint(parent, x, y, z) {
    const g = new THREE.Group();
    g.position.set(x, y, z);
    parent.add(g);
    return g;
  }
  part(root, new THREE.CapsuleGeometry(0.17, 0.3, 4, 12), shirt, 0, 1.12, 0).scale.z = 0.75;
  part(root, new THREE.SphereGeometry(0.135, 16, 12), skin, 0, 1.53, 0);
  part(root, new THREE.CylinderGeometry(0.055, 0.06, 0.1, 12), skin, 0, 1.38, 0);
  const hair = part(root, new THREE.SphereGeometry(0.138, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2),
    new THREE.MeshStandardMaterial({ color: 0xaca79f }), 0, 1.56, 0);
  hair.scale.y = 0.75;
  part(root, new THREE.BoxGeometry(0.25, 0.035, 0.045), shoes, 0, 1.56, 0.12);
  const arms = [], elbows = [], legs = [], knees = [];
  for (const side of [-1, 1]) {
    const arm = joint(root, side * 0.225, 1.3, 0);
    part(arm, new THREE.CapsuleGeometry(0.055, 0.18, 3, 10), shirt, 0, -0.13, 0);
    const elbow = joint(arm, 0, -0.27, 0);
    part(elbow, new THREE.CapsuleGeometry(0.042, 0.17, 3, 10), skin, 0, -0.12, 0);
    part(elbow, new THREE.SphereGeometry(0.045, 10, 8), skin, 0, -0.255, 0);
    const leg = joint(root, side * 0.1, 0.83, 0);
    part(leg, new THREE.CapsuleGeometry(0.075, 0.25, 3, 10), trousers, 0, -0.19, 0);
    const knee = joint(leg, 0, -0.39, 0);
    part(knee, new THREE.CapsuleGeometry(0.06, 0.24, 3, 10), trousers, 0, -0.18, 0);
    part(knee, new THREE.BoxGeometry(0.14, 0.09, 0.25), shoes, 0, -0.385, 0.045);
    arms.push(arm); elbows.push(elbow); legs.push(leg); knees.push(knee);
  }
  root.visible = false;
  return { root, arms, elbows, legs, knees };
}

export function animateWearer(rig, route, ms) {
  const pose = poseAt(route, ms);
  let distance = 0, heading = 0, speed = 0;
  for (let i = 1; i < route.length; i++) {
    const a = route[i - 1], b = route[i];
    if (ms < a[0]) break;
    const dx = b[1] - a[1], dz = b[3] - a[3];
    const length = Math.hypot(dx, dz);
    const fraction = THREE.MathUtils.clamp((ms - a[0]) / (b[0] - a[0]), 0, 1);
    distance += length * fraction;
    if (length > 0.001 && a[2] >= 1.59 && b[2] >= 1.59) heading = Math.atan2(dx, dz);
    if (ms < b[0]) {
      speed = length / ((b[0] - a[0]) / 1000);
      break;
    }
  }
  const fall = THREE.MathUtils.clamp((1.6 - pose.eye[1]) / 1.25, 0, 1);
  const tilt = fall * fall * (3 - 2 * fall) * Math.PI / 2;
  const phase = distance * Math.PI * 2 / 1.1;
  const walking = Math.min(speed / 0.5, 1) * (1 - fall);
  rig.root.quaternion.setFromAxisAngle(new THREE.Vector3(0, 1, 0), heading);
  rig.root.quaternion.multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), tilt));
  // Anchor to the authored eye pose before resolving contact with the floor.
  const eyeOffset = new THREE.Vector3(0, 1.6, 0.1).applyQuaternion(rig.root.quaternion);
  rig.root.position.fromArray(pose.eye).sub(eyeOffset);
  rig.root.visible = true;
  for (let i = 0; i < 2; i++) {
    const swing = Math.sin(phase + i * Math.PI);
    rig.legs[i].rotation.x = swing * 0.4 * walking;
    rig.knees[i].rotation.x = Math.max(0, -swing) * 0.55 * walking;
    rig.arms[i].rotation.x = -swing * 0.3 * walking - fall * 0.65;
    rig.elbows[i].rotation.x = -0.12 - fall * 0.25;
  }
  rig.root.updateMatrixWorld(true);
  const floor = Math.max(0, pose.eye[1] - 1.6);
  const lowest = new THREE.Box3().setFromObject(rig.root).min.y;
  if (lowest < floor) rig.root.position.y += floor - lowest;
  return pose;
}
