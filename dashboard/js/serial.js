/**
 * serial.js — Web Serial reader and bounded HTTP batch relay.
 *
 * This module deliberately owns no DOM. realtime.js supplies configuration
 * and renders the status objects reported through onStatus.
 */
(function exposeSerialBridge(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.SerialBridge = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function buildApi() {
  "use strict";

  const AXES = ["ax", "ay", "az", "gx", "gy", "gz"];
  const RETRY_DELAYS_MS = [250, 500, 1000];
  const MAX_QUEUED_BATCHES = 5;

  function parseSample(line) {
    try {
      const value = JSON.parse(line);
      if (!value || Array.isArray(value)) return null;
      if (!AXES.every((key) => Number.isFinite(value[key]))) return null;
      return Object.fromEntries(AXES.map((key) => [key, value[key]]));
    } catch (_) {
      return null;
    }
  }

  class SampleBatcher {
    constructor(size = 10) {
      if (!Number.isInteger(size) || size < 1) {
        throw new TypeError("Batch size must be a positive integer");
      }
      this.size = size;
      this.samples = [];
    }

    push(sample) {
      this.samples.push(sample);
      if (this.samples.length < this.size) return null;
      return this.samples.splice(0, this.size);
    }

    clear() {
      this.samples.length = 0;
    }
  }

  function eventMatchesSource(event, source) {
    return source === "usb"
      ? event.transport === "usb"
      : event.transport !== "usb";
  }

  function normalizeEventSamples(event) {
    if (Array.isArray(event.samples)) return event.samples;
    if (AXES.every((key) => Number.isFinite(event[key]))) {
      return [Object.fromEntries(AXES.map((key) => [key, event[key]]))];
    }
    return [];
  }

  function create(options = {}) {
    const serial = options.serial
      ?? (typeof navigator !== "undefined" ? navigator.serial : null);
    const fetchImpl = options.fetch
      ?? (typeof fetch !== "undefined" ? fetch.bind(globalThis) : null);
    const wait = options.wait
      ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
    const onStatus = options.onStatus ?? (() => {});
    const apiBase = (options.apiBase ?? "").replace(/\/$/, "");
    const batcher = new SampleBatcher(options.batchSize ?? 10);

    let port = null;
    let reader = null;
    let readTask = null;
    let heartbeatTimer = null;
    let connected = false;
    let generation = 0;
    let invalidLines = 0;
    let lineBuffer = "";
    let relayConfig = null;
    let queue = [];
    let draining = false;
    let writeChain = Promise.resolve();

    function report(state, details = {}) {
      onStatus({
        state,
        invalidLines,
        queuedBatches: queue.length,
        ...details,
      });
    }

    function isSupported() {
      return Boolean(serial && typeof serial.requestPort === "function");
    }

    function isConnected() {
      return connected;
    }

    function writeCommand(command) {
      if (!port || !port.writable) return Promise.resolve(false);
      writeChain = writeChain.then(async () => {
        if (!port || !port.writable) return false;
        const writer = port.writable.getWriter();
        try {
          await writer.write(new TextEncoder().encode(`${command}\n`));
          return true;
        } finally {
          writer.releaseLock();
        }
      });
      return writeChain.catch((error) => {
        report("write-error", { message: error.message });
        return false;
      });
    }

    async function postBatch(samples, activeGeneration) {
      const headers = { "Content-Type": "application/json" };
      if (relayConfig.token) {
        headers.Authorization = `Bearer ${relayConfig.token}`;
      }
      const response = await fetchImpl(`${apiBase}/api/sensor/batch`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          patient_id: relayConfig.patientId,
          device_id: relayConfig.deviceId,
          firmware_version: relayConfig.firmwareVersion,
          ntp_synced: false,
          transport: "usb",
          samples,
        }),
      });
      if (activeGeneration !== generation) return false;
      if (response.status === 401) {
        const error = new Error("Device token was rejected");
        error.code = "AUTH";
        throw error;
      }
      if (!response.ok) {
        throw new Error(`POST /api/sensor/batch returned HTTP ${response.status}`);
      }
      return true;
    }

    async function drainQueue(activeGeneration) {
      if (draining || !connected || !fetchImpl) return;
      draining = true;
      try {
        while (queue.length > 0 && connected && activeGeneration === generation) {
          let delivered = false;
          for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
            try {
              delivered = await postBatch(queue[0], activeGeneration);
              break;
            } catch (error) {
              if (error.code === "AUTH") {
                queue = [];
                report("auth-error", { message: error.message });
                return;
              }
              if (attempt === RETRY_DELAYS_MS.length) {
                report("backend-error", { message: error.message });
                break;
              }
              await wait(RETRY_DELAYS_MS[attempt]);
            }
          }
          if (activeGeneration !== generation || !connected) return;
          queue.shift();
          report(delivered ? "connected" : "batch-dropped");
        }
      } finally {
        draining = false;
      }
    }

    function enqueueSample(sample, activeGeneration) {
      const batch = batcher.push(sample);
      if (!batch) return;
      if (queue.length >= MAX_QUEUED_BATCHES) queue.shift();
      queue.push(batch);
      report("connected");
      void drainQueue(activeGeneration);
    }

    function consumeText(text, activeGeneration) {
      lineBuffer += text;
      const lines = lineBuffer.split(/\r?\n/);
      lineBuffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const sample = parseSample(line);
        if (sample) enqueueSample(sample, activeGeneration);
        else invalidLines += 1;
      }
    }

    async function readLoop(activeGeneration) {
      const decoder = new TextDecoder();
      try {
        reader = port.readable.getReader();
        while (connected && activeGeneration === generation) {
          const { value, done } = await reader.read();
          if (done) break;
          consumeText(decoder.decode(value, { stream: true }), activeGeneration);
        }
      } catch (error) {
        if (activeGeneration === generation) {
          report("read-error", { message: error.message });
        }
      } finally {
        if (reader) {
          reader.releaseLock();
          reader = null;
        }
        if (activeGeneration === generation && connected) {
          connected = false;
          clearInterval(heartbeatTimer);
          heartbeatTimer = null;
          batcher.clear();
          queue = [];
          report("disconnected");
        }
      }
    }

    async function connect({
      patientId,
      deviceId = "esp32-webserial",
      firmwareVersion = "webserial-relay-1.0",
      token = "",
    }) {
      if (!isSupported()) throw new Error("Web Serial is not supported");
      if (!fetchImpl) throw new Error("Fetch API is not available");
      if (!Number.isInteger(patientId) || patientId < 1) {
        throw new TypeError("Patient ID must be a positive integer");
      }
      if (connected) return;

      report("connecting");
      const selectedPort = await serial.requestPort();
      await selectedPort.open({ baudRate: 115200 });
      port = selectedPort;
      relayConfig = { patientId, deviceId, firmwareVersion, token };
      connected = true;
      invalidLines = 0;
      lineBuffer = "";
      queue = [];
      batcher.clear();
      generation += 1;
      const activeGeneration = generation;

      await writeCommand("MODE USB");
      heartbeatTimer = setInterval(() => void writeCommand("PING"), 2000);
      readTask = readLoop(activeGeneration);
      report("connected");
    }

    async function disconnect({ resumeWifi = true } = {}) {
      if (!port && !connected) return;
      const activePort = port;
      if (resumeWifi && connected) await writeCommand("MODE WIFI");
      generation += 1;
      connected = false;
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
      batcher.clear();
      queue = [];
      lineBuffer = "";

      if (reader) {
        try { await reader.cancel(); } catch (_) { /* port already gone */ }
      }
      if (readTask) {
        try { await readTask; } catch (_) { /* reported by readLoop */ }
      }
      readTask = null;
      port = null;
      try { await activePort.close(); } catch (_) { /* physical disconnect */ }
      report("disconnected");
    }

    return { connect, disconnect, isConnected, isSupported };
  }

  return {
    parseSample,
    SampleBatcher,
    eventMatchesSource,
    normalizeEventSamples,
    create,
  };
});
