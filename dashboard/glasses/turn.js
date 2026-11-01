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
