// How long the next listening turn may run, in phone ms. The deadline comes as backend
// monotonic ms and is converted with the phone's clock mapping, because remaining_ms has
// already aged by the network trip that brought it. Before the first sync, remaining_ms
// minus a margin is the fallback. The turn stops `deadlineMarginMs` before the deadline: a timer can wake a few
// milliseconds late and the clock mapping has its own uncertainty, and the backend refuses audio captured after the
// deadline, so a turn that ran past it could lose a HELP. Returns 0 when there is no room for a useful turn.
export function turnLength(listen, backendOffset, nowMs, minTurnMs = 300, fallbackMarginMs = 300, deadlineMarginMs = 250) {
  if (!listen) return 0;
  const left = backendOffset !== null && listen.until_backend_ms != null
    ? listen.until_backend_ms - backendOffset - nowMs - deadlineMarginMs
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

// Says a line and reports when the voice was on. If the end event never comes the voice is cut off and the span runs
// to that moment, from the start event or, without one, from the call: the backend keeps the whole span out of any
// recorded turn. Returns null when the browser could not speak it at all.
export function speakLine(text, { synth, makeUtterance, now, setTimer, clearTimer, timeoutMs }) {
  return new Promise((resolve) => {
    const calledAt = now();
    const utterance = makeUtterance(text);
    let started = null;
    let finished = false;
    let timer = null;
    const finish = (value) => {
      if (finished) return;
      finished = true;
      clearTimer(timer);
      resolve(value);
    };
    timer = setTimer(() => {
      synth.cancel();
      finish({ start: started ?? calledAt, end: now(), timedOut: true });
    }, timeoutMs);
    utterance.onstart = () => { started = now(); };
    utterance.onend = () => finish({ start: started ?? calledAt, end: now(), timedOut: false });
    utterance.onerror = () => finish(null);
    synth.cancel();
    synth.speak(utterance);
  });
}
