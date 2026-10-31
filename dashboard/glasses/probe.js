// U0 gate (docs/spatial/MVP2_PLAN.md): play a line, wait the guard, record one
// finalized 4 s turn, upload it with a frame, and log what the backend decoded.
const token = new URLSearchParams(location.hash.slice(1)).get("t");
const GUARD_MS = 500;
const TURN_MS = 4000;
const TTS_TIMEOUT_MS = 10000;
// No OK or HELP phrase, so an echo of it cannot look like an answer.
const PROMPT = "Sound check. Please speak after this sentence.";
const MIMES = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/aac"];

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let stream = null;
let mime = "";
let wakeLock = "unsupported";

function status(text, cls = "") {
  $("status").textContent = text;
  $("status").className = cls;
}

function logRow(html) {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = html;
  $("log").prepend(row);
}

function speak(text) {
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US";
    const out = { start: null, end: null, error: null };
    const done = () => { clearTimeout(timer); resolve(out); };
    const timer = setTimeout(() => { out.error = "no end event"; done(); }, TTS_TIMEOUT_MS);
    u.onstart = () => { out.start = performance.now(); };
    u.onend = () => { out.end = performance.now(); done(); };
    u.onerror = (e) => { out.error = e.error || "error"; done(); };
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  });
}

async function record(ms) {
  const recorder = new MediaRecorder(new MediaStream(stream.getAudioTracks()), mime ? { mimeType: mime } : {});
  const parts = [];
  recorder.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
  const stopped = new Promise((r) => { recorder.onstop = r; });
  recorder.start();
  const start = performance.now();
  await sleep(ms);
  recorder.stop();
  await stopped;
  return { blob: new Blob(parts, { type: recorder.mimeType || mime }), start, stop: performance.now() };
}

function grabFrame() {
  const canvas = document.createElement("canvas");
  canvas.width = 640;
  canvas.height = 480;
  canvas.getContext("2d").drawImage($("cam"), 0, 0, 640, 480);
  return new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.8));
}

async function post(path, body, type) {
  const started = performance.now();
  try {
    const res = await fetch(`/api/probe/${path}`, { method: "POST", body, headers: { "Content-Type": type, "X-Probe-Token": token } });
    const json = await res.json().catch(() => null);
    return { status: res.status, ms: Math.round(performance.now() - started), body: json };
  } catch (e) {
    return { status: 0, ms: Math.round(performance.now() - started), body: { error: String(e) } };
  }
}

async function trial(mode) {
  $("speak").disabled = $("silent").disabled = true;
  const id = `${mode}-${Date.now()}`;
  $("say").textContent = mode === "speak" ? "After the voice, say: I am fine" : "Stay silent until it finishes.";
  status("Playing the prompt…");
  const t0 = performance.now();
  const rel = (t) => (t == null ? null : Math.round(t - t0));
  const tts = await speak(PROMPT);
  await sleep(GUARD_MS);
  status("Recording 4 s…");
  const rec = await record(TURN_MS);
  $("say").textContent = "";
  status("Uploading…");
  const audio = await post(`audio?trial=${id}&stt=1`, rec.blob, rec.blob.type || "application/octet-stream");
  const frameBlob = await grabFrame();
  const frame = frameBlob ? await post(`frame?trial=${id}`, frameBlob, "image/jpeg") : { status: 0, body: { error: "no frame" } };
  const report = {
    trial: id, mode, user_agent: navigator.userAgent, chosen_mime: mime, recorder_mime: rec.blob.type, wake_lock: wakeLock,
    guard_ms: GUARD_MS, turn_ms: TURN_MS, prompt: PROMPT,
    times_ms: { tts_start: rel(tts.start), tts_end: rel(tts.end), listen_start: rel(rec.start), listen_stop: rel(rec.stop) },
    tts_error: tts.error, audio_bytes: rec.blob.size,
    audio: { status: audio.status, round_trip_ms: audio.ms, ...audio.body },
    frame: { status: frame.status, round_trip_ms: frame.ms, ...frame.body },
  };
  const saved = await post("report", JSON.stringify(report), "application/json");
  const a = report.audio;
  const ok = a.decodable && frame.status === 200 && !tts.error && saved.status === 200;
  logRow(`<b class="${ok ? "pass" : "fail"}">${ok ? "PASS" : "CHECK"}</b> ${mode} · ${rec.blob.type || "?"} ${rec.blob.size} B<br>
    decoded: ${a.decodable ? `${a.codec} ${a.duration_s} s, rms ${a.rms_dbfs} dBFS` : (a.error || a.detail || a.status)}<br>
    heard: “${a.transcript ?? ""}” (${a.stt_ms ?? "?"} ms STT, round trip ${a.round_trip_ms} ms)<br>
    tts: ${tts.error ? `error ${tts.error}` : `${report.times_ms.tts_start}–${report.times_ms.tts_end} ms`} ·
    frame: ${frame.status === 200 ? `${frame.body.width}×${frame.body.height}` : `failed ${frame.status}`} · report ${saved.status}`);
  status("Done. Run another trial or close the page.", ok ? "pass" : "fail");
  $("speak").disabled = $("silent").disabled = false;
}

$("speak").onclick = () => trial("speak");
$("silent").onclick = () => trial("silent");

$("start").onclick = async () => {
  if (!token) {
    status("Missing token: open the link that ends with #t=…", "fail");
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: { facingMode: "environment", width: 640, height: 480 },
    });
  } catch (e) {
    status(`Microphone or camera refused: ${e.name}`, "fail");
    return;
  }
  $("cam").srcObject = stream;
  mime = MIMES.find((m) => window.MediaRecorder?.isTypeSupported?.(m)) ?? "";
  // iOS only lets speech start from a tap; this empty line unlocks it.
  speechSynthesis.speak(new SpeechSynthesisUtterance(""));
  try {
    await navigator.wakeLock.request("screen");
    wakeLock = "granted";
  } catch (e) {
    wakeLock = navigator.wakeLock ? `refused: ${e.name}` : "unsupported";
  }
  $("start").disabled = true;
  $("speak").disabled = $("silent").disabled = false;
  status(`Ready. Recorder MIME: ${mime || "browser default"}. Wake lock: ${wakeLock}.`);
};
