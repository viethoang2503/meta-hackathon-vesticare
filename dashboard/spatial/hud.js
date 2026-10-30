// Simulated glasses HUD for the wearer, drawn over the glasses camera panel.
// Priority: a fall waiting for the wearer ("Are you OK?" with its countdown),
// then a fresh zone advisory, otherwise nothing.

const ADVISORY_VISIBLE_MS = 6000;

const $ = (id) => document.getElementById(id);
let shownKey = "";

function utc(value) {
  return value ? new Date(value.endsWith("Z") ? value : `${value}Z`).getTime() : null;
}

export function updateHud(snapshot, offsetMs, onWearerOk) {
  const hud = $("hud");
  const waiting = snapshot?.status === "Running" && snapshot.incidents.find((i) => i.state === "awaiting_wearer");
  if (waiting) {
    const left = Math.max(0, Math.ceil((utc(waiting.wearer_deadline_at) - Date.now()) / 1000));
    const key = `ask:${waiting.id}`;
    if (shownKey !== key) {
      hud.className = "hud hud-ask";
      hud.innerHTML = `<div class="hud-title">Are you OK?</div>
        <div class="hud-line">A fall was detected. Calling your caregiver in <b id="hud-countdown"></b> s.</div>
        <button id="hud-ok" type="button">I'm OK</button>`;
      $("hud-ok").addEventListener("click", () => onWearerOk(waiting.id));
      shownKey = key;
    }
    $("hud-countdown").textContent = String(left);
    hud.hidden = false;
    return;
  }
  const advisory = snapshot?.advisories?.filter((a) => a.shown && offsetMs >= a.source_ms && offsetMs - a.source_ms < ADVISORY_VISIBLE_MS).pop();
  if (advisory) {
    const key = `tip:${advisory.action_id}:${advisory.sam_status}`;
    if (shownKey !== key) {
      const confirm = advisory.sam_status === "Complete" ? "Stairs confirmed in view"
        : advisory.sam_status ? `Camera check: ${advisory.sam_status}` : "Checking camera…";
      hud.className = "hud hud-tip";
      hud.innerHTML = `<div class="hud-title" id="hud-advisory">${advisory.text}</div>
        <div class="hud-line" id="hud-advisory-check">${confirm}</div>`;
      shownKey = key;
    }
    hud.hidden = false;
    return;
  }
  hud.hidden = true;
  shownKey = "";
}
