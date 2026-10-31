import { classifyReply } from "./reply.js";

const LISTEN_MS = 6000;
const QUESTION = { "en-US": "Are you OK?", "vi-VN": "Bạn có ổn không?" };
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const $ = (id) => document.getElementById(id);

$("caps").textContent = [
  `HTTPS: ${window.isSecureContext ? "có" : "KHÔNG (micro sẽ bị chặn)"}`,
  `Nhận giọng nói: ${Recognition ? "có" : "KHÔNG"}`,
  `Đọc giọng nói: ${"speechSynthesis" in window ? "có" : "KHÔNG"}`,
].join(" · ");

function show(text, verdict) {
  $("now").textContent = text;
  $("now").className = verdict ? `verdict-${verdict}` : "";
}

function record(mode, outcome) {
  const li = document.createElement("li");
  li.textContent = `${mode}: ${outcome}`;
  $("log").prepend(li);
}

function speak(text, lang) {
  return new Promise((resolve, reject) => {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang;
    u.onend = resolve;
    u.onerror = (e) => reject(new Error(`speak ${e.error}`));
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  });
}

// Resolves with every transcript heard before a verdict or the time limit.
function listen(lang) {
  return new Promise((resolve, reject) => {
    if (!Recognition) return reject(new Error("no SpeechRecognition"));
    const rec = new Recognition();
    rec.lang = lang;
    rec.interimResults = true;
    rec.continuous = true;
    rec.maxAlternatives = 3;
    const heard = [];
    let verdict = "unclear";
    const stop = setTimeout(() => rec.stop(), LISTEN_MS);
    rec.onresult = (event) => {
      for (const result of event.results) {
        for (const alt of result) {
          if (!heard.includes(alt.transcript)) heard.push(alt.transcript);
          const v = classifyReply(alt.transcript);
          if (v !== "unclear") verdict = v;
        }
      }
      $("heard").textContent = `Nghe được: ${heard.join(" | ")}`;
      if (verdict !== "unclear") rec.stop();
    };
    rec.onerror = (e) => { clearTimeout(stop); reject(new Error(`listen ${e.error}`)); };
    rec.onend = () => { clearTimeout(stop); resolve({ verdict, heard }); };
    rec.start();
  });
}

async function trial(mode, ask) {
  const lang = $("lang").value;
  for (const b of document.querySelectorAll("button")) b.disabled = true;
  $("heard").textContent = "";
  try {
    if (ask) {
      show("Đang hỏi…");
      await speak(QUESTION[lang], lang);
    }
    show("Đang nghe… hãy trả lời");
    const started = performance.now();
    const { verdict, heard } = await listen(lang);
    const ms = Math.round(performance.now() - started);
    const label = { ok: "HỦY: người đeo ổn", help: "BÁO NGAY: cần giúp", unclear: "KHÔNG RÕ: tiếp tục đếm ngược" }[verdict];
    show(label, verdict);
    record(mode, `${label} · ${ms} ms · "${heard.join(" | ") || "không nghe thấy gì"}"`);
  } catch (err) {
    show(`Lỗi: ${err.message}`, "help");
    record(mode, `lỗi ${err.message}`);
  } finally {
    for (const b of document.querySelectorAll("button")) b.disabled = false;
  }
}

$("ask").addEventListener("click", () => trial("Hỏi rồi nghe", true));
$("listen").addEventListener("click", () => trial("Chỉ nghe", false));
$("timer").addEventListener("click", () => {
  show("Chờ 5 s… đặt điện thoại xuống, đừng chạm");
  setTimeout(() => trial("Tự hỏi sau 5 s", true), 5000);
});
