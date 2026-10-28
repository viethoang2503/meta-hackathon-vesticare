"""
config.py

Cau hinh trung tam cho toan bo backend.
Tat ca cac thong so deu tap trung tai day de de quan ly.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load bien moi truong tu file .env
load_dotenv()

# ===========================================================================
# DUONG DAN
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # VERTI-GO ALERT PROJECT/
BACKEND_DIR = Path(__file__).resolve().parent.parent           # backend/
ML_DIR = PROJECT_ROOT / "ml"

# Database
DATABASE_PATH = BACKEND_DIR / "database.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# ML Model (3-class: NORMAL / VESTIBULAR_INSTABILITY / FALL)
# CNN+BiLSTM+Attention: recall-FALL=0.876, FP=1684 (vs 1D-CNN recall=0.849, FP=3039)
TFLITE_MODEL_PATH  = ML_DIR / "models_3class" / "fall_detection_3class_cnn_bilstm_attn_fp16.tflite"
KERAS_MODEL_PATH   = ML_DIR / "models_3class" / "fall_detection_3class_cnn_bilstm_attn.keras"
SCALER_PARAMS_PATH = ML_DIR / "models_3class" / "scaler_params.npz"


# ===========================================================================
# NGUONG PHAT HIEN
# ===========================================================================

# Nguong SVM (Signal Vector Magnitude) - don vi G
SVM_FALL_THRESHOLD = 2.5       # SVM > 2.5G -> nghi nga
SVM_SWAY_THRESHOLD = 1.5       # SVM 1.5-2.5G -> lao dao
SVM_NORMAL_MAX = 1.5           # SVM < 1.5G -> binh thuong

# Nguong confidence cua ML model
# T=0.55: CNN+BiLSTM+Attn+KFall threshold sweep → recall=0.884, prec=0.713, FP=1855 (optimal)
# baseline_comparison.py dung T=0.45 cho fair comparison giua cac model
ML_CONFIDENCE_THRESHOLD = 0.55            # P(FALL) >= threshold -> FALL alert (no SVM assist)
ML_INSTABILITY_CONFIDENCE_THRESHOLD = 0.5 # P(INSTABILITY) >= 0.5 -> VESTIBULAR_INSTABILITY alert


# ===========================================================================
# CAM BIEN
# ===========================================================================

SAMPLING_RATE = 50             # Hz (50 mau/giay)
WINDOW_SIZE = 100              # 100 mau = 2 giay (khop voi model da train)
NUM_CHANNELS = 7               # ax, ay, az, gx, gy, gz, svm

# Sustained prediction: FALL phai duoc du doan lien tuc trong FALL_CONFIRM_SECONDS
# truoc khi phat alert. Model chay lai o MOI mau moi (cua so truot tung mau), nen
# so window lien tiep = so mau lien tiep: 0.5 s = 25 window o 50 Hz.
# Luu y: voi nguong SVM cung, 1 spike nam trong buffer 100 mau suot 2 s, nen gate
# nay chi lam tre alert 0.5 s chu khong loc duoc spike do; no loc dao dong ngan
# cua xac suat ML. Nen tune lai sau khi train model moi.
FALL_CONFIRM_SECONDS = 0.5
FALL_CONFIRM_WINDOWS = max(1, int(FALL_CONFIRM_SECONDS * SAMPLING_RATE))


# ===========================================================================
# SERVER
# ===========================================================================

API_HOST = "0.0.0.0"
API_PORT = 8000
API_PREFIX = "/api"

# CORS - cho phep Dashboard ket noi tu trinh duyet
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8080",
    "*",  # Cho phep tat ca origin (dev mode)
]


# ===========================================================================
# TELEGRAM BOT
# ===========================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ===========================================================================
# DEVICE AUTHENTICATION
# ===========================================================================

# Token xac thuc thiet bi phần cứng. Set trong .env.
# Neu de trong ("") thi bo qua kiem tra token — cho phep simulator hoat dong.
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "")


# ===========================================================================
# DU BAO THOI TIET (Sprint 5)
# ===========================================================================

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
OPENWEATHER_CITY = os.getenv("OPENWEATHER_CITY", "Hanoi")
