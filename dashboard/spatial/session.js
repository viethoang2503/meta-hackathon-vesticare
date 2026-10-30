// One spatial run as the page sees it: Start/Stop, the latest snapshot, the
// timeline and the incident list. The session id lives in the URL hash so a
// refresh reopens the same run instead of starting another.

import { openIncident, refreshOpenIncident } from "./evidence.js";

const POLL_MS = 500;
const RETRY_MS = 1500;

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const r = await fetch(path, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.detail ?? `${r.status} ${path}`), { status: r.status });
  return body;
}

const STATE_LABEL = {
  awaiting_wearer: "Waiting for the wearer",
  cancelled_by_wearer: "Cancelled by the wearer",
  alerted: "Caregiver alerted",
};

function fmt(ms) {
  return `${(ms / 1000).toFixed(1)} s`;
}

export function createSession({ onSnapshot }) {
  let snapshot = null;
  let fetchedAt = 0;
  let timer = null;
  let connectionLost = false;

  const scenarioSelect = $("scenario"), start = $("start"), stop = $("stop"), status = $("run-status");

  api("/api/spatial/scenarios").then((list) => {
    for (const s of list) scenarioSelect.add(new Option(`${s.id} · ${s.title}`, s.id));
  });

  function render() {
    const running = snapshot?.status === "Running";
    start.disabled = running;
    stop.disabled = !running;
    scenarioSelect.disabled = running;
    if (!snapshot) {
      status.textContent = "No run selected.";
      $("timeline").hidden = true;
      $("incidents").innerHTML = '<span class="muted">No incidents.</span>';
      return;
    }
    const ended = snapshot.recording_ended ? " · Recording ended" : "";
    const reason = snapshot.end_reason ? ` (${snapshot.end_reason})` : "";
    const lost = connectionLost ? " · Connection lost, retrying…" : "";
    status.textContent = `${snapshot.scenario.id} · ${snapshot.status}${reason}${ended} · model ${snapshot.model_source}${lost}`;

    const duration = snapshot.scenario.duration_ms;
    $("timeline").hidden = false;
    $("timeline-fill").style.width = `${(100 * snapshot.source_offset_ms) / duration}%`;
    $("timeline-label").textContent = `${fmt(snapshot.source_offset_ms)} / ${fmt(duration)}`;
    const ticks = $("timeline-ticks");
    ticks.innerHTML = "";
    for (const inc of snapshot.incidents) {
      const tick = document.createElement("button");
      tick.className = "tick";
      tick.style.left = `${(100 * inc.source_start_ms) / duration}%`;
      tick.title = `Incident ${inc.id} at ${fmt(inc.source_start_ms)}`;
      tick.setAttribute("aria-label", tick.title);
      tick.addEventListener("click", () => openIncident(inc.id));
      ticks.appendChild(tick);
    }

    const list = $("incidents");
    list.innerHTML = snapshot.incidents.length ? "" : '<span class="muted">No incidents.</span>';
    for (const inc of snapshot.incidents) {
      const b = document.createElement("button");
      b.className = "incident";
      b.dataset.incidentId = inc.id;
      b.innerHTML = `<b>${inc.alert_type}</b> · ${inc.location.zone_label} · ${fmt(inc.source_start_ms)}<br>` +
        `<span class="state state-${inc.state}">${STATE_LABEL[inc.state] ?? inc.state}</span>`;
      b.addEventListener("click", () => openIncident(inc.id));
      list.appendChild(b);
    }
  }

  async function load(id) {
    clearTimeout(timer);
    try {
      snapshot = await api(`/api/spatial/sessions/${id}`);
      connectionLost = false;
    } catch (err) {
      if (err.status === 404) {
        snapshot = null;
        connectionLost = false;
        history.replaceState(null, "", location.pathname);
        render();
        onSnapshot(null);
        return;
      }
      // Anything else is the network or the server, not the run: keep the run and try again.
      connectionLost = true;
      if (snapshot) render();
      else status.textContent = "Connection lost, retrying…";
      timer = setTimeout(() => load(id), RETRY_MS);
      return;
    }
    fetchedAt = performance.now();
    render();
    onSnapshot(snapshot);
    if (snapshot) refreshOpenIncident(snapshot);
    if (snapshot?.status === "Running") timer = setTimeout(() => load(snapshot.id), POLL_MS);
  }

  start.addEventListener("click", async () => {
    try {
      const s = await api("/api/spatial/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario_id: scenarioSelect.value }),
      });
      history.replaceState(null, "", `#session=${s.id}`);
      load(s.id);
    } catch (err) {
      status.textContent = `Start failed: ${err.message}`;
    }
  });

  stop.addEventListener("click", async () => {
    if (!snapshot) return;
    try {
      await api(`/api/spatial/sessions/${snapshot.id}/stop`, { method: "POST" });
    } catch (err) {
      status.textContent = `Stop failed: ${err.message}`;
    }
    load(snapshot.id);
  });

  const fromHash = new URLSearchParams(location.hash.slice(1)).get("session");
  if (fromHash) load(fromHash);
  else render();

  return {
    // The wearer's route and the source offset to draw at `now`, interpolated between polls.
    current(now) {
      if (!snapshot) return null;
      const moving = snapshot.status === "Running" && !snapshot.recording_ended;
      const offset = moving
        ? Math.min(snapshot.scenario.duration_ms, snapshot.source_offset_ms + (now - fetchedAt))
        : snapshot.source_offset_ms;
      return { route: snapshot.scenario.route, offset_ms: offset };
    },
  };
}
