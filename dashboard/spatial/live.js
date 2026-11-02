// Laptop view of a live session (docs/spatial/MVP2.md, M6 and M9): what the wearer's phone says and
// hears, the state of the incident and its sends, and the evidence with the agent's cited explanation.
// Every part carries the label of what produced it. Nothing here decides anything.
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const utc = (iso) => new Date(`${iso}Z`);
const clock = (iso) => (iso ? utc(iso).toLocaleTimeString() : "");
let session = null;
let detail = { id: null, at: 0, body: null };

async function api(path, options = {}) {
  const res = await fetch(`/api/spatial/${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

function chip(label, value, good) {
  return `<span class="chip">${esc(label)}: <b class="${good === undefined ? "" : good ? "good" : "bad"}">${esc(value)}</b></span>`;
}

function renderReady(snap) {
  const live = snap.live, wearer = snap.wearer, cam = snap.camera;
  const synced = live.clock && live.clock.offset_ms !== undefined;
  $("ready").innerHTML = [
    chip("Device", live.status_label, live.status_label === "Detector ready"),
    chip("Device clock", synced ? `synced ±${live.clock.uncertainty_ms} ms` : "Unsynchronized", synced),
    chip("Phone", wearer?.status ?? "Unknown", wearer?.status === "Ready"),
    chip("Camera", cam?.frames_received ? `${cam.frames_received} frames, ${cam.clock}` : "no frames", cam?.clock === "synchronized"),
    chip("Alerts", live.config.notification_mode, live.config.notification_mode === "telegram"),
  ].join("");
  $("source").textContent = `Source: ${live.config.source_label}. Alerts: ${live.config.notification_mode}. Detector: ${snap.model_source}.`;
}

function renderWearer(snap) {
  const w = snap.wearer;
  const said = (w?.spoken ?? []).slice().reverse().map((u) =>
    `<li>${esc(u.text)}<span class="tag">${esc(u.source)}</span><span class="tag">${esc(u.status)}${u.reason ? `: ${esc(u.reason)}` : ""}</span></li>`);
  $("said").innerHTML = said.join("") || "<li>Nothing yet</li>";
  const heard = snap.actions.filter((a) => a.kind === "self_report").slice().reverse().map((a) => {
    const d = a.detail, speech = d.speech ?? {};
    const note = d.applied ? "" : ` (no effect: ${esc(d.reason ?? speech.not_counted_as_ok ?? "")})`;
    return `<li>${clock(a.created_at)} “${esc(speech.transcript ?? "")}”<span class="tag">${esc(d.response)}</span>${note}</li>`;
  });
  $("heard").innerHTML = heard.join("") || "<li>Nothing yet</li>";
}

function renderIncident(snap) {
  const i = snap.incidents.at(-1);
  if (!i) {
    $("incident").textContent = "No incident";
    $("actions").innerHTML = "";
    return null;
  }
  const left = i.state === "awaiting_wearer" && i.wearer_deadline_at ? Math.max(0, Math.round((utc(i.wearer_deadline_at) - Date.now()) / 1000)) : null;
  $("incident").innerHTML = `<div class="state">#${i.id} ${esc(i.state)}</div>` +
    `<p>${esc(i.decision_source)}, peak ${i.peak_svm.toFixed(1)} g${left === null ? "" : `. Wearer window: ${left} s left`}. ` +
    `${i.is_acknowledged ? "Acknowledged." : "Not acknowledged."}</p>`;
  const rows = snap.actions.filter((a) => a.incident_id === i.id && !["self_report", "drill", "spoken"].includes(a.kind)).map((a) => {
    const d = a.detail ?? {};
    return `<li>${clock(a.created_at)} <b>${esc(a.kind)}</b> ${esc(a.target ?? "")}<span class="tag">${esc(d.status ?? "")}</span>` +
      `${d.text ? `<blockquote>${esc(d.text)}</blockquote>` : ""}</li>`;
  });
  $("actions").innerHTML = rows.join("");
  return i.id;
}

function renderEvidence(d) {
  if (!d) return;
  const f = d.frame;
  const frame = f.status === "Available"
    ? `<img class="frame" src="${esc(f.url)}" alt="Phone camera frame"><p>${esc(f.label)} · ${esc(f.scene)} · ${esc(f.alignment_error_ms)} ms from the fall. ${esc(f.source)}</p>`
    : `<p class="bad">Camera ${esc(f.status)}: ${esc(f.reason ?? "")}</p>`;
  const agent = d.analyses.filter((a) => a.kind === "agent").at(-1);
  let explanation = "<p>No agent explanation yet.</p>";
  if (agent) {
    const out = agent.output_text ? JSON.parse(agent.output_text) : null;
    const refused = (agent.tool_log?.calls ?? []).filter((c) => c.refused);
    explanation = `<p>Agent (${esc(agent.model)}): <b class="${agent.status === "Complete" ? "good" : ""}">${esc(agent.status)}</b>${agent.reason ? ` · ${esc(agent.reason)}` : ""}</p>` +
      (out ? `<blockquote>${esc(out.summary)}</blockquote><ul class="feed">` +
        out.observations.map((o) => `<li>${esc(o.text)} <span class="tag">${o.evidence_ids.map(esc).join(", ")}</span></li>`).join("") +
        `</ul><p>Cannot be known from this evidence: ${(out.uncertainties ?? []).map(esc).join(" ")}</p>` : "") +
      (refused.length ? `<p>Refused requests: ${refused.map((c) => `${esc(c.tool)} (${esc(c.refused)})`).join("; ")}</p>` : "");
  }
  $("evidence").innerHTML = `<div class="cols"><div>${frame}</div><div>${explanation}</div></div>`;
}

async function refresh() {
  if (!session) return;
  try {
    const snap = await api(`sessions/${session}`);
    renderReady(snap);
    renderWearer(snap);
    const incident = renderIncident(snap);
    if (incident && (detail.id !== incident || Date.now() - detail.at > 3000)) {
      detail = { id: incident, at: Date.now(), body: await api(`incidents/${incident}`) };
    }
    renderEvidence(incident ? detail.body : null);
    $("pair").disabled = snap.status !== "Running";
    $("end").disabled = snap.status !== "Running";
    $("begin").disabled = snap.status === "Running";
  } catch (e) {
    $("error").textContent = e.message;
  }
}

$("begin").onclick = async () => {
  $("error").textContent = "";
  try {
    const snap = await api("live", { method: "POST", body: JSON.stringify({
      patient_id: 1, device_id: $("device").value.trim(), source: $("src").value, notification_mode: $("mode").value,
      record_frames: $("record").checked }) });
    session = snap.id;
    detail = { id: null, at: 0, body: null };
  } catch (e) {
    $("error").textContent = e.message;
  }
};

$("end").onclick = async () => {
  await api(`sessions/${session}/stop`, { method: "POST" });
};

$("pair").onclick = async () => {
  try {
    const { code, expires_at } = await api(`sessions/${session}/pairing`, { method: "POST" });
    const tunnel = $("tunnel").value.trim().replace(/\/$/, "");
    $("link").textContent = tunnel
      ? `${tunnel}/glasses/index.html#s=${session}&p=${code}`
      : `Enter the tunnel URL above, then press again. (Code made, valid until ${clock(expires_at)}.)`;
  } catch (e) {
    $("error").textContent = e.message;
  }
};

// A refresh must not lose a running session: take it up again before the first poll.
api("live/active").then((snap) => { session = snap.id; }).catch(() => {}).finally(() => setInterval(refresh, 1000));
