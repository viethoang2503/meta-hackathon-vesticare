// iPhone 1 in a live session (docs/spatial/MVP2.md, "Kênh tiếng nói"). It plays the lines the
// backend queues, waits a guard so its own voice is not recorded, and records fixed turns while
// the backend says to listen. It never cuts a turn to speak and never extends a deadline: the
// backend decides everything; this page only plays, records and reports.
import { recordUntil, speakLine, turnLength } from "./turn.js";

const params = new URLSearchParams(location.hash.slice(1));
const session = params.get("s");
let token = null;  // exchanged from the single-use code in the link; never kept in the address bar
const api = `/api/wearer/${session}`;
const POLL_MS = 300;
const GUARD_MS = 500;
const MIN_TURN_MS = 300;
const DEADLINE_MARGIN_MS = 250;
const CLOCK_ROUNDS = 5;
const CLOCK_RESYNC_MS = 60000;
const TTS_TIMEOUT_MS = 15000;
const FRAME_MS = 1000;
const FRAME_QUALITY = 0.7;
const MIMES = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"];

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let stream = null;
let mime = "";
let lastUtterance = 0;
let lastClockSync = -Infinity;
let backendUncertainty = 0;
let backendOffset = null;  // backend monotonic ms = phone performance.now() + backendOffset

const paired = (async () => {
  const code = params.get("p");
  if (!session || !code) return;
  history.replaceState(null, "", `${location.pathname}#s=${session}`);
  try {
    const res = await fetch(`${api}/pair`, { method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify({ code }) });
    if (res.ok) token = (await res.json()).token;
  } catch { /* shown when Start is pressed */ }
})();

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
  backendOffset = mapping.offset_ms;
  backendUncertainty = mapping.uncertainty_ms;
  lastClockSync = performance.now();
  log(`clock synced, ±${mapping.uncertainty_ms} ms`);
}

function speak(text) {
  return speakLine(text, {
    synth: speechSynthesis,
    makeUtterance: (t) => Object.assign(new SpeechSynthesisUtterance(t), { lang: "en-US" }),
    now: () => performance.now(), setTimer: setTimeout, clearTimer: clearTimeout, timeoutMs: TTS_TIMEOUT_MS,
  });
}

async function recordTurn(endAt) {
  const recorder = new MediaRecorder(new MediaStream(stream.getAudioTracks()), mime ? { mimeType: mime } : {});
  const parts = [];
  recorder.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
  const { start, end } = await recordUntil(recorder, endAt, { now: () => performance.now(), sleep });  // finalized file, never a timeslice fragment
  return { blob: new Blob(parts, { type: recorder.mimeType || mime }), start, end };
}

async function play(u) {
  show("Speaking", u.text);
  const spoken = await speak(u.text);
  if (!spoken) {
    log(`could not speak "${u.text}"`);  // not reported as played
    return;
  }
  if (spoken.timedOut) log(`speech did not end, cut off: "${u.text}"`);
  await sleep(GUARD_MS);
  await call("played", { body: { utterance_id: u.id, speech_start: spoken.start, speech_end: spoken.end,
                                 timed_out: spoken.timedOut } });
  log(`said "${u.text}"`);
}

async function sendFrames() {
  const canvas = document.createElement("canvas");
  canvas.width = 640;
  canvas.height = 480;
  const video = $("cam");
  for (;;) {
    const started = performance.now();
    try {
      if (video.readyState >= 2) {
        const captured = performance.now();
        canvas.getContext("2d").drawImage(video, 0, 0, 640, 480);
        const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", FRAME_QUALITY));
        await call("frame", { body: blob, type: "image/jpeg", query: `?capture=${captured}` });
      }
    } catch (e) {
      log(`frame not sent: ${e.message || e}`);
    }
    await sleep(Math.max(0, FRAME_MS - (performance.now() - started)));
  }
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
      const decidedAt = performance.now();
      const length = turnLength(listen, backendOffset, decidedAt, MIN_TURN_MS, 300, DEADLINE_MARGIN_MS + backendUncertainty);
      if (length > 0) {
        show(listen.mode === "help_only" ? "Listening for help" : "Listening", `${Math.ceil(length / 1000)} s turn`);
        const turn = await recordTurn(decidedAt + length);
        if (turn.end - turn.start < MIN_TURN_MS) {
          log("recorder started too late for a useful turn");
          continue;
        }
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
  await paired;
  if (!session || !token) {
    show("Link not usable", "Scan a new QR from the laptop: each pairing link works once and for ten minutes.", "bad");
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
  $("cam").srcObject = new MediaStream(stream.getVideoTracks());
  sendFrames();
  loop();
};
