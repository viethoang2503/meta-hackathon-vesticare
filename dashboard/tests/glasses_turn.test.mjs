import assert from "node:assert/strict";
import { test } from "node:test";
import { turnLength } from "../glasses/turn.js";

const listen = { turn_ms: 4000, remaining_ms: 1000, until_backend_ms: 51_000 };

test("the last turn ends at the backend deadline even when the poll reply arrived late", () => {
  // Review finding 7: deadline 1000 ms after the poll; the reply reached the phone at 200 ms.
  // Phone clock = backend clock - 50 000 ms. The old code recorded 1000 ms from 200 ms, ending at 1200.
  const offset = 50_000;
  const now = 51_000 - offset - 800;   // 200 ms after the poll on the phone clock
  assert.equal(turnLength(listen, offset, now), 800);
});

test("a full turn when the deadline is far, nothing when it is too close", () => {
  assert.equal(turnLength({ ...listen, until_backend_ms: 60_000 }, 0, 50_000), 4000);
  assert.equal(turnLength({ ...listen, until_backend_ms: 50_200 }, 0, 50_000), 0);
  assert.equal(turnLength(null, 0, 0), 0);
});

test("before the first clock sync the stale remaining time is shortened by a margin", () => {
  assert.equal(turnLength(listen, null, 0), 700);
});
