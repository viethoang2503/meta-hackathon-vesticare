# G4. Hardware gate

G4 in [MVP2.md](../../../MVP2.md): the ESP32 at the waist must give clean data before any result from it counts. `backend/tools/g4_report.py` builds the table from one live session and writes `<session>.json` and `<session>.md` here.

## Record

You need the ESP32 worn at the waist, a phone filming the whole run with its clock visible, a thick mattress on a flat floor, and a person standing by.

1. In `backend/.env` and `vesticare-sensor/include/config.h`, set the same `DEVICE_TOKEN`. In `config.h`, also set the 2.4 GHz Wi-Fi, `BACKEND_URL=http://<laptop IP>:8010` and `FIRMWARE_VERSION "2.0.0"`.
2. Start the backend on the LAN: `cd backend && ../.verify/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010`.
3. Flash and watch the log: `cd vesticare-sensor && pio run -e esp32dev -t upload -t monitor`. Wait for `Clock sync HTTP 200`.
4. Open the session: `curl -X POST http://127.0.0.1:8010/api/spatial/live -H 'Content-Type: application/json' -d '{"patient_id": 1, "device_id": "<DEVICE_ID from config.h>"}'`. Keep the returned `id`.
5. Do the run and note each start and end time in session seconds:
   - Stand still for 30 s.
   - Walk normally for 5 minutes.
   - Make 3 controlled falls onto the mattress, then lie still for 10 s after each one.
   - Do at least 10 everyday trials: sit down, stand up, bend to pick something up, lie down on purpose.
6. Stop the session: `curl -X POST http://127.0.0.1:8010/api/spatial/sessions/<id>/stop`.

## Report

```
.verify/venv/bin/python backend/tools/g4_report.py <id> --rest 2:32 \
    --fall 340:355 --fall 370:385 --fall 400:415 --adl 430:440 --adl 445:455 ...
```

Session seconds come from the laptop snapshot, `source_offset_ms`, or from the video lined up with the first sample. The rest row also prints which axis carries gravity next to the axis in the training data, because the model is sensitive to orientation ([GATES.md](../../../GATES.md), G1).

Do not change thresholds after reading the result to make the gate pass. Fix the cause, which can be acquisition, axes, units, placement or transport, then record again. Publish the failing runs too. A session streamed by `tools/fake_esp32.py` is reported as "Simulated: not G4 evidence".
