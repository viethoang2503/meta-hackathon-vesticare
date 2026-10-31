import assert from "node:assert/strict";
import { test } from "node:test";
import { classifyReply } from "../glasses/reply.js";

test("a clear I'm OK in English or Vietnamese cancels", () => {
  for (const said of ["I'm OK", "i am okay", "I’m fine thanks", "Tôi ổn", "không sao", "ổn rồi"]) {
    assert.equal(classifyReply(said), "ok", said);
  }
});

test("asking for help wins even when the sentence also contains ok", () => {
  for (const said of ["help", "I'm not okay", "tôi không ổn", "cứu tôi với", "đau quá", "I can't get up"]) {
    assert.equal(classifyReply(said), "help", said);
  }
});

test("noise, silence and half words keep the countdown running", () => {
  for (const said of ["", "uh", "okay google", "on the stairs", "bạn có ổn không", "token", null]) {
    assert.equal(classifyReply(said), "unclear", String(said));
  }
});
