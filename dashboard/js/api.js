/**
 * api.js — fetch wrappers for Vesti-Care backend REST endpoints.
 * Shared by index.html (live monitor) and history.html.
 */

const API_BASE = window.location.protocol === "file:"
  ? "http://localhost:8000"
  : window.location.origin;

/**
 * Call a backend route and return its JSON body, or throw with the route and
 * status in the message.
 *
 * A route's `detail` is folded into the error: reporting the status code
 * alone would throw away the only part of the response an operator can act on.
 *
 * @param {string} path route path beginning with a slash
 * @param {RequestInit} [options]
 */
async function apiFetch(path, options = {}) {
  const method = options.method ?? "GET";
  const resp = await fetch(`${API_BASE}${path}`, options);
  if (!resp.ok) {
    let detail = "";
    try {
      const body = await resp.json();
      if (body && typeof body.detail === "string") detail = ` — ${body.detail}`;
    } catch (_) {
      // Error body was not JSON; the status code is all we have.
    }
    throw new Error(`${method} ${path} → HTTP ${resp.status}${detail}`);
  }
  return resp.json();
}

/**
 * Fetch recent/filtered alerts.
 * @param {object} opts
 * @param {number}  [opts.limit=20]
 * @param {number}  [opts.offset=0]
 * @param {number|null} [opts.patientId]
 * @param {boolean} [opts.includeAcknowledged=true]
 * @param {string|null} [opts.since]  ISO datetime string
 * @param {string|null} [opts.until]  ISO datetime string
 */
async function fetchRecentAlerts({
  limit = 20,
  offset = 0,
  patientId = null,
  includeAcknowledged = true,
  since = null,
  until = null,
} = {}) {
  const params = new URLSearchParams({
    limit,
    offset,
    include_acknowledged: includeAcknowledged,
  });
  if (patientId)  params.append("patient_id", patientId);
  if (since)      params.append("since", since);
  if (until)      params.append("until", until);

  const resp = await fetch(`${API_BASE}/api/alerts/recent?${params}`);
  if (!resp.ok) throw new Error(`GET /api/alerts/recent → HTTP ${resp.status}`);
  return resp.json();
}

/**
 * Fetch summary counts (total, unacknowledged, today).
 * @param {number|null} [patientId]
 */
async function fetchAlertStats(patientId = null) {
  const params = new URLSearchParams();
  if (patientId) params.append("patient_id", patientId);
  const resp = await fetch(`${API_BASE}/api/alerts/stats?${params}`);
  if (!resp.ok) throw new Error(`GET /api/alerts/stats → HTTP ${resp.status}`);
  return resp.json();
}

async function acknowledgeAlert(alertId) {
  const resp = await fetch(`${API_BASE}/api/alerts/acknowledge/${alertId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes: null }),
  });
  if (!resp.ok) throw new Error(`POST /api/alerts/acknowledge/${alertId} → HTTP ${resp.status}`);
  return resp.json();
}

/**
 * Submit feedback for an alert.
 * @param {number} alertId
 * @param {"valid"|"false"} label
 */
async function feedbackAlert(alertId, label) {
  const resp = await fetch(`${API_BASE}/api/alerts/feedback/${alertId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  if (!resp.ok) throw new Error(`POST /api/alerts/feedback/${alertId} → HTTP ${resp.status}`);
  return resp.json();
}

async function fetchDeviceHealth(patientId = null) {
  const params = new URLSearchParams();
  if (patientId) params.append("patient_id", patientId);
  const resp = await fetch(`${API_BASE}/api/sensor/health?${params}`);
  if (!resp.ok) throw new Error(`GET /api/sensor/health → HTTP ${resp.status}`);
  return resp.json();
}

function sseUrl() {
  return `${API_BASE}/api/stream`;
}
