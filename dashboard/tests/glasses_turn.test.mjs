import assert from "node:assert/strict";
import { test } from "node:test";
import { recordUntil, speakLine, turnLength } from "../glasses/turn.js";

const listen = { turn_ms: 4000, remaining_ms: 1000, until_backend_ms: 51_000 };

test("the last turn ends a margin before the backend deadline even when the poll reply arrived late", () => {
  // Review finding 7: deadline 1000 ms after the poll; the reply reached the phone at 200 ms.
  // Phone clock = backend clock - 50 000 ms. The old code recorded 1000 ms from 200 ms, ending at 1200.
  // Review round 3: the turn also stops 250 ms early, so a late timer cannot push it past the deadline.
  const offset = 50_000;
  const now = 51_000 - offset - 800;   // 200 ms after the poll on the phone clock
  assert.equal(turnLength(listen, offset, now), 550);
  assert.equal(turnLength(listen, offset, now, 300, 300, 100), 700);
});

test("a full turn when the deadline is far, nothing when it is too close", () => {
  assert.equal(turnLength({ ...listen, until_backend_ms: 60_000 }, 0, 50_000), 4000);
  assert.equal(turnLength({ ...listen, until_backend_ms: 50_500 }, 0, 50_000), 0);   // 500 ms left, 250 kept back
  assert.equal(turnLength(null, 0, 0), 0);
});

test("before the first clock sync the stale remaining time is shortened by a margin", () => {
  assert.equal(turnLength(listen, null, 0), 700);
});

function fakeRecorder(clock, startupMs) {
  return {
    start() { clock.t += startupMs; queueMicrotask(() => this.onstart()); },
    stop() { clock.stoppedAt = clock.t; queueMicrotask(() => this.onstop()); },
  };
}

test("a slow recorder start shortens the turn instead of running past the deadline", async () => {
  // Review finding 7: chosen at 200 ms for a 1000 ms deadline, recorder ready at 400 ms.
  const clock = { t: 200 };
  const sleep = async (ms) => { clock.t += ms; };
  const got = await recordUntil(fakeRecorder(clock, 200), 1000, { now: () => clock.t, sleep });
  assert.deepEqual(got, { start: 400, end: 1000 });
  assert.equal(clock.stoppedAt, 1000);
});

test("a recorder that starts after the deadline stops at once with an empty turn", async () => {
  const clock = { t: 900 };
  const sleep = async (ms) => { clock.t += ms; };
  const got = await recordUntil(fakeRecorder(clock, 300), 1000, { now: () => clock.t, sleep });
  assert.equal(got.end - got.start, 0);
});

test("a timer that wakes late still leaves the capture end before the deadline", async () => {
  // Review round 3: a timer woke 7.2 ms late and the turn ended past the deadline, so a HELP in it was refused.
  const deadline = 1000;
  const clock = { t: 200 };
  const length = turnLength({ turn_ms: 4000, remaining_ms: 800, until_backend_ms: 51_000 }, 50_000, clock.t);
  const sleep = async (ms) => { clock.t += ms + 100; };    // 100 ms late, far worse than 7.2 ms
  const got = await recordUntil(fakeRecorder(clock, 0), clock.t + length, { now: () => clock.t, sleep });
  assert.ok(got.end <= deadline, `capture ended at ${got.end}`);
});

function fakeSynth() {
  const synth = { spoken: [], cancels: 0, speak(u) { this.spoken.push(u); }, cancel() { this.cancels += 1; } };
  return synth;
}

function speakDeps(synth, clock, timers) {
  return { synth, makeUtterance: () => ({}), now: () => clock.t,
           setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length - 1; }, clearTimer: () => {}, timeoutMs: 15_000 };
}

test("a line that ends normally reports its start and end", async () => {
  const synth = fakeSynth(), clock = { t: 100 }, timers = [];
  const done = speakLine("hello", speakDeps(synth, clock, timers));
  synth.spoken[0].onstart(); clock.t = 400; synth.spoken[0].onend();
  assert.deepEqual(await done, { start: 100, end: 400, timedOut: false });
});

test("a line whose end event never comes is cut off and its span runs from the start to the cut", async () => {
  // Review round 3: after the timeout the phone listened while the speaker may still have been talking.
  const synth = fakeSynth(), clock = { t: 5000 }, timers = [];
  const done = speakLine("hello", speakDeps(synth, clock, timers));
  clock.t = 5200; synth.spoken[0].onstart();
  clock.t = 20_000; timers[0].fn();
  assert.deepEqual(await done, { start: 5200, end: 20_000, timedOut: true });
  assert.equal(synth.cancels, 2);   // once before speaking, once at the timeout
});

test("without a start event the span begins when the line was handed to the speaker", async () => {
  const synth = fakeSynth(), clock = { t: 5000 }, timers = [];
  const done = speakLine("hello", speakDeps(synth, clock, timers));
  clock.t = 20_000; timers[0].fn();
  assert.deepEqual(await done, { start: 5000, end: 20_000, timedOut: true });
});

test("a line the browser cannot speak reports nothing", async () => {
  const synth = fakeSynth(), clock = { t: 0 }, timers = [];
  const done = speakLine("hello", speakDeps(synth, clock, timers));
  synth.spoken[0].onerror();
  assert.equal(await done, null);
});
