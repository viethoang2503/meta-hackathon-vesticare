const test = require("node:test");
const assert = require("node:assert/strict");

const {
  parseSample,
  SampleBatcher,
  eventMatchesSource,
  normalizeEventSamples,
} = require("../js/serial.js");

test("parseSample accepts exactly six finite sensor axes", () => {
  assert.equal(parseSample("WiFi connected"), null);
  assert.equal(
    parseSample('{"ax":0,"ay":0,"az":1,"gx":0,"gy":0}'),
    null,
  );
  assert.equal(
    parseSample('{"ax":"0","ay":0,"az":1,"gx":0,"gy":0,"gz":0}'),
    null,
  );
  assert.deepEqual(
    parseSample('{"ax":0,"ay":0,"az":1,"gx":0.1,"gy":0.2,"gz":0.3}'),
    { ax: 0, ay: 0, az: 1, gx: 0.1, gy: 0.2, gz: 0.3 },
  );
});

test("SampleBatcher emits exact batches and clear drops partial data", () => {
  const batcher = new SampleBatcher(10);
  for (let i = 0; i < 9; i += 1) {
    assert.equal(batcher.push({ ax: i }), null);
  }
  batcher.clear();
  for (let i = 0; i < 9; i += 1) {
    assert.equal(batcher.push({ ax: i }), null);
  }
  const batch = batcher.push({ ax: 9 });
  assert.equal(batch.length, 10);
  assert.equal(batch[9].ax, 9);
});

test("eventMatchesSource allows legacy events only for Wi-Fi", () => {
  assert.equal(eventMatchesSource({ transport: "usb" }, "usb"), true);
  assert.equal(eventMatchesSource({ transport: "wifi" }, "usb"), false);
  assert.equal(eventMatchesSource({ transport: "wifi" }, "wifi"), true);
  assert.equal(eventMatchesSource({ transport: null }, "wifi"), true);
  assert.equal(eventMatchesSource({}, "wifi"), true);
});

test("normalizeEventSamples supports batch and legacy six-axis SSE", () => {
  const sample = { ax: 1, ay: 2, az: 3, gx: 4, gy: 5, gz: 6 };
  assert.deepEqual(normalizeEventSamples({ samples: [sample] }), [sample]);
  assert.deepEqual(normalizeEventSamples(sample), [sample]);
  assert.deepEqual(normalizeEventSamples({ svm: 1 }), []);
});
