/**
 * realtime.js — wires EventSource, chart, and DOM together.
 *
 * Responsibilities:
 *   - Connect to GET /api/stream (SSE)
 *   - On each event: update state panel + push SVM point to chart
 *   - On alert_created: reload alerts table
 *   - Load/reload alerts table via REST
 *   - Handle patient filter
 *   - Reconnect automatically on SSE error
 */

// ─── State ───────────────────────────────────────────────────────────────────

let _es = null;
let _patientFilter = null;  // null = show all
let _reconnectTimer = null;
let _activeSource = "wifi";
let _serialBridge = null;
let liveState = null;

// ─── Startup ─────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  liveState = LiveState.create();
  initSvmChart("svm-chart");
  initImuCharts();
  setupPatientFilter();
  setupSourceControls();
  loadAlerts();
  loadMetrics();
  loadDeviceHealth();
  setInterval(loadDeviceHealth, 5000);
  // Freshness is a function of elapsed time, not of arriving messages, so the
  // snapshot is repainted on a timer. Without this the badge would keep showing
  // the last decision forever once the stream went quiet.
  setInterval(() => renderLiveSnapshot(liveState.snapshot(Date.now())), 500);
  connectSSE();
});

// ─── Patient filter ──────────────────────────────────────────────────────────

function setupPatientFilter() {
  const input = document.getElementById("patient-filter");
  input.addEventListener("change", () => {
    const val = parseInt(input.value, 10);
    _patientFilter = isNaN(val) || val < 1 ? null : val;
    loadAlerts();
    loadMetrics();
  });
}

// ─── SSE connection ──────────────────────────────────────────────────────────

function connectSSE() {
  if (_es) {
    _es.close();
    _es = null;
  }

  setConnectionStatus("connecting");
  _es = new EventSource(sseUrl());

  _es.onopen = () => {
    setConnectionStatus("connected");
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer);
      _reconnectTimer = null;
    }
  };

  _es.onmessage = (evt) => {
    try {
      const data = JSON.parse(evt.data);
      handleLiveEvent(data);
    } catch (_) {
      // malformed JSON — ignore
    }
  };

  _es.onerror = () => {
    setConnectionStatus("disconnected");
    _es.close();
    _es = null;
    // Reconnect after 3 s
    _reconnectTimer = setTimeout(connectSSE, 3000);
  };
}

// ─── Event handling ──────────────────────────────────────────────────────────

/**
 * True when an IMU event belongs on this screen.
 *
 * Two filters, both operator-controlled: the patient box and the Wi-Fi/USB
 * source selector.
 */
function _imuEventIsForThisView(data) {
  if (_patientFilter !== null && data.patient_id !== _patientFilter) return false;
  return SerialBridge.eventMatchesSource(data, _activeSource);
}

/**
 * One entry point for events on the stream.
 *
 * IMU events flow to the per-sample panels and into the pure model, which is
 * what decides what the badge says.
 */
function handleLiveEvent(data) {
  const eventType = data.event_type ?? (typeof data.state === "string" ? "imu_prediction" : null);
  if (eventType === "imu_prediction") {
    // Returns rather than only skipping the panels: an event for a patient who
    // is not on screen must not reach the decision model either, or the badge
    // would report a state belonging to someone else.
    if (!_imuEventIsForThisView(data)) return;
    handleStateEvent(data);
  }
  liveState.acceptEvent(data, Date.now());
  renderLiveSnapshot(liveState.snapshot(Date.now()));
}

function handleStateEvent(data) {
  // Reached only for events that already passed `_imuEventIsForThisView`, so it
  // does not re-filter. Its sole caller is handleLiveEvent above.
  updateStatePanel(data);
  const samples = SerialBridge.normalizeEventSamples(data);
  updateSensorReadouts(samples);
  pushImuSamples(samples);
  if (samples.length > 0) {
    samples.forEach((sample) => pushSvmPoint(sample.svm));
  } else if (Number.isFinite(data.svm)) {
    pushSvmPoint(data.svm);
  }
  setChartStateColor(data.state);

  if (data.alert_created) {
    loadAlerts();
    loadMetrics();
  }
}

function updateSensorReadouts(samples) {
  if (samples.length === 0) return;
  const latest = samples[samples.length - 1];
  ["ax", "ay", "az", "gx", "gy", "gz"].forEach((axis) => {
    const el = document.getElementById(`sensor-${axis}`);
    el.textContent = Number.isFinite(latest[axis]) ? latest[axis].toFixed(4) : "—";
  });
}

function setupSourceControls() {
  const select = document.getElementById("source-select");
  const button = document.getElementById("usb-connect");
  const tokenField = document.getElementById("token-field");

  _serialBridge = SerialBridge.create({
    apiBase: API_BASE,
    onStatus: renderUsbStatus,
  });

  if (!_serialBridge.isSupported()) {
    select.querySelector('option[value="usb"]').disabled = true;
    renderUsbStatus({ state: "unsupported" });
  }

  select.addEventListener("change", async () => {
    _activeSource = select.value;
    const usbSelected = _activeSource === "usb";
    button.disabled = !usbSelected || !_serialBridge.isSupported();
    tokenField.hidden = !usbSelected;
    document.getElementById("imu-source").textContent =
      `Source: ${usbSelected ? "USB Serial" : "Wi-Fi"}`;

    if (!usbSelected) {
      await _serialBridge.disconnect({ resumeWifi: true });
      button.textContent = "Connect USB";
      renderUsbStatus({ state: "wifi" });
    } else if (!_serialBridge.isConnected()) {
      renderUsbStatus({ state: "ready" });
    }
  });

  button.addEventListener("click", async () => {
    if (_serialBridge.isConnected()) {
      await _serialBridge.disconnect({ resumeWifi: true });
      select.value = "wifi";
      select.dispatchEvent(new Event("change"));
      return;
    }

    const patientId = parseInt(document.getElementById("patient-filter").value, 10);
    if (!Number.isInteger(patientId) || patientId < 1) {
      renderUsbStatus({ state: "input-error", message: "Enter a Patient ID first" });
      return;
    }

    try {
      await _serialBridge.connect({
        patientId,
        token: document.getElementById("device-token").value,
      });
      button.textContent = "Disconnect USB";
    } catch (error) {
      renderUsbStatus({ state: "connect-error", message: error.message });
      select.value = "wifi";
      _activeSource = "wifi";
      tokenField.hidden = true;
      button.disabled = true;
    }
  });

  window.addEventListener("beforeunload", () => {
    void _serialBridge.disconnect({ resumeWifi: true });
  });
}

function renderUsbStatus(status) {
  const el = document.getElementById("usb-status");
  const labels = {
    wifi: "Wi-Fi selected",
    ready: "Ready to connect",
    connecting: "Selecting serial port…",
    connected: `USB connected${status.queuedBatches ? ` · queued ${status.queuedBatches}` : ""}`,
    disconnected: "USB disconnected",
    unsupported: "Web Serial requires Chrome or Edge",
    "auth-error": "Device token rejected",
    "backend-error": "Backend unavailable; retrying",
    "batch-dropped": "Backend unavailable; batch dropped",
    "read-error": "USB read failed",
    "write-error": "USB command failed",
    "input-error": status.message,
    "connect-error": status.message,
  };
  el.textContent = labels[status.state] ?? status.message ?? status.state;
  el.className = `source-status source-${status.state}`;
}

// ─── Decision tier ───────────────────────────────────────────────────────────

function _setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function _decisionClass(decision) {
  return `decision-${decision.toLowerCase().replace(/\s+/g, "-")}`;
}

function _renderProbability(id, value, known) {
  const bar = document.getElementById(id);
  if (bar) bar.value = known ? value : 0;
  // A stale stream has no current probabilities. Painting the last known
  // numbers, or zeros, would both read as a fresh measurement.
  _setText(`${id}-value`, known ? `${(value * 100).toFixed(1)}%` : "—");
}

function _renderFreshness(id, label, source, fresh) {
  const el = document.getElementById(id);
  if (!el) return;
  let state = "stale";
  let text = `${label}: no data`;
  if (source && fresh) {
    state = "live";
    text = `${label}: live`;
  } else if (source) {
    text = `${label}: stale >2 s`;
  }
  el.textContent = text;
  el.className = `freshness freshness-${state}`;
}

/**
 * Built as DOM nodes rather than an HTML string.
 *
 * Labels come from a fixed map today, but writing them with `textContent` keeps
 * the list safe if a future event type passes backend text through.
 */
function _renderTimeline(events) {
  const list = document.getElementById("event-timeline");
  if (!list) return;
  list.replaceChildren();

  if (events.length === 0) {
    const empty = document.createElement("li");
    empty.className = "placeholder";
    empty.textContent = "No events yet.";
    list.append(empty);
    return;
  }

  for (const event of events.slice().reverse()) {
    const row = document.createElement("li");
    const time = document.createElement("span");
    time.className = "timeline-time";
    time.textContent = new Date(event.atMs).toLocaleTimeString();
    // A string passed to append() becomes a text node, never parsed as markup.
    row.append(time, ` ${event.label}`);
    list.append(row);
  }
}

/**
 * Paint everything the pure model decides. Called on each event and on a timer,
 * so it must be idempotent for a given snapshot.
 */
function renderLiveSnapshot(snapshot) {
  const badge = document.getElementById("decision-badge");
  if (badge) {
    badge.textContent = snapshot.decision;
    badge.className = `decision-badge ${_decisionClass(snapshot.decision)}`;
  }

  const known = snapshot.imuFresh && snapshot.imu !== null;
  _renderProbability("prob-normal", snapshot.probabilities.normal, known);
  _renderProbability("prob-instability", snapshot.probabilities.instability, known);
  _renderProbability("prob-fall", snapshot.probabilities.fall, known);

  _setText("evidence-line", snapshot.evidenceText);
  _renderFreshness("imu-freshness", "IMU", snapshot.imu, snapshot.imuFresh);
  _renderTimeline(snapshot.timeline);
}

// ─── State panel ─────────────────────────────────────────────────────────────

function updateStatePanel(data) {
  document.getElementById("metric-svm").textContent =
    data.svm != null ? `${data.svm.toFixed(3)} g` : "—";

  document.getElementById("metric-peak-svm").textContent =
    data.window_ready ? `${data.peak_svm.toFixed(3)} g` : "—";

  document.getElementById("metric-conf").textContent =
    data.window_ready ? `${(data.fall_probability * 100).toFixed(1)}%` : "—";

  document.getElementById("metric-patient").textContent =
    data.patient_id ?? "—";

  document.getElementById("metric-buffer").textContent =
    `${data.samples_buffered} / 100`;
}

// ─── Connection status bar ───────────────────────────────────────────────────

function setConnectionStatus(status) {
  const el = document.getElementById("conn-status");
  const labels = {
    connected: "Live",
    connecting: "Connecting…",
    disconnected: "Disconnected",
  };
  el.textContent = labels[status] ?? status;
  el.className = `conn-badge conn-${status}`;
}

// ─── Device health ───────────────────────────────────────────────────────────

async function loadDeviceHealth() {
  try {
    const h = await fetchDeviceHealth(_patientFilter);
    const el = document.getElementById("dh-status");
    if (el) {
      el.textContent = h.online ? "Online" : "Offline";
      el.className = `dh-badge ${h.online ? "dh-online" : "dh-offline"}`;
    }
    const lastSeen = document.getElementById("dh-last-seen");
    if (lastSeen) lastSeen.textContent = h.last_seen_s != null ? `${h.last_seen_s}s ago` : "—";
    const src = document.getElementById("dh-source");
    if (src) src.textContent = h.source_type ?? "—";
    const rate = document.getElementById("dh-rate");
    if (rate) rate.textContent = h.effective_sample_rate != null ? `${h.effective_sample_rate} Hz` : "—";
  } catch (_) {
    // non-critical — backend may not be running yet
  }
}

// ─── System metrics ──────────────────────────────────────────────────────────

async function loadMetrics() {
  try {
    const stats = await fetchAlertStats(_patientFilter);
    document.getElementById("stat-total").textContent   = stats.total_alerts;
    document.getElementById("stat-unacked").textContent = stats.unacknowledged;
    document.getElementById("stat-today").textContent   = stats.alerts_today;
    document.getElementById("stat-latency").textContent =
      stats.avg_latency_ms != null ? `${stats.avg_latency_ms} ms` : "—";

    document.getElementById("stat-false").textContent   = stats.false_alert_count ?? "—";
  } catch (_) {
    // non-critical — leave existing values
  }
}

// ─── Alerts table ────────────────────────────────────────────────────────────

async function loadAlerts() {
  const container = document.getElementById("alerts-container");
  try {
    const data = await fetchRecentAlerts({ limit: 20, patientId: _patientFilter });
    renderAlertsTable(container, data.alerts);
  } catch (err) {
    container.innerHTML = `<p class="error-msg">Failed to load alerts: ${err.message}</p>`;
  }
}

function renderAlertsTable(container, alerts) {
  if (!alerts || alerts.length === 0) {
    container.innerHTML = `<p class="placeholder">No alerts yet.</p>`;
    return;
  }

  const rows = alerts.map((a) => {
    const ts = new Date(a.timestamp).toLocaleString();
    const notifBadge = _notifBadge(a.notification_status);
    const ackBadge = a.is_acknowledged
      ? `<span class="badge badge-ack">Ack</span>`
      : `<button class="btn-ack" onclick="ackAlert(${a.id})">Acknowledge</button>`;

    return `
      <tr class="${a.is_acknowledged ? "row-acked" : "row-pending"}">
        <td>${a.id}</td>
        <td>${a.patient_id}</td>
        <td>${ts}</td>
        <td>${(a.confidence * 100).toFixed(1)}%</td>
        <td>${a.peak_svm.toFixed(3)} g</td>
        <td><div class="lifecycle-cell">${notifBadge}${ackBadge}</div></td>
      </tr>`;
  }).join("");

  container.innerHTML = `
    <table class="alerts-table">
      <thead>
        <tr>
          <th>ID</th><th>Patient</th><th>Time</th>
          <th>Confidence</th><th>Peak SVM</th><th>Lifecycle</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function _notifBadge(status) {
  if (!status || status === "pending")
    return `<span class="badge badge-notif-pending">Pending</span>`;
  if (status === "sent")
    return `<span class="badge badge-notified">Notified</span>`;
  if (status === "failed")
    return `<span class="badge badge-notif-failed">Failed</span>`;
  if (status === "skipped")
    return `<span class="badge badge-notif-skipped">Skipped</span>`;
  return `<span class="badge badge-notif-skipped">${status}</span>`;
}

async function ackAlert(alertId) {
  try {
    await acknowledgeAlert(alertId);
    loadAlerts();
    loadMetrics();
  } catch (err) {
    alert(`Acknowledge failed: ${err.message}`);
  }
}
