// Laptop-side controls for the M3 stairs trials (docs/spatial/MVP2.md, M3).
const TRIALS = [
  ["lit", "Start: stairs, good light"],
  ["dim", "Start: stairs, dim light"],
  ["none", "Start: no stairs in view"],
];
const NAMES = { lit: "Stairs, good light", dim: "Stairs, dim light", none: "No stairs" };
const TARGETS = { lit: "found in at least 80%", dim: "reported only", none: "false positives at most 10%" };
const $ = (id) => document.getElementById(id);
let session = null;
let open = false;

async function api(path, options = {}) {
  const res = await fetch(`/api/spatial/${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

function setControls() {
  for (const b of $("buttons").children) b.disabled = !session || open;
  $("stop-trial").disabled = !session || !open;
  $("end").disabled = !session;
}

for (const [condition, label] of TRIALS) {
  const button = document.createElement("button");
  button.textContent = label;
  button.disabled = true;
  button.onclick = async () => {
    try {
      await api(`sessions/${session}/stairs-trials`, { method: "POST", body: JSON.stringify({ condition }) });
      open = true;
      setControls();
    } catch (e) {
      $("now").textContent = e.message;
    }
  };
  $("buttons").append(button);
}

$("stop-trial").onclick = async () => {
  try {
    await api(`sessions/${session}/stairs-trials/end`, { method: "POST" });
    open = false;
    setControls();
  } catch (e) {
    $("now").textContent = e.message;
  }
};

$("begin").onclick = async () => {
  try {
    const snap = await api("live", { method: "POST", body: JSON.stringify({
      patient_id: 1, device_id: "camera", source: "camera_trial", notification_mode: "simulated" }) });
    session = snap.id;
    $("begin").disabled = true;
    setControls();
  } catch (e) {
    $("camera").textContent = e.message;
  }
};

$("end").onclick = async () => {
  await api(`sessions/${session}/stop`, { method: "POST" });
  session = null;
  open = false;
  setControls();
};

const row = (cells, tag = "td") => `<tr>${cells.map((c) => `<${tag}>${c}</${tag}>`).join("")}</tr>`;

function renderReport(report) {
  const latency = (l) => (l.n ? `${l.p50_ms} / ${l.p95_ms} ms (${l.n} readings)` : "none yet");
  const rows = Object.entries(report.conditions).map(([k, c]) => row([
    NAMES[k], TARGETS[k], `${c.found} of ${c.valid}${c.rate === null ? "" : ` (${Math.round(c.rate * 100)}%)`}`,
    `${c.no_result} no result, ${c.pending} running`, latency(c.latency), `<span class="${c.status}">${c.status}</span>`]));
  $("report").innerHTML = `<p>Overall <b class="${report.status}">${report.status}</b>. Each condition needs 10 trials with a result.</p>` +
    `<table>${row(["Condition", "Target", "Stairs reported", "Not counted", "SAM latency p50 / p95", "Status"], "th")}${rows.join("")}</table>`;
}

async function refresh() {
  if (!session) return;
  try {
    const snap = await api(`sessions/${session}`);
    const cam = snap.camera;
    $("camera").textContent = !cam || !cam.frames_received
      ? `Waiting for the phone camera. Phone: ${snap.wearer?.status ?? "Unknown"}.`
      : `${cam.frames_received} frames received, last one ${cam.last_frame_age_s} s ago.`;
    const report = await api(`sessions/${session}/stairs-report`);
    open = report.trials.some((t) => !t.ended);
    const last = report.trials.at(-1);
    $("now").textContent = open
      ? `Trial ${last.trial} (${NAMES[last.condition]}) is open: ${last.calls.length} readings so far.`
      : "No trial open.";
    setControls();
    renderReport(report);
  } catch (e) {
    $("now").textContent = e.message;
  }
}
// After a refresh, take the running camera session up again instead of leaving Start blocked by a 409.
api("live/active").then((snap) => {
  if (snap.live?.config?.source !== "camera_trial") return;
  session = snap.id;
  $("begin").disabled = true;
  setControls();
}).catch(() => {}).finally(() => setInterval(refresh, 1000));
