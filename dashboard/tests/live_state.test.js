const test = require("node:test");
const assert = require("node:assert/strict");
const LiveState = require("../js/live_state.js");

// --- event routing -----------------------------------------------------------

test("legacy state events are treated as IMU predictions", () => {
  const state = LiveState.create();
  state.acceptEvent({ state: "NORMAL", timestamp: "2026-08-28T00:00:00Z" }, 0);
  assert.equal(state.snapshot(100).decision, "NO FALL");
});

test("every clinical state maps to its display decision", () => {
  const cases = [
    ["NORMAL", "NO FALL"],
    ["VESTIBULAR_INSTABILITY", "INSTABILITY"],
    ["FALL", "FALL"],
    ["BUFFERING", "BUFFERING"],
  ];
  for (const [value, expected] of cases) {
    const state = LiveState.create();
    state.acceptEvent({ event_type: "imu_prediction", state: value }, 0);
    assert.equal(state.snapshot(0).decision, expected, value);
  }
});

test("an unknown state does not silently become NO FALL", () => {
  const state = LiveState.create();
  state.acceptEvent({ event_type: "imu_prediction", state: "WAT" }, 0);
  assert.equal(state.snapshot(0).decision, "UNKNOWN");
});

// --- freshness ---------------------------------------------------------------

test("IMU older than two seconds is UNKNOWN rather than NO FALL", () => {
  const state = LiveState.create({ imuStaleMs: 2000 });
  state.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, 0);
  assert.equal(state.snapshot(2001).decision, "UNKNOWN");
});

test("IMU inside the freshness window still reports its decision", () => {
  const state = LiveState.create({ imuStaleMs: 2000 });
  state.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, 0);
  assert.equal(state.snapshot(1999).decision, "NO FALL");
  assert.equal(state.snapshot(1999).imuFresh, true);
});

test("with no IMU event at all the decision is UNKNOWN", () => {
  const state = LiveState.create();
  const snapshot = state.snapshot(0);
  assert.equal(snapshot.decision, "UNKNOWN");
  assert.equal(snapshot.imuFresh, false);
});

// --- FALL latch --------------------------------------------------------------

test("confirmed FALL display remains latched for ten seconds", () => {
  const state = LiveState.create({ fallLatchMs: 10000 });
  state.acceptEvent({ event_type: "imu_prediction", state: "FALL" }, 1000);
  state.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, 1500);
  assert.equal(state.snapshot(10999).decision, "FALL");

  // The IMU has to still be live for the post-latch assertion to be about the
  // latch. The plan's version asserted NO FALL at 11001 with the last event at
  // 1500 — 9.5 s stale — which its own safety rule requires to read UNKNOWN.
  // Keeping the stream fresh tests latch expiry; the stale case is covered by
  // "IMU older than two seconds is UNKNOWN" above.
  state.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, 10500);
  assert.equal(state.snapshot(11001).decision, "NO FALL");
});

test("latch expiry cannot resurrect a stale stream as NO FALL", () => {
  const state = LiveState.create({ fallLatchMs: 10000, imuStaleMs: 2000 });
  state.acceptEvent({ event_type: "imu_prediction", state: "FALL" }, 1000);
  state.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, 1500);
  assert.equal(state.snapshot(11001).decision, "UNKNOWN");
});

test("a stale IMU during the latch still shows FALL", () => {
  const state = LiveState.create({ fallLatchMs: 10000, imuStaleMs: 2000 });
  state.acceptEvent({ event_type: "imu_prediction", state: "FALL" }, 0);
  // No further events: IMU is stale, but a confirmed fall must not disappear
  // into UNKNOWN while the ten-second latch is running.
  assert.equal(state.snapshot(5000).decision, "FALL");
});

// --- evidence copy -----------------------------------------------------------

test("evidence reports whether the IMU stream is live", () => {
  const noImu = LiveState.create();
  assert.equal(noImu.snapshot(0).evidenceText, "IMU unavailable");

  const live = LiveState.create();
  live.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, 0);
  assert.equal(live.snapshot(0).evidenceText, "IMU live");
});

// --- probabilities -----------------------------------------------------------

test("probabilities are carried through and default to zero", () => {
  const state = LiveState.create();
  state.acceptEvent(
    {
      event_type: "imu_prediction", state: "NORMAL",
      normal_probability: 0.7, instability_probability: 0.2, fall_probability: 0.1,
    },
    0
  );
  assert.deepEqual(state.snapshot(0).probabilities,
    { normal: 0.7, instability: 0.2, fall: 0.1 });

  const bare = LiveState.create();
  bare.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, 0);
  assert.deepEqual(bare.snapshot(0).probabilities,
    { normal: 0, instability: 0, fall: 0 });
});

// --- timeline ----------------------------------------------------------------

test("timeline is bounded and records transitions only", () => {
  const state = LiveState.create({ maxTimelineEvents: 3 });
  ["NORMAL", "VESTIBULAR_INSTABILITY", "NORMAL", "FALL"].forEach((value, index) =>
    state.acceptEvent({ event_type: "imu_prediction", state: value }, index * 100)
  );
  assert.equal(state.snapshot(400).timeline.length, 3);
});

test("repeated identical states add no timeline entries", () => {
  const state = LiveState.create();
  for (let i = 0; i < 10; i += 1) {
    state.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, i * 10);
  }
  assert.equal(state.snapshot(100).timeline.length, 1);
});

test("snapshot returns a copied timeline so callers cannot mutate state", () => {
  const state = LiveState.create();
  state.acceptEvent({ event_type: "imu_prediction", state: "NORMAL" }, 0);
  const first = state.snapshot(0).timeline;
  first.push({ label: "injected" });
  assert.equal(state.snapshot(0).timeline.length, 1);
});
