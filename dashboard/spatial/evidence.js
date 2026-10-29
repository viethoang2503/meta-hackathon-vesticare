// Evidence drawer for one incident: location, glasses frame, IMU around the
// impact, decision and action log. Every block names where its data came from.

const $ = (id) => document.getElementById(id);

let openId = null;
let openKey = "";

const STATE_LABEL = {
  awaiting_wearer: "Waiting for the wearer",
  cancelled_by_wearer: "Cancelled by the wearer",
  alerted: "Caregiver alerted",
};

function fmt(ms) {
  return `${(ms / 1000).toFixed(2)} s`;
}

function drawImu(canvas, detail) {
  const ctx = canvas.getContext("2d");
  const w = (canvas.width = canvas.clientWidth * devicePixelRatio);
  const h = (canvas.height = canvas.clientHeight * devicePixelRatio);
  ctx.clearRect(0, 0, w, h);
  const rows = detail.imu.samples;
  if (!rows.length) return;
  const t0 = rows[0][0], t1 = rows[rows.length - 1][0];
  const x = (ms) => ((ms - t0) / (t1 - t0 || 1)) * w;
  const series = [
    { color: "#e7e3db", value: (r) => r[7], max: Math.max(3, ...rows.map((r) => r[7])) },
    { color: "#5aa0e0", value: (r) => Math.hypot(r[4], r[5], r[6]), max: Math.max(100, ...rows.map((r) => Math.hypot(r[4], r[5], r[6]))) },
  ];
  ctx.lineWidth = 1.5 * devicePixelRatio;
  for (const s of series) {
    ctx.strokeStyle = s.color;
    ctx.beginPath();
    rows.forEach((r, i) => {
      const y = h - (s.value(r) / s.max) * (h - 6) - 3;
      i ? ctx.lineTo(x(r[0]), y) : ctx.moveTo(x(r[0]), y);
    });
    ctx.stroke();
  }
  ctx.strokeStyle = "#e0483e";
  ctx.setLineDash([4 * devicePixelRatio, 4 * devicePixelRatio]);
  ctx.beginPath();
  ctx.moveTo(x(detail.source_start_ms), 0);
  ctx.lineTo(x(detail.source_start_ms), h);
  ctx.stroke();
  ctx.setLineDash([]);
}

function render(detail) {
  const loc = detail.location;
  const frame = detail.frame;
  const frameHtml = frame.status === "Available"
    ? `<img id="evidence-frame" src="${frame.url}" alt="Glasses frame at ${fmt(frame.source_ms)}">
       <div class="source">${frame.label} · ${frame.scene} · frame at ${fmt(frame.source_ms)}, ${frame.alignment_error_ms} ms from the incident</div>`
    : `<div class="unavailable" id="evidence-frame-unavailable">Frame unavailable: ${frame.reason}</div>`;
  const actions = detail.actions.length
    ? detail.actions.map((a) => `<li>${a.kind} → ${a.target ?? ""} <span class="muted">${new Date(a.created_at + "Z").toLocaleTimeString()}</span></li>`).join("")
    : '<li class="muted">No actions yet.</li>';
  $("evidence").innerHTML = `
    <div class="evidence-head">
      <b>${detail.alert_type}</b> · incident ${detail.id}
      <span class="state state-${detail.state}" id="evidence-state">${STATE_LABEL[detail.state] ?? detail.state}</span>
    </div>
    <div><span class="muted">Where</span> <span id="evidence-zone">${loc.zone_label}</span>
      <span class="source-inline">${loc.source}</span></div>
    <div><span class="muted">When</span> ${fmt(detail.source_start_ms)} into ${detail.scenario_id}</div>
    ${frameHtml}
    <canvas id="evidence-imu" class="imu" aria-label="IMU around the incident"></canvas>
    <div class="source">${detail.imu.source}: SVM (g, light) and |gyro| (deg/s, blue), ±5 s around the incident (red).</div>
    <div class="facts">
      <span class="muted">Decided by</span> ${detail.decision_source}
      · <span class="muted">fall p</span> ${detail.fall_probability.toFixed(2)}
      · <span class="muted">peak SVM</span> ${detail.peak_svm.toFixed(2)} g
    </div>
    <h3>Actions</h3>
    <ul id="evidence-actions">${actions}</ul>`;
  drawImu($("evidence-imu"), detail);
}

export async function openIncident(id) {
  const r = await fetch(`/api/spatial/incidents/${id}`);
  if (!r.ok) {
    $("evidence").innerHTML = `<span class="muted">Incident ${id} could not be loaded.</span>`;
    return;
  }
  const detail = await r.json();
  openId = id;
  openKey = `${detail.state}:${detail.actions.length}`;
  render(detail);
}

// Re-fetch the open incident when the run's snapshot shows its state or actions changed.
export function refreshOpenIncident(snapshot) {
  const inc = snapshot.incidents.find((i) => i.id === openId);
  if (!inc) return;
  const actions = snapshot.actions.filter((a) => a.incident_id === openId).length;
  if (`${inc.state}:${actions}` !== openKey) openIncident(openId);
}
