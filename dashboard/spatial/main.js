import * as THREE from "./vendor/three.module.js";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { addLights, buildHome, poseAt } from "./scene.js";
import { openIncident } from "./evidence.js";
import { createSession } from "./session.js";

const HOME_ID = "home1";
const CUTAWAY_SCALE = 0.35;
const LYING_EYE_HEIGHT = 1.0;

const manifest = await (await fetch(`/spatial-assets/homes/${HOME_ID}/manifest.json`)).json();

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14171c);
addLights(scene);
const home = buildHome(manifest);
scene.add(home.root);

const view = document.getElementById("view");
const main = new THREE.WebGLRenderer({ antialias: true });
main.setPixelRatio(Math.min(devicePixelRatio, 2));
main.shadowMap.enabled = true;
main.shadowMap.type = THREE.PCFSoftShadowMap;
view.appendChild(main.domElement);

const overview = manifest.cameras.overview;
const camera = new THREE.PerspectiveCamera(overview.fov, 1, 0.1, 100);
const controls = new OrbitControls(camera, main.domElement);
function resetView() {
  camera.position.set(...overview.position);
  controls.target.set(...overview.target);
  controls.update();
}
resetView();

const glassesBox = document.getElementById("glasses");
const glasses = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
glasses.setPixelRatio(1);
glasses.shadowMap.enabled = true;
glassesBox.appendChild(glasses.domElement);
const glassesCam = new THREE.PerspectiveCamera(70, 4 / 3, 0.05, 50);

const presetSelect = document.getElementById("glasses-preset");
const presets = Object.keys(manifest.cameras).filter((k) => k.startsWith("glasses_"));
for (const name of presets) presetSelect.add(new Option(name.replace("glasses_", "").replaceAll("_", " "), name));
function setGlasses(name) {
  const p = manifest.cameras[name];
  glassesCam.fov = p.fov;
  glassesCam.position.set(...p.position);
  glassesCam.lookAt(...p.target);
  glassesCam.updateProjectionMatrix();
  presetSelect.value = name;
}
presetSelect.addEventListener("change", () => setGlasses(presetSelect.value));
setGlasses(presets[0]);

function aimGlasses(pose) {
  glassesCam.fov = 70;
  glassesCam.position.set(...pose.eye);
  glassesCam.lookAt(...pose.look);
  glassesCam.updateProjectionMatrix();
}

const cutaway = document.getElementById("cutaway");
const hazards = document.getElementById("hazards");
hazards.addEventListener("change", () => { home.zones.visible = hazards.checked; tags.forEach((t) => t.hazard && (t.el.hidden = !hazards.checked)); });
document.getElementById("reset-view").addEventListener("click", resetView);

const tags = manifest.zones.map((z) => {
  const el = document.createElement("div");
  el.className = "zone-tag" + (z.fall_exposure ? " hazard" : "");
  el.textContent = z.label;
  view.appendChild(el);
  const y = z.id === "stairs_landing" ? manifest.stairs.steps * manifest.stairs.rise + 0.3 : 0.3;
  return { el, hazard: z.fall_exposure, at: new THREE.Vector3((z.rect.x[0] + z.rect.x[1]) / 2, y, (z.rect.z[0] + z.rect.z[1]) / 2) };
});

const wearer = new THREE.Group();
{
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.18, 1.0, 4, 12), new THREE.MeshStandardMaterial({ color: 0x3f7cc4 }));
  body.position.y = 0.68;
  body.castShadow = true;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.14, 16, 12), new THREE.MeshStandardMaterial({ color: 0xe8c4a0 }));
  head.position.y = 1.45;
  wearer.add(body, head);
  wearer.visible = false;
  scene.add(wearer);
}
function placeWearer(pose) {
  const [x, y, z] = pose.eye;
  const heading = Math.atan2(pose.look[0] - x, pose.look[2] - z);
  wearer.visible = true;
  wearer.position.set(x, 0, z);
  wearer.rotation.set(0, heading, 0);
  if (y < LYING_EYE_HEIGHT) {
    wearer.rotation.x = Math.PI / 2;
    wearer.position.y = 0.2;
  }
}

const markers = new Map();
const markerMaterial = new THREE.MeshStandardMaterial({ color: 0xe0483e, emissive: 0x7a1410 });
function syncMarkers(incidents) {
  for (const inc of incidents) {
    if (markers.has(inc.id)) continue;
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.28, 20, 14), markerMaterial);
    m.position.set(inc.location.x, 1.0, inc.location.z);
    m.userData.incidentId = inc.id;
    scene.add(m);
    markers.set(inc.id, m);
  }
  for (const [id, m] of markers) {
    if (!incidents.some((i) => i.id === id)) {
      scene.remove(m);
      markers.delete(id);
    }
  }
}

const selection = document.getElementById("selection");
const raycaster = new THREE.Raycaster();
let highlighted = null;
function selectableOf(o) {
  for (let n = o; n; n = n.parent) if (home.selectable.includes(n)) return n;
  return null;
}
function select(group) {
  if (highlighted) highlighted.traverse((m) => m.isMesh && m.material.userData.base && (m.material = m.material.userData.base));
  highlighted = group;
  if (!group) {
    selection.innerHTML = '<span class="muted">Nothing selected.</span>';
    return;
  }
  group.traverse((m) => {
    if (!m.isMesh) return;
    const hl = m.material.clone();
    hl.emissive = new THREE.Color(0xd9822b);
    hl.emissiveIntensity = 0.35;
    hl.userData.base = m.material;
    m.material = hl;
  });
  const id = group.userData.objectId;
  const spec = manifest.objects.find((o) => o.id === id);
  const zoneId = spec?.zone ?? manifest.stairs.zone;
  const zone = manifest.zones.find((z) => z.id === zoneId);
  selection.innerHTML = `<b>${id}</b><br><span class="muted">Zone</span> ${zone.label}` +
    (zone.fall_exposure ? `<br><span class="muted">Hazard prompt</span> ${zone.hazard_prompt}` : "");
}
main.domElement.addEventListener("click", (ev) => {
  const r = main.domElement.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), camera);
  const markerHit = raycaster.intersectObjects([...markers.values()])[0];
  if (markerHit) {
    openIncident(markerHit.object.userData.incidentId);
    return;
  }
  const hit = raycaster.intersectObjects(home.selectable, true)[0];
  select(hit ? selectableOf(hit.object) : null);
});

function resize() {
  main.setSize(view.clientWidth, view.clientHeight);
  camera.aspect = view.clientWidth / view.clientHeight;
  camera.updateProjectionMatrix();
  glasses.setSize(glassesBox.clientWidth, glassesBox.clientWidth * 3 / 4, false);
}
addEventListener("resize", resize);
resize();

function renderGlasses(renderer) {
  home.walls.scale.y = 1;
  home.mounted.forEach((m) => (m.visible = true));
  const zonesVisible = home.zones.visible, wearerVisible = wearer.visible;
  home.zones.visible = wearer.visible = false;
  markers.forEach((m) => (m.visible = false));
  renderer.render(scene, glassesCam);
  home.zones.visible = zonesVisible;
  wearer.visible = wearerVisible;
  markers.forEach((m) => (m.visible = true));
}

const session = createSession({
  onSnapshot(snapshot) {
    syncMarkers(snapshot ? snapshot.incidents : []);
    presetSelect.disabled = Boolean(snapshot);
    if (!snapshot) wearer.visible = false;
  },
});

function render(now) {
  const live = session.current(now);
  if (live) {
    const pose = poseAt(live.route, live.offset_ms);
    placeWearer(pose);
    aimGlasses(pose);
  }
  const pulse = 1 + 0.15 * Math.sin(now / 200);
  markers.forEach((m) => m.scale.setScalar(pulse));
  renderGlasses(glasses);
  home.walls.scale.y = cutaway.checked ? CUTAWAY_SCALE : 1;
  home.mounted.forEach((m) => (m.visible = !cutaway.checked));
  main.render(scene, camera);
  for (const t of tags) {
    const p = t.at.clone().project(camera);
    t.el.style.left = `${(p.x + 1) / 2 * view.clientWidth}px`;
    t.el.style.top = `${(1 - p.y) / 2 * view.clientHeight}px`;
  }
}
let frames = 0;
const fpsStart = performance.now();
main.setAnimationLoop((now) => { render(now); frames++; });

function projectToScreen(point) {
  const p = point.clone().project(camera);
  const r = main.domElement.getBoundingClientRect();
  return { x: r.left + (p.x + 1) / 2 * r.width, y: r.top + (1 - p.y) / 2 * r.height };
}

// Hooks for verification flows and backend/tools/export_frames.py. Not used by the page itself.
window.__spatial = {
  manifest,
  setGlasses,
  glassesPNG: () => { renderGlasses(glasses); return glasses.domElement.toDataURL("image/png"); },
  renderPose: (pose, width, height, quality = 0.85) => {
    const r = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    r.setPixelRatio(1);
    r.shadowMap.enabled = true;
    r.setSize(width, height, false);
    const saved = { fov: glassesCam.fov, aspect: glassesCam.aspect, pos: glassesCam.position.clone(), quat: glassesCam.quaternion.clone() };
    glassesCam.aspect = width / height;
    aimGlasses(pose);
    renderGlasses(r);
    const url = r.domElement.toDataURL("image/jpeg", quality);
    Object.assign(glassesCam, { fov: saved.fov, aspect: saved.aspect });
    glassesCam.position.copy(saved.pos);
    glassesCam.quaternion.copy(saved.quat);
    glassesCam.updateProjectionMatrix();
    r.dispose();
    return url;
  },
  screenOf: (id) => projectToScreen(new THREE.Box3().setFromObject(home.byId.get(id)).getCenter(new THREE.Vector3())),
  markerScreen: (incidentId) => projectToScreen(markers.get(incidentId).position),
  fps: () => frames / ((performance.now() - fpsStart) / 1000),
  ready: true,
};
