// Evidence drawer for one incident: location, glasses frame with the SAM mask,
// IMU around the impact, decision and action log. Every block names where its
// data came from. Masks are decoded from the stored SAM output with Meta's own
// parser; nothing is drawn when SAM did not return one.

import { decodeMaskToRaster, parseImageStream, recordsOfKind } from "./vendor/meta-sam-parser/index.js";

const $ = (id) => document.getElementById(id);

let openId = null;
let openKey = "";
let latestRequest = 0;

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

async function* samEvents(text) {
  yield { type: "response.output_text.delta", item_id: "m", output_index: 0, content_index: 0, delta: text };
  yield { type: "response.output_text.done", item_id: "m", output_index: 0, content_index: 0, text };
  yield { type: "response.completed" };
}

async function drawMask(img, canvas, outputText) {
  await img.decode();
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const parsed = parseImageStream(samEvents(outputText));
  for await (const _ of parsed);
  const result = await parsed.finalResult;
  const ctx = canvas.getContext("2d");
  const paint = ctx.createImageData(canvas.width, canvas.height);
  let pixels = 0;
  for (const record of recordsOfKind(result.records, "mask")) {
    const raster = decodeMaskToRaster(record.mask);
    const { left, top } = record.bounds;
    for (let j = 0; j < record.mask.height; j++) {
      for (let i = 0; i < record.mask.width; i++) {
        if (!raster[j * record.mask.width + i]) continue;
        const p = ((top + j) * canvas.width + left + i) * 4;
        paint.data.set([224, 72, 62, 140], p);
        pixels++;
      }
    }
  }
  ctx.putImageData(paint, 0, 0);
  canvas.dataset.maskPixels = String(pixels);
}

function samSummary(analysis) {
  if (!analysis) return '<span class="muted">SAM: not started.</span>';
  const head = `SAM 3.1 · prompt "${analysis.prompt ?? "none"}" · <b id="evidence-sam-status">${analysis.status}</b>`;
  if (analysis.status === "Complete") return `${head} · ${analysis.boxes.length} object(s) · ${(analysis.duration_ms / 1000).toFixed(1)} s`;
  if (analysis.reason) return `${head} · ${analysis.reason}`;
  return head;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function agentHtml(detail) {
  const agent = detail.analyses.find((a) => a.kind === "agent");
  if (!agent) return '<span class="muted">Explanation starts after the SAM analysis.</span>';
  const head = `Muse Spark · <b id="evidence-agent-status">${agent.status}</b>`;
  if (agent.status === "Running") return `${head} · explaining…`;
  if (agent.status !== "Complete") return `${head} · ${escapeHtml(agent.reason ?? "")}`;
  const answer = JSON.parse(agent.output_text);
  const calls = agent.tool_log?.calls ?? [];
  const tokens = agent.tool_log?.usage?.total_tokens;
  const observations = answer.observations.map((o) =>
    `<li>${escapeHtml(o.text)} ${o.evidence_ids.map((e) => `<code class="chip">${escapeHtml(e)}</code>`).join(" ")}</li>`).join("");
  const uncertainties = (answer.uncertainties ?? []).map((u) => `<li>${escapeHtml(u)}</li>`).join("");
  const fixes = detail.recommendations.map((f) => `<li><b>${escapeHtml(f.title)}</b><br><span class="muted">${escapeHtml(f.detail)}</span></li>`).join("");
  return `${head} · ${calls.length} tool calls${tokens ? ` · ${tokens} tokens` : ""} · ${(agent.duration_ms / 1000).toFixed(1)} s
    <p id="evidence-summary">${escapeHtml(answer.summary)}</p>
    <ul id="evidence-observations">${observations}</ul>
    ${uncertainties ? `<div class="muted">Not shown by the evidence</div><ul id="evidence-uncertainties">${uncertainties}</ul>` : ""}
    <h3>Suggested changes</h3>
    <ul id="evidence-recommendations">${fixes || '<li class="muted">None suggested.</li>'}</ul>
    <div class="source">Generated by ${escapeHtml(agent.model)} from tool evidence; every cited id and fix was checked against what the tools returned and the curated library.</div>`;
}

function render(detail) {
  const loc = detail.location;
  const frame = detail.frame;
  const sam = detail.analyses.find((a) => a.kind === "sam");
  const frameHtml = frame.status === "Available"
    ? `<div class="frame"><img id="evidence-frame" src="${frame.url}" alt="Glasses frame at ${fmt(frame.source_ms)}">
       <canvas id="evidence-mask" aria-hidden="true"></canvas></div>
       <div class="source">${frame.label} · ${frame.scene} · frame at ${fmt(frame.source_ms)}, ${frame.alignment_error_ms} ms from the incident</div>
       <div id="evidence-sam" class="source">${samSummary(sam)}</div>`
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
    <h3>Explanation</h3>
    <div id="evidence-agent">${agentHtml(detail)}</div>
    <h3>Actions</h3>
    <ul id="evidence-actions">${actions}</ul>`;
  drawImu($("evidence-imu"), detail);
  if (sam?.status === "Complete" && $("evidence-frame")) drawMask($("evidence-frame"), $("evidence-mask"), sam.output_text);
}

// Only the most recent request may render: an older or slower response is dropped.
export async function openIncident(id) {
  const request = ++latestRequest;
  openId = id;
  let r, detail;
  try {
    r = await fetch(`/api/spatial/incidents/${id}`);
    detail = r.ok ? await r.json() : null;
  } catch {
    detail = null;
  }
  if (request !== latestRequest) return;
  if (!detail) {
    $("evidence").innerHTML = `<span class="muted" id="evidence-error">Incident ${id} could not be loaded.</span>`;
    return;
  }
  openKey = `${detail.state}:${detail.actions.length}:${detail.analyses.map((a) => a.status).join(",")}`;
  render(detail);
}

// Re-fetch the open incident when the run's snapshot shows its state or actions changed.
export function refreshOpenIncident(snapshot) {
  const inc = snapshot.incidents.find((i) => i.id === openId);
  if (!inc) return;
  const actions = snapshot.actions.filter((a) => a.incident_id === openId).length;
  if (`${inc.state}:${actions}:${inc.analysis_status ?? ""}` !== openKey) openIncident(openId);
}
