/**
 * live_state.js — pure state model for the live monitor.
 *
 * Owns no DOM, no timers and no fetch. It takes SSE dicts plus an injected
 * `nowMs` and returns a snapshot; realtime.js does the rendering. That split is
 * what makes stale-data and FALL-latch behaviour testable in Node without a
 * browser, and those are exactly the rules that are easy to get subtly wrong.
 *
 * The invariant this file exists to enforce:
 *
 *   Silence is never reassurance. An IMU stream that stopped two seconds ago
 *   reports UNKNOWN, not NO FALL — a dashboard that shows green because nothing
 *   is arriving is worse than one that shows nothing.
 */
(function exposeLiveState(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.LiveState = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function buildApi() {
  "use strict";

  const DECISIONS = {
    NORMAL: "NO FALL",
    VESTIBULAR_INSTABILITY: "INSTABILITY",
    FALL: "FALL",
    BUFFERING: "BUFFERING",
  };

  const UNKNOWN = "UNKNOWN";

  const DEFAULTS = {
    imuStaleMs: 2000,
    fallLatchMs: 10000,
    maxTimelineEvents: 50,
  };

  function numberOr(value, fallback) {
    return typeof value === "number" && isFinite(value) ? value : fallback;
  }

  function create(options) {
    const config = Object.assign({}, DEFAULTS, options || {});

    let imu = null;
    let imuAtMs = null;
    let fallLatchedAtMs = null;
    let lastDecision = null;
    const timeline = [];

    function pushTimeline(label, nowMs) {
      timeline.push({ label, atMs: nowMs });
      while (timeline.length > config.maxTimelineEvents) timeline.shift();
    }

    function classify(event) {
      // A legacy event has no event_type but does carry a string `state`. Routing
      // on that keeps older publishers working rather than dropping them.
      if (event && typeof event.event_type === "string") return event.event_type;
      if (event && typeof event.state === "string") return "imu_prediction";
      return null;
    }

    function acceptEvent(event, nowMs) {
      const kind = classify(event);
      if (kind === "imu_prediction") {
        imu = event;
        imuAtMs = nowMs;
        const decision = DECISIONS[event.state] || UNKNOWN;
        if (decision === "FALL") fallLatchedAtMs = nowMs;
        if (decision !== lastDecision) {
          pushTimeline(decision, nowMs);
          lastDecision = decision;
        }
      }
    }

    function fresh(atMs, nowMs, windowMs) {
      return atMs !== null && nowMs - atMs < windowMs;
    }

    function snapshot(nowMs) {
      const imuFresh = fresh(imuAtMs, nowMs, config.imuStaleMs);
      const latched =
        fallLatchedAtMs !== null && nowMs - fallLatchedAtMs <= config.fallLatchMs;

      let decision;
      if (latched) {
        // Held even when the IMU has gone stale: a confirmed fall must not fade
        // into UNKNOWN while someone is still walking to the room.
        decision = "FALL";
      } else if (!imuFresh || !imu) {
        decision = UNKNOWN;
      } else {
        decision = DECISIONS[imu.state] || UNKNOWN;
      }

      const evidenceText = !imuFresh || !imu ? "IMU unavailable" : "IMU live";

      return {
        decision,
        probabilities: {
          normal: numberOr(imu && imu.normal_probability, 0),
          instability: numberOr(imu && imu.instability_probability, 0),
          fall: numberOr(imu && imu.fall_probability, 0),
        },
        imuFresh,
        imu,
        evidenceText,
        timeline: timeline.slice(),
      };
    }

    return { acceptEvent, snapshot };
  }

  return { create, DECISIONS, UNKNOWN };
});
