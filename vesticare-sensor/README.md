# vesticare-sensor

ESP32 + MPU-6050 firmware that streams IMU data to the verti-go-alert backend.

## Setup

1. `cp include/config.h.example include/config.h`
2. Edit `include/config.h`: set `WIFI_SSID`, `WIFI_PASSWORD`, `BACKEND_URL`
   (e.g. `http://192.168.1.100:8000`), `DEVICE_TOKEN` (must match the
   backend's `DEVICE_TOKEN` env var, or leave empty if that's unset),
   `PATIENT_ID`, `DEVICE_ID`.
3. Wiring: MPU-6050 SDA -> ESP32 GPIO21, SCL -> GPIO22, VCC -> 3.3V, GND -> GND.

The sensor samples at 50 Hz. Acceleration is emitted in `g`, angular velocity
in `deg/s` (matching the training datasets and the simulator), and Wi-Fi
uploads use batches of 10 samples.

## Build & test

- Native unit tests (no hardware needed): `pio test -e native`
- Build for the board: `pio run -e esp32dev`
- Flash + monitor: `pio run -e esp32dev -t upload -t monitor`

## Wi-Fi and USB modes

The firmware starts in Wi-Fi mode. It also prints one six-axis JSON sample per
line at 115200 baud:

```json
{"ax":0.0123,"ay":-0.021,"az":0.9981,"gx":0.0012,"gy":0.0041,"gz":-0.002}
```

The dashboard at `http://localhost:8000/` can relay this stream through the
backend using Chrome or Edge Web Serial. Select `USB Serial`, enter a positive
Patient ID, then click `Connect USB` and choose the ESP32 port. If the backend
uses `DEVICE_TOKEN`, enter the same value in the optional token field; it is
kept only in memory for the current page session.

The line-oriented firmware commands are:

- `MODE USB` — stop direct Wi-Fi upload and use the browser relay.
- `MODE WIFI` — resume direct Wi-Fi upload.
- `PING` — refresh the USB watchdog.

The dashboard sends `PING` every two seconds. After six seconds without a
heartbeat, the firmware discards its partial batch and automatically returns
to Wi-Fi. Switching modes also discards the partial batch so the backend never
receives the same sample over both transports.

## Payload contract

See `docs/superpowers/specs/2026-08-15-esp32-firmware-rebuild-design.md` and
`docs/architecture.md` §7 in the main repo for the locked payload contract
this firmware must not deviate from.
