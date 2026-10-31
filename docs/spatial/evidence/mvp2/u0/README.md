# U0. Phone probe

Gate U0 in [MVP2_PLAN.md](../../../MVP2_PLAN.md): the iPhone plays a line, records a 4 s turn that the backend can decode, and uploads a frame.

## Files

- `trials.jsonl` holds one row per trial on a real phone. The page writes it through `POST /api/probe/report`.
- `chrome_headless_smoke.jsonl` holds two rows from headless Chrome with a fake microphone and camera, recorded on 2026-10-31. It only shows that the page and the API work end to end. It is not U0 evidence for the iPhone.

Raw audio is decoded and then discarded. Frames are kept in `backend/probe_data/`, which git ignores.

## Run

1. Start the backend with a probe token. Without the token, `/api/probe/*` answers 404.

   ```
   cd backend
   export VC_PROBE_TOKEN=$(openssl rand -hex 16)
   ../.verify/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
   ```

2. Start the tunnel in a second terminal: `cloudflared tunnel --url http://127.0.0.1:8010`.
3. Open `https://<tunnel>/glasses/probe.html#t=<token>` on iPhone 1, mounted the way it will be in the demo.
4. Tap Start and allow the microphone and camera.
5. Run "Trial: I answer" three times and "Trial: nobody answers" three times.

## Gate

A trial passes when the audio is decodable, the frame is about 640×480, the speech has start and end events, and the report is saved. The gate also needs the following measurements written down:

- MIME and codec chosen on the iPhone.
- Whether the voice could be heard through the mount, judged by ear.
- The transcript of a "nobody answers" trial, which shows whether the microphone picked up the prompt after the guard.
- Round-trip time and STT time.

The machine voice and a scripted sentence do not count toward M7.

## Result on 2026-10-31

iPhone on iOS 18.7, Safari, held by hand at the laptop. The forehead mount is not tested yet. All numbers below come from `trials.jsonl`.

| Check | Result |
|---|---|
| Recorder format | `audio/mp4; codecs=mp4a.40.2` (AAC). All 6 turns decoded, 4.0 s each |
| Speech events | Start and end fired on every trial. Listening began 500 to 516 ms after the end event |
| Frame | 640×480 on every trial |
| Wake lock | Granted |
| STT time | Whisper small took 0.9 to 1.1 s per pass on 5 of 6 turns, and 3.1 s on one |
| Silent turns, run 1 (no VAD, Whisper's default context) | 3 of 3 produced text: "Thank you.", "It's a wonderful, wonderful, wonderful. Thank you. You're welcome. Thank you.", "Okay, we've done some over both. Have a good one." |
| Silent turn, run 2 (raw and VAD, no previous-text context) | Both empty, no_speech 1.0. Only 1 turn |
| Answer turns | Run 1: "I'm fine. I'm fine. I'm fine." Run 2: "I'm fine." both with and without VAD. The speaker said it once in both runs, so run 1 repeated it with the default context |

U0 passes for format, playback, recording and frames. The prompt was clearly audible with the phone in hand. The mount and loudness through the mount are still open. Silent-turn hallucination is now a known risk. A local rerun with synthetic quiet noise and a click reproduced "Thank you." without VAD, and with VAD the output was empty. Machine-voice phrases kept the same text with VAD at 0 dB and at -20 dB. In the same rerun, a quiet machine-voice "Help" was heard as "Health." with VAD and without it.
