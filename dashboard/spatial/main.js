import * as THREE from "./vendor/three.module.js";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { addLights, buildHome } from "./scene.js";

const HOME_ID = "home1";
const CUTAWAY_SCALE = 0.35;

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

function render() {
  home.walls.scale.y = 1;
  home.mounted.forEach((m) => (m.visible = true));
  const zonesWereVisible = home.zones.visible;
  home.zones.visible = false;
  glasses.render(scene, glassesCam);
  home.zones.visible = zonesWereVisible;
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
main.setAnimationLoop(() => { render(); frames++; });

// Hooks for the verification flows: pick a glasses preset and read its frame or the overview FPS.
window.__spatial = {
  manifest,
  setGlasses,
  glassesPNG: () => { render(); return glasses.domElement.toDataURL("image/png"); },
  selectObject: (id) => select(home.byId.get(id) ?? null),
  screenOf: (id) => {
    const p = new THREE.Box3().setFromObject(home.byId.get(id)).getCenter(new THREE.Vector3()).project(camera);
    const r = main.domElement.getBoundingClientRect();
    return { x: r.left + (p.x + 1) / 2 * r.width, y: r.top + (1 - p.y) / 2 * r.height };
  },
  fps: () => frames / ((performance.now() - fpsStart) / 1000),
  ready: true,
};
