// Laptop-side controls for the M7 voice drills (docs/spatial/MVP2.md, M7).
const TRIALS = [
  ["ok", "Say “I'm fine”"],
  ["help", "Say “I need help”"],
  ["ambiguous", "Say something unclear (“yes”, “I think so”)"],
  ["silence", "Stay silent"],
  ["loudspeaker", "TV or speaker talking, nobody answers"],
];
const $ = (id) => document.getElementById(id);
let session = null;

async function api(path, options = {}) {
  const res = await fetch(`/api/spatial/${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

function setButtons(enabled) {
  for (const b of $("buttons").children) b.disabled = !enabled;
}

for (const [expected, label] of TRIALS) {
  const button = document.createElement("button");
  button.textContent = label;
  button.disabled = true;
  button.onclick = async () => {
    try {
      await api(`sessions/${session}/drill`, { method: "POST", body: JSON.stringify({ expected, person: $("person").value.trim() }) });
    } catch (e) {
      $("now").textContent = e.message === "busy" ? "The last trial is still running; wait for it to finish." : e.message;
    }
  };
  $("buttons").append(button);
}

$("begin").onclick = async () => {
  try {
    const snap = await api("live", { method: "POST", body: JSON.stringify({
      patient_id: 1, device_id: "drill", source: "drill", notification_mode: "simulated",
      wearer_cancel_seconds: Number($("window").value) }) });
    session = snap.id;
    $("begin").disabled = true;
    $("end").disabled = false;
    setButtons(true);
  } catch (e) {
    $("now").textContent = e.message;
  }
};

$("end").onclick = async () => {
  await api(`sessions/${session}/stop`, { method: "POST" });
  setButtons(false);
  $("end").disabled = true;
};

function row(cells, tag = "td") {
  return `<tr>${cells.map((c) => `<${tag}>${c}</${tag}>`).join("")}</tr>`;
}

function renderReport(report) {
  const names = { ok: "OK", help: "HELP", ambiguous: "Unclear", silence_or_loudspeaker: "Silence / speaker" };
  let html = "";
  for (const [person, p] of Object.entries(report.people)) {
    const g = p.groups;
    const cell = (k) => (g[k] ? `${g[k].correct}/${g[k].n}${g[k].pending ? ` (+${g[k].pending} running)` : ""}` : "0/0");
    html += `<h2>${person} <span class="${p.status}">${p.status}</span></h2><table>` +
      row(["Group", "Handled correctly", "Cancelled", "Raised HELP wrongly"], "th") +
      Object.keys(names).map((k) => row([names[k], cell(k), g[k]?.cancelled ?? 0, g[k]?.false_help ?? 0])).join("") + "</table>" +
      `<p>False cancels: <b class="${p.false_cancel_count ? "Fail" : "Pass"}">${p.false_cancel_count}</b> (must be 0).` +
      p.false_cancels.map((f) => ` ${f.expected}: heard “${f.heard.join(" / ")}”`).join("") + "</p>";
  }
  $("report").innerHTML = html || "<p>No trials yet.</p>";
}

async function refresh() {
  if (!session) return;
  try {
    const snap = await api(`sessions/${session}`);
    const open = snap.incidents.filter((i) => i.decision_source === "voice_drill").at(-1);
    const state = snap.wearer?.status ?? "Unknown";
    const heard = snap.actions.filter((a) => a.kind === "self_report").at(-1)?.detail?.speech?.transcript;
    $("now").textContent = open
      ? `Trial ${open.id}: ${open.state}. Phone: ${state}.${heard ? ` Last heard: “${heard}”.` : ""}`
      : `Ready. Phone: ${state}.`;
    renderReport(await api(`sessions/${session}/drill-report`));
  } catch (e) {
    $("now").textContent = e.message;
  }
}
setInterval(refresh, 1000);
