// iPhone 1 in a live session (docs/spatial/MVP2.md, "Kênh tiếng nói"). It plays the lines the
// backend queues, waits a guard so its own voice is not recorded, and records fixed turns while
// the backend says to listen. It never cuts a turn to speak and never extends a deadline: the
// backend decides everything; this page only plays, records and reports.
const params = new URLSearchParams(location.hash.slice(1));
const session = params.get("s");
const token = params.get("t");
const api = `/api/wearer/${session}`;
const POLL_MS = 300;
const GUARD_MS = 500;
const MIN_TURN_MS = 300;
const CLOCK_ROUNDS = 5;
const CLOCK_RESYNC_MS = 60000;
const TTS_TIMEOUT_MS = 15000;
const MIMES = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"];

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let stream = null;
let mime = "";
let lastUtterance = 0;
let lastClockSync = -Infinity;

function show(state, detail = "", cls = "") {
  $("state").textContent = state;
  $("state").className = cls;
  $("detail").textContent = detail;
}

function log(text) {
  const li = document.createElement("li");
  li.textContent = `${new Date().toLocaleTimeString()} ${text}`;
  $("log").prepend(li);
  while ($("log").children.length > 12) $("log").lastChild.remove();
}

async function call(path, { method = "POST", body, type = "application/json", query = "" } = {}) {
  const res = await fetch(`${api}/${path}${query}`, {
    method, body: type === "application/json" && body !== undefined ? JSON.stringify(body) : body,
    headers: { "X-Wearer-Token": token, ...(body !== undefined ? { "Content-Type": type } : {}) },
  });
  if (!res.ok) throw new Error(`${path} HTTP ${res.status}`);
  return res.json();
}

async function syncClock() {
  const rounds = [];
  for (let i = 0; i < CLOCK_ROUNDS; i++) {
    const t0 = performance.now();
    const reply = await call("clock");
    rounds.push({ t0, ...reply, t3: performance.now() });
  }
  const mapping = await call("clock/report", { body: { rounds } });
  lastClockSync = performance.now();
  log(`clock synced, ±${mapping.uncertainty_ms} ms`);
}

function speak(text) {
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US";
    const timer = setTimeout(() => resolve(false), TTS_TIMEOUT_MS);
    u.onend = () => { clearTimeout(timer); resolve(true); };
    u.onerror = () => { clearTimeout(timer); resolve(false); };
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  });
}

async function recordTurn(ms) {
  const recorder = new MediaRecorder(new MediaStream(stream.getAudioTracks()), mime ? { mimeType: mime } : {});
  const parts = [];
  recorder.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
  const stopped = new Promise((r) => { recorder.onstop = r; });
  recorder.start();
  const start = performance.now();
  await sleep(ms);
  recorder.stop();
  await stopped;  // finalized file, never a timeslice fragment
  return { blob: new Blob(parts, { type: recorder.mimeType || mime }), start, end: performance.now() };
}

async function play(u) {
  show("Speaking", u.text);
  const ok = await speak(u.text);
  if (!ok) {
    log(`could not speak "${u.text}"`);  // not reported as played
    return;
  }
  await sleep(GUARD_MS);
  await call("played", { body: { utterance_id: u.id } });
  log(`said "${u.text}"`);
}

async function loop() {
  for (;;) {
    try {
      if (performance.now() - lastClockSync > CLOCK_RESYNC_MS) await syncClock();
      const state = await call(`poll`, { method: "GET", query: `?after=${lastUtterance}` });
      if (state.utterances.length) {
        const u = state.utterances[0];
        lastUtterance = u.id;
        await play(u);
        continue;
      }
      const listen = state.listen;
      if (listen && listen.remaining_ms >= MIN_TURN_MS) {
        show(listen.mode === "help_only" ? "Listening for help" : "Listening", `${Math.ceil(listen.remaining_ms / 1000)} s left`);
        const turn = await recordTurn(Math.min(listen.turn_ms, listen.remaining_ms));
        const reply = await call("audio", {
          body: turn.blob, type: turn.blob.type || "application/octet-stream",
          query: `?incident_id=${listen.incident_id}&capture_start=${turn.start}&capture_end=${turn.end}`,
        });
        if (reply.capture_time !== "confirmed") log(`turn time not confirmed: ${reply.capture_time}`);
        continue;
      }
      show("Ready", "Waiting. The backend will speak if a fall is detected.", "good");
    } catch (e) {
      show("Connection problem", String(e.message || e), "bad");
      await sleep(1000);
    }
    await sleep(POLL_MS);
  }
}

$("start").onclick = async () => {
  if (!session || !token) {
    show("Missing link", "Open the link from the laptop: it ends with #s=…&t=…", "bad");
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: { facingMode: "environment", width: 640, height: 480 },
    });
    await call("status", { body: { microphone_error: null } });
  } catch (e) {
    show("Microphone or camera refused", e.name, "bad");
    call("status", { body: { microphone_error: e.name || "refused" } }).catch(() => {});
    return;
  }
  mime = MIMES.find((m) => window.MediaRecorder?.isTypeSupported?.(m)) ?? "";
  speechSynthesis.speak(new SpeechSynthesisUtterance(""));  // iOS speaks only after a tap
  try { await navigator.wakeLock.request("screen"); } catch { log("screen may lock: keep it on"); }
  $("start").disabled = true;
  log(`recorder ${mime || "default"}`);
  loop();
};
