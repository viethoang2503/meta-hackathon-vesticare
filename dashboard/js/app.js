/**
 * app.js — History page logic (history.html).
 *
 * Responsibilities:
 *  - Read filter inputs (patient, from date, to date, unacknowledged only)
 *  - Fetch filtered alerts from GET /api/alerts/recent
 *  - Render a table where each row can be expanded for full event details
 *  - Acknowledge alerts inline
 */

let _expandedId = null;  // currently expanded alert id

// ─── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Set "until" default to now so the initial load shows recent alerts
  const untilInput = document.getElementById("filter-until");
  if (untilInput) untilInput.value = _localDatetimeNow();

  applyFilters();
});

// ─── Filter form ──────────────────────────────────────────────────────────────

async function applyFilters() {
  const patientId    = _numVal("filter-patient");
  const since        = _isoVal("filter-since");
  const until        = _isoVal("filter-until");
  const unackedOnly  = document.getElementById("filter-unacked").checked;

  const container = document.getElementById("history-container");
  const summary   = document.getElementById("history-summary");
  container.innerHTML = `<p class="placeholder">Loading…</p>`;
  summary.textContent = "";

  try {
    const data = await fetchRecentAlerts({
      limit: 500,
      patientId,
      since,
      until,
      includeAcknowledged: !unackedOnly,
    });

    summary.textContent = `${data.total} alert${data.total !== 1 ? "s" : ""} found`;
    renderHistoryTable(container, data.alerts);
  } catch (err) {
    container.innerHTML = `<p class="error-msg">Failed to load: ${err.message}</p>`;
  }
}

// ─── Table rendering ──────────────────────────────────────────────────────────

function renderHistoryTable(container, alerts) {
  if (!alerts || alerts.length === 0) {
    container.innerHTML = `<p class="placeholder">No alerts match the current filters.</p>`;
    return;
  }

  const rows = alerts.map((a) => _buildRow(a)).join("");

  container.innerHTML = `
    <table class="alerts-table history-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Patient</th>
          <th>Timestamp</th>
          <th>Type</th>
          <th>Confidence</th>
          <th>Peak SVM</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody id="history-tbody">${rows}</tbody>
    </table>`;
}

function _buildRow(a) {
  const ts     = new Date(a.timestamp).toLocaleString();
  const status = a.is_acknowledged
    ? `<span class="badge badge-ack">Ack</span>`
    : `<span class="badge badge-pending">Pending</span>`;

  return `
    <tr class="${a.is_acknowledged ? "row-acked" : "row-pending"} history-row"
        onclick="toggleDetail(${a.id})"
        style="cursor:pointer"
        data-id="${a.id}">
      <td>${a.id}</td>
      <td>${a.patient_id}</td>
      <td>${ts}</td>
      <td>${a.alert_type}</td>
      <td>${(a.confidence * 100).toFixed(1)}%</td>
      <td>${a.peak_svm.toFixed(3)} g</td>
      <td>${status}</td>
    </tr>
    <tr id="detail-${a.id}" class="detail-row" style="display:none">
      <td colspan="7">${_buildDetail(a)}</td>
    </tr>`;
}

function _buildDetail(a) {
  const ackTs = a.acknowledged_at
    ? new Date(a.acknowledged_at).toLocaleString()
    : "—";

  const ackBtn = a.is_acknowledged
    ? ""
    : `<button class="btn-ack" onclick="event.stopPropagation(); ackAndRefresh(${a.id})">
         Mark as Acknowledged
       </button>`;

  let feedbackHtml;
  if (a.feedback === "valid") {
    feedbackHtml = `<span class="badge badge-feedback-valid">Valid Event</span>`;
  } else if (a.feedback === "false") {
    feedbackHtml = `<span class="badge badge-feedback-false">False Alert</span>`;
  } else {
    feedbackHtml = `
      <button class="btn-feedback btn-feedback-valid"
              onclick="event.stopPropagation(); markFeedback(${a.id}, 'valid')">Valid</button>
      <button class="btn-feedback btn-feedback-false"
              onclick="event.stopPropagation(); markFeedback(${a.id}, 'false')">False Alert</button>`;
  }

  return `
    <div class="detail-panel">
      <div class="detail-grid">
        <div class="detail-item">
          <span class="detail-label">Alert ID</span>
          <span class="detail-val">${a.id}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Patient ID</span>
          <span class="detail-val">${a.patient_id}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Timestamp (UTC)</span>
          <span class="detail-val">${a.timestamp}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Alert type</span>
          <span class="detail-val">${a.alert_type}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Fall confidence</span>
          <span class="detail-val">${(a.confidence * 100).toFixed(2)}%</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Peak SVM</span>
          <span class="detail-val">${a.peak_svm.toFixed(4)} g</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Acknowledged at</span>
          <span class="detail-val">${ackTs}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Notes</span>
          <span class="detail-val">${a.notes ?? "—"}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">Alert latency</span>
          <span class="detail-val">${a.latency_ms != null ? `${a.latency_ms.toFixed(1)} ms` : "—"}</span>
        </div>
      </div>
      <div class="detail-actions">
        ${ackBtn}
        <div class="feedback-group">
          <span class="detail-label" style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted)">Feedback</span>
          <div>${feedbackHtml}</div>
        </div>
      </div>
    </div>`;
}

// ─── Row expand / collapse ────────────────────────────────────────────────────

function toggleDetail(alertId) {
  // Collapse the previously expanded row (if different)
  if (_expandedId !== null && _expandedId !== alertId) {
    const prev = document.getElementById(`detail-${_expandedId}`);
    if (prev) prev.style.display = "none";
  }

  const row = document.getElementById(`detail-${alertId}`);
  if (!row) return;

  if (row.style.display === "none") {
    row.style.display = "table-row";
    _expandedId = alertId;
  } else {
    row.style.display = "none";
    _expandedId = null;
  }
}

// ─── Acknowledge ──────────────────────────────────────────────────────────────

async function ackAndRefresh(alertId) {
  try {
    await acknowledgeAlert(alertId);
    applyFilters();
  } catch (err) {
    alert(`Acknowledge failed: ${err.message}`);
  }
}

// ─── Feedback ─────────────────────────────────────────────────────────────────

async function markFeedback(alertId, label) {
  try {
    await feedbackAlert(alertId, label);
    applyFilters();
  } catch (err) {
    alert(`Feedback failed: ${err.message}`);
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function _numVal(id) {
  const val = parseInt(document.getElementById(id)?.value, 10);
  return isNaN(val) || val < 1 ? null : val;
}

function _isoVal(id) {
  const val = document.getElementById(id)?.value;
  if (!val) return null;
  // datetime-local gives "YYYY-MM-DDTHH:MM" in whatever the user typed.
  // Inputs are labelled "(UTC)" so the user enters UTC directly.
  // Send as a naive string (no Z) — the backend treats naive datetimes as UTC,
  // consistent with how timestamps are stored in SQLite.
  return val.length === 16 ? `${val}:00` : val;
}

function _localDatetimeNow() {
  // Returns "YYYY-MM-DDTHH:MM" in UTC for the datetime-local input default.
  // Must use UTC here so the default matches the UTC timestamps in the database.
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-${pad(now.getUTCDate())}` +
         `T${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}`;
}
