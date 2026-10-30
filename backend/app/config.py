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
SCENARIOS_DIR = PROJECT_ROOT / "assets" / "scenarios"

# Database
DATABASE_PATH = BACKEND_DIR / "database.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# ML Model (3-class: NORMAL / VESTIBULAR_INSTABILITY / FALL)
# CNN+BiLSTM+Attention: recall-FALL=0.876, FP=1684 (vs 1D-CNN recall=0.849, FP=3039)
TFLITE_MODEL_PATH  = ML_DIR / "model_3class" / "fall_detection_3class_cnn_bilstm_attn_fp16.tflite"
KERAS_MODEL_PATH   = ML_DIR / "model_3class" / "fall_detection_3class_cnn_bilstm_attn.keras"
SCALER_PARAMS_PATH = ML_DIR / "model_3class" / "scaler_params.npz"


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


# ===========================================================================
# SPATIAL REPLAY (docs/spatial/SPEC.md, bang tham so)
# ===========================================================================

SPATIAL_RUN_TIMEOUT_SECONDS = 180
SPATIAL_FALL_MERGE_SECONDS = 10

# Mot FALL da xac nhan chi mo incident neu sau do nguoi deo nam yen.
# Muc "Trung binh" do nguoi dung chon: TRAIN recall 0.998, TEST recall 0.993 va bao nham 55,5%
# file hoat dong thuong ngay (ml/scripts/tune_stillness.py; docs/spatial/GATES.md).
FALL_STILLNESS_SECONDS = 2.0
FALL_STILLNESS_WINDOW_SECONDS = 10.0
FALL_STILLNESS_SVM_STD_MAX = 0.05
FALL_STILLNESS_GYRO_MEAN_MAX = 10.0

# Policy B: HUD dem nguoc truoc khi bao nguoi cham soc; nguoi deo bam "I'm OK" de huy.
WEARER_CANCEL_WINDOW_SECONDS = 7

# Bang chung cua incident (docs/spatial/SPEC.md, bang tham so)
FRAME_ALIGNMENT_TOLERANCE_MS = 250
IMU_DETAIL_MAX_SECONDS = 10

# Phan tich SAM cho incident (docs/spatial/SPEC.md)
MODEL_API_BASE = "https://api.meta.ai/v1"
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
SAM_MODEL = "sam-3.1"
ANALYSIS_TIMEOUT_SECONDS = 45

# Agent giai thich incident (docs/spatial/SPEC.md: max_tool_calls, analysis_timeout_seconds)
AGENT_MODEL = "muse-spark-1.3"
AGENT_MAX_TOOL_CALLS = 5
# Reasoning tokens count against this; 1200 cut the final JSON short on the first real run.
AGENT_MAX_TOKENS = 4000
FIXES_LIBRARY_PATH = PROJECT_ROOT / "assets" / "fixes" / "library.json"

# Ngay 7: loi nhac theo zone va nguoi cham soc (docs/spatial/SPEC.md, bang tham so)
ADVISORY_COOLDOWN_SECONDS = 60
CAREGIVER_ACK_TIMEOUT_SECONDS = 20
