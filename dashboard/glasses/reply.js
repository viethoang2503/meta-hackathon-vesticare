// What the wearer said after "Are you OK?". Only a clear "I'm OK" cancels;
// anything unclear keeps the countdown running, because a wrong cancel leaves
// a real fall unreported while a wrong alert only costs the caregiver a check.

// Asking for anyone to be called counts as help too, so "I'm OK, call my
// daughter" alerts: the safe side of an ambiguous sentence.
const HELP = [
  "help", "not ok", "not okay", "not fine", "hurt", "hurts", "pain", "can't get up", "cannot get up",
  "fell", "fallen", "call", "ambulance", "emergency",
  "cuu", "giup", "khong on", "dau", "khong dung day duoc", "nga", "goi", "cap cuu",
];
const OK = [
  "i'm ok", "im ok", "i am ok", "i'm okay", "im okay", "i am okay", "i'm fine", "im fine", "i am fine",
  "toi on", "minh on", "khong sao", "on roi",
];

function normalize(text) {
  return ` ${text
    .toLowerCase()
    .replace(/đ/g, "d")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[’`]/g, "'")
    .replace(/[^a-z' ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()} `;
}

const has = (text, phrases) => phrases.some((p) => text.includes(` ${p} `));

export function classifyReply(transcript) {
  const text = normalize(transcript ?? "");
  if (has(text, HELP)) return "help";
  if (has(text, OK)) return "ok";
  return "unclear";
}
