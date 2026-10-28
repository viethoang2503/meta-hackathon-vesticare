/**
 * realtime_contract.test.js — static wiring contract for the live page.
 *
 * These tests read the shipped files as text. They cannot prove the page
 * renders, but they do catch the failure mode that matters here: markup and
 * scripts drifting apart, so an element the renderer writes to silently does
 * not exist, or a duplicate id makes `getElementById` update the wrong node.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..", "..");

function read(relative) {
  return fs.readFileSync(path.join(ROOT, relative), "utf8");
}

const HTML = read("dashboard/index.html");
const REALTIME = read("dashboard/js/realtime.js");
const COMPONENTS = read("dashboard/css/components.css");
const RESPONSIVE = read("dashboard/css/responsive.css");

function idCount(html, id) {
  return (html.match(new RegExp(`id=["']${id}["']`, "g")) || []).length;
}

// --- markup ------------------------------------------------------------------

test("live page exposes one copy of every decision control", () => {
  for (const id of [
    "prob-normal", "prob-instability", "prob-fall", "imu-freshness",
    "evidence-line", "event-timeline",
  ]) {
    assert.equal(idCount(HTML, id), 1, `expected exactly one #${id}`);
  }
  assert.ok(HTML.indexOf("js/live_state.js") < HTML.indexOf("js/realtime.js"));
});

test("existing sensor and chart ids stay unique", () => {
  for (const id of [
    "sensor-ax", "sensor-ay", "sensor-az", "sensor-gx", "sensor-gy", "sensor-gz",
    "svm-chart", "accel-chart", "gyro-chart", "conn-status",
    "metric-svm", "metric-peak-svm", "metric-conf", "metric-patient", "metric-buffer",
  ]) {
    assert.equal(idCount(HTML, id), 1, `expected exactly one #${id}`);
  }
});

test("the decision badge is the only state display", () => {
  // #state-badge was removed on purpose. It showed the raw per-event state, so
  // it stayed green while the stream was stale — the one thing the model exists
  // to prevent. Two badges disagreeing about the same moment is worse than one.
  assert.equal(idCount(HTML, "state-badge"), 0);
  assert.ok(!REALTIME.includes("state-badge"));
  assert.equal(idCount(HTML, "decision-badge"), 1);
});

test("scripts load dependencies before the module that consumes them", () => {
  const order = ["js/api.js", "js/charts.js", "js/serial.js", "js/live_state.js",
    "js/realtime.js"];
  const positions = order.map((src) => HTML.indexOf(src));
  assert.ok(positions.every((position) => position > -1), "every script is loaded");
  for (let i = 1; i < positions.length; i += 1) {
    assert.ok(positions[i - 1] < positions[i], `${order[i - 1]} loads before ${order[i]}`);
  }
});

test("probabilities are accessible progress bars bounded at one", () => {
  for (const id of ["prob-normal", "prob-instability", "prob-fall"]) {
    const element = HTML.match(new RegExp(`<progress[^>]*id=["']${id}["'][^>]*>`));
    assert.ok(element, `#${id} is a <progress> element`);
    assert.match(element[0], /max=["']1["']/, `#${id} is bounded at 1`);
  }
});

test("every probability bar carries a text label, not colour alone", () => {
  for (const id of ["prob-normal", "prob-instability", "prob-fall"]) {
    assert.ok(new RegExp(`for=["']${id}["']`).test(HTML)
      || new RegExp(`<progress[^>]*id=["']${id}["'][^>]*aria-label=`).test(HTML),
      `#${id} has a label or aria-label`);
  }
  assert.equal(idCount(HTML, "prob-normal-value"), 1);
  assert.equal(idCount(HTML, "prob-instability-value"), 1);
  assert.equal(idCount(HTML, "prob-fall-value"), 1);
});

test("the decision badge announces changes to assistive technology", () => {
  const badge = HTML.match(/<[^>]*id=["']decision-badge["'][^>]*>/);
  assert.ok(badge, "#decision-badge exists");
  assert.match(badge[0], /aria-live=["']polite["']/);
});

// --- realtime wiring ---------------------------------------------------------

test("sse messages are routed through the live state model", () => {
  assert.match(REALTIME, /function handleLiveEvent\(/);
  assert.match(REALTIME, /function renderLiveSnapshot\(/);
  assert.match(REALTIME, /handleLiveEvent\(data\)/);
  assert.match(REALTIME, /LiveState\.create\(/);
});

test("a repaint timer makes the stale transition happen without traffic", () => {
  assert.match(REALTIME, /setInterval\([^,]+,\s*500\)/);
});

test("the view filter is applied once, before the decision model sees an event", () => {
  // Two copies of this predicate drifted apart easily and hid which one actually
  // gated the badge. The gate that matters is the one in front of
  // liveState.acceptEvent, so it lives in handleLiveEvent and nowhere else.
  assert.match(REALTIME, /function _imuEventIsForThisView\(/);
  const body = REALTIME.slice(REALTIME.indexOf("function handleStateEvent("));
  const stateEvent = body.slice(0, body.indexOf("\n}"));
  assert.ok(!stateEvent.includes("_patientFilter"),
    "handleStateEvent must not re-filter what its caller already filtered");
});

test("the event timeline is built as nodes, never as an HTML string", () => {
  // Keeps the list safe if a future event type passes backend text through.
  const timeline = REALTIME.slice(REALTIME.indexOf("function _renderTimeline("));
  const body = timeline.slice(0, timeline.indexOf("\n}"));
  assert.ok(!body.includes("innerHTML"),
    "_renderTimeline must not assign innerHTML");
  assert.match(body, /createElement\("li"\)/);
  assert.match(body, /textContent/);
});

// --- styling contract --------------------------------------------------------

test("the timeline stays bounded", () => {
  assert.match(COMPONENTS, /#event-timeline[\s\S]{0,200}overflow-y:\s*auto/);
});

test("decision states have text-bearing classes for every value", () => {
  for (const decision of ["no-fall", "instability", "fall", "unknown", "buffering"]) {
    assert.ok(COMPONENTS.includes(`.decision-${decision}`),
      `.decision-${decision} is styled`);
  }
});

test("narrow viewports stack the technical charts under the decision", () => {
  const at900 = RESPONSIVE.indexOf("max-width: 900px");
  const at500 = RESPONSIVE.indexOf("max-width: 500px");
  assert.ok(at900 > -1 && at500 > -1, "both breakpoints exist");
  // The 900 px block stacks the IMU charts.
  assert.match(RESPONSIVE.slice(at900, at500), /\.imu-chart-grid[\s\S]{0,120}grid-template-columns:\s*1fr/);
  // The 500 px block keeps the decision card first in source order.
  assert.match(RESPONSIVE.slice(at500), /\.live-grid[\s\S]{0,200}order/);
});
