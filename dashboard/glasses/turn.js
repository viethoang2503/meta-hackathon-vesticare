// How long the next listening turn may run, in phone ms. The deadline comes as backend
// monotonic ms and is converted with the phone's clock mapping, because remaining_ms has
// already aged by the network trip that brought it. Before the first sync, remaining_ms
// minus a margin is the fallback. Returns 0 when there is no room for a useful turn.
export function turnLength(listen, backendOffset, nowMs, minTurnMs = 300, fallbackMarginMs = 300) {
  if (!listen) return 0;
  const left = backendOffset !== null && listen.until_backend_ms != null
    ? listen.until_backend_ms - backendOffset - nowMs
    : listen.remaining_ms - fallbackMarginMs;
  return left >= minTurnMs ? Math.min(listen.turn_ms, left) : 0;
}

// Records until an absolute phone time. The recorder may start late; the turn then runs shorter
// instead of past the deadline. `recorder` has start(), stop(), onstart and onstop like MediaRecorder.
export async function recordUntil(recorder, endAt, { now, sleep }) {
  const started = new Promise((resolve) => { recorder.onstart = () => resolve(now()); });
  const stopped = new Promise((resolve) => { recorder.onstop = resolve; });
  recorder.start();
  const start = await started;  // capture begins here, not when start() was called
  await sleep(Math.max(0, endAt - now()));
  const end = now();  // capture ends at stop(); finalizing the file comes after
  recorder.stop();
  await stopped;
  return { start, end };
}
