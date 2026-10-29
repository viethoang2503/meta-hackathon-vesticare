# Vesti-Care — Meta Hackathon

A wearable IoT system combining AI and sensor data to detect falls and instability events for patients with vestibular disorders. Alerts are sent in real-time via Telegram and logged in a dashboard for review and feedback.

## System Overview

```
Simulator / ESP32 firmware
        │
        ▼  HTTP POST /api/sensor/data
   FastAPI Backend  ─── SQLite DB
        │
        ├── IMU preprocessing + TFLite inference
        ├── Alert creation + Telegram notification
        └── SSE stream (/api/stream)
                │
                ▼
           Dashboard (browser)
```

## Project Structure

```
vesticare-sensor/  ESP32 + MPU-6050 firmware (PlatformIO)
backend/           FastAPI server, database, simulator
dashboard/         Web frontend (HTML/CSS/JS, open directly in browser)
ml/                IMU ML training pipeline
```

## Prerequisites

- Python 3.12
- pip
- A `.env` file in `backend/` (see [Environment Variables](#environment-variables))
- (Optional) Telegram bot token and chat ID for notifications

## Quick Start

### 1. Clone and set up the environment

```bash
git clone <repo-url>
cd vesticare-metahackathon

python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` and fill in your values (see [Environment Variables](#environment-variables)).

### 3. Start the backend

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`.  
Interactive API docs: `http://localhost:8000/docs`

### 4. Open the dashboard

Open `http://localhost:8000/` after starting the backend. No frontend build
step is required. Serving the dashboard from localhost also enables the USB
Web Serial mode in Chrome and Edge.

### 5. Run the simulator

In a separate terminal (with the venv active):

```bash
cd backend

# Stream normal activity
python simulator/run_scenario.py --scenario normal

# Simulate a single fall event
python simulator/run_scenario.py --scenario one_fall

# Simulate multiple fall events (tests cooldown suppression)
python simulator/run_scenario.py --scenario multi_fall

# Run all scenarios and save results
python simulator/run_scenario.py --all --output results.json
```

## Environment Variables

Create `backend/.env` based on this template:

```env
# Telegram notifications (optional — alerts still work without this)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# OpenWeather (optional — forecast feature only)
OPENWEATHER_API_KEY=
OPENWEATHER_CITY=Hanoi
```

To get a Telegram bot token: message [@BotFather](https://t.me/BotFather) on Telegram.  
To get a chat ID: message [@userinfobot](https://t.me/userinfobot).

If these variables are not set, the system runs normally — Telegram notifications are skipped and logged as `not_configured`.

## Running Tests

```bash
cd backend
pytest tests/ -v
```

## API Reference

Interactive API docs are available at `http://localhost:8000/docs` when the backend is running.

Key endpoints:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/sensor/data` | Ingest a single IMU sample |
| `POST` | `/api/sensor/batch` | Ingest a batch of IMU samples |
| `GET` | `/api/alerts/recent` | List recent alerts |
| `GET` | `/api/alerts/stats` | Alert statistics |
| `POST` | `/api/alerts/acknowledge/{id}` | Acknowledge an alert |
| `POST` | `/api/alerts/feedback/{id}` | Mark alert as valid or false |
| `GET` | `/api/stream` | Real-time stream (Server-Sent Events) |

## Model

The backend attempts to load the CNN+BiLSTM+Attention (CBA) TFLite model at startup, then falls back to a rule-based SVM heuristic if loading fails (e.g. missing TFLite runtime on Windows). Trained models live in `ml/model_3class/`. The folder currently holds the Keras models and `scaler_params.npz` but no TFLite export, so the backend loads `fall_detection_3class_cnn_bilstm_attn.keras` through TensorFlow:
- `fall_detection_3class.tflite` — FP32 production model
- `fall_detection_3class_cnn_bilstm_attn_fp16.tflite` — FP16 quantized (5.7× smaller, negligible expected accuracy loss; TFLite runtime not verified on Windows)

Detection logic:
- Window size: 100 samples (2 seconds at 50 Hz)
- Channels: `ax, ay, az, gx, gy, gz, svm` (7 channels)
- FALL alert if: peak SVM ≥ 2.5 g (hard fallback), OR ML fall probability ≥ 55%
- VESTIBULAR_INSTABILITY alert if: ML instability probability ≥ 50%
- Sustained confirmation: FALL must be predicted continuously for 0.5 s (25 samples) before alerting
- Cooldown: 10 seconds between alerts per patient to suppress duplicates

## Hardware (Optional)

ESP32 + MPU-6050 firmware is maintained as a separate PlatformIO project (`vesticare-sensor`). The hardware path is optional — the simulator covers all demo scenarios.

The live page supports two manually selected hardware transports:

- **Wi-Fi:** firmware sends 10-sample batches directly to the backend every
  approximately 200 ms.
- **USB Serial:** enter a positive Patient ID, select `USB Serial`, optionally
  enter the backend device token for the current page session, click
  `Connect USB`, and grant access to the ESP32 port. Chrome or Edge is required.

Both transports store samples and run the same backend fall-detection and alert
pipeline. Switch back to Wi-Fi before unplugging when possible. If the browser
or cable disappears in USB mode, the firmware returns to Wi-Fi after six
seconds without a heartbeat.
