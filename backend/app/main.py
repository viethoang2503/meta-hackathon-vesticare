"""
main.py

Diem khoi dong (Entry point) cua ung dung FastAPI.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

from app.api.alerts import router as alerts_router
from app.api.forecast import router as forecast_router
from app.api.sensor import router as sensor_router
from app.api.spatial import router as spatial_router
from app.api.websocket import router as stream_router
from app.config import (
    CORS_ORIGINS,
    ML_CONFIDENCE_THRESHOLD,
    SAMPLING_RATE,
    SCALER_PARAMS_PATH,
    SVM_FALL_THRESHOLD,
    SVM_SWAY_THRESHOLD,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TFLITE_MODEL_PATH,
    WINDOW_SIZE,
)
from app.database import init_db
from app.services.broadcaster import broadcaster
from app.services.spatial_runs import spatial_runs

_startup_logger = logging.getLogger("vesti_care.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _startup_logger.info("========================================")
    _startup_logger.info("  Vesti-Care Backend starting up")
    _startup_logger.info("========================================")

    # Runtime configuration snapshot
    _startup_logger.info("--- Sensor config ---")
    _startup_logger.info("  SAMPLING_RATE       = %d Hz", SAMPLING_RATE)
    _startup_logger.info("  WINDOW_SIZE         = %d samples (%.1f s)", WINDOW_SIZE, WINDOW_SIZE / SAMPLING_RATE)

    _startup_logger.info("--- Detection thresholds ---")
    _startup_logger.info("  SVM_FALL_THRESHOLD  = %.2f g", SVM_FALL_THRESHOLD)
    _startup_logger.info("  SVM_SWAY_THRESHOLD  = %.2f g", SVM_SWAY_THRESHOLD)
    _startup_logger.info("  ML_CONFIDENCE       = %.0f%%", ML_CONFIDENCE_THRESHOLD * 100)

    _startup_logger.info("--- Model artifacts ---")
    _startup_logger.info("  TFLite model  : %s [%s]", TFLITE_MODEL_PATH, "found" if TFLITE_MODEL_PATH.exists() else "MISSING — heuristic fallback active")
    _startup_logger.info("  Scaler params : %s [%s]", SCALER_PARAMS_PATH, "found" if SCALER_PARAMS_PATH.exists() else "MISSING — identity scaler active")

    _startup_logger.info("--- Notifications ---")
    telegram_status = "enabled" if (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) else "disabled (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)"
    _startup_logger.info("  Telegram      : %s", telegram_status)

    _startup_logger.info("========================================")

    init_db()
    interrupted = spatial_runs.mark_interrupted_runs()
    if interrupted:
        _startup_logger.info("  Spatial runs marked Interrupted after restart: %d", interrupted)
    broadcaster.set_loop(asyncio.get_running_loop())

    yield


app = FastAPI(
    title="Vesti-Care API",
    description="Backend API cho he thong phat hien nga va chong mat Vesti-Care",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sensor_router)
app.include_router(alerts_router)
app.include_router(stream_router)
app.include_router(forecast_router)
app.include_router(spatial_router)


@app.get("/api/health", tags=["System"])
def health_check():
    return {
        "status": "online",
        "service": "Vesti-Care Backend",
        "message": "He thong dang hoat dong binh thuong!",
    }


ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
app.mount("/spatial-assets", StaticFiles(directory=ASSETS_DIR), name="spatial-assets")

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "dashboard"
app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
