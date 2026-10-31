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
