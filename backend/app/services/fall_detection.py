"""
fall_detection.py

Service xu ly fall detection trong backend:
- load scaler va TFLite model
- giu buffer sensor data theo patient
- chuan hoa window
- infer state hien tai
- ap dung cooldown cho alert FALL
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, Hashable

import time

import numpy as np

logger = logging.getLogger(__name__)


def _sha256(path) -> str:
    """Return the first 12 hex chars of the SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]

from app.config import (
    FALL_CONFIRM_WINDOWS,
    KERAS_MODEL_PATH,
    ML_CONFIDENCE_THRESHOLD,
    ML_INSTABILITY_CONFIDENCE_THRESHOLD,
    NUM_CHANNELS,
    SAMPLING_RATE,
    SCALER_PARAMS_PATH,
    SVM_FALL_THRESHOLD,
    SVM_SWAY_THRESHOLD,
    TFLITE_MODEL_PATH,
    WINDOW_SIZE,
)


ALERT_COOLDOWN_SECONDS = max(10, WINDOW_SIZE // max(1, SAMPLING_RATE))


@dataclass
class PredictionResult:
    state: str
    window_ready: bool
    samples_buffered: int
    fall_probability: float | None = None
    instability_probability: float | None = None
    peak_svm: float | None = None
    alert_created: bool = False
    alert_suppressed: bool = False
    model_source: str = "heuristic"
    inference_latency_ms: float | None = None
    active_threshold: float | None = None   # V.6: threshold thực tế được dùng
    # Which branch of classify_state produced a FALL: "svm_rule", or the model
    # source that scored the window ("keras" | "tflite" | "heuristic").
    decision_source: str | None = None


class FallDetectionService:
    def __init__(self) -> None:
        self._inference_lock = threading.Lock()
        self.buffers: dict[int, Deque[np.ndarray]] = defaultdict(
            lambda: deque(maxlen=WINDOW_SIZE)
        )
        self.last_alert_at: dict[int, datetime] = {}
        self._fall_streak: dict[int, int] = defaultdict(int)
        self.scaler_mean, self.scaler_std = self._load_scaler()
        self.interpreter = self._load_tflite_interpreter()
        self.keras_model = None
        self.input_details = None
        self.output_details = None

        if self.interpreter is not None:
            self.input_details = self.interpreter.get_input_details()
            self.output_details = self.interpreter.get_output_details()
            self.model_source = "tflite"
        else:
            self.keras_model = self._load_keras_model()
            self.model_source = "keras" if self.keras_model is not None else "heuristic"
        self._keras_infer = self._compile_keras(self.keras_model)

    def _load_scaler(self) -> tuple[np.ndarray, np.ndarray]:
        if not SCALER_PARAMS_PATH.exists():
            logger.warning("Scaler not found at %s — using identity scaler", SCALER_PARAMS_PATH)
            return np.zeros(NUM_CHANNELS, dtype=np.float32), np.ones(
                NUM_CHANNELS, dtype=np.float32
            )

        scaler = np.load(SCALER_PARAMS_PATH)
        mean = scaler["mean"].astype(np.float32)
        std = scaler["std"].astype(np.float32)
        std[std == 0] = 1.0
        sha = _sha256(SCALER_PARAMS_PATH)
        logger.info("Scaler loaded: path=%s sha256=%s", SCALER_PARAMS_PATH, sha)
        return mean, std

    def _load_tflite_interpreter(self):
        if not TFLITE_MODEL_PATH.exists():
            logger.warning(
                "TFLite model not found at %s — falling back to heuristic detection",
                TFLITE_MODEL_PATH,
            )
            return None

        try:
            import tensorflow as tf

            interpreter = tf.lite.Interpreter(model_path=str(TFLITE_MODEL_PATH))
            interpreter.allocate_tensors()
            size_kb = TFLITE_MODEL_PATH.stat().st_size // 1024
            sha = _sha256(TFLITE_MODEL_PATH)
            logger.info(
                "TFLite model loaded: path=%s size=%dKB sha256=%s",
                TFLITE_MODEL_PATH,
                size_kb,
                sha,
            )
            return interpreter
        except Exception as exc:
            logger.warning("Failed to load TFLite model: %s — trying Keras fallback", exc)
            return None

    def _load_keras_model(self):
        if not KERAS_MODEL_PATH.exists():
            logger.warning("Keras model not found at %s — using heuristic", KERAS_MODEL_PATH)
            return None
        try:
            import tensorflow as tf
            model = tf.keras.models.load_model(KERAS_MODEL_PATH, compile=False)
            sha = _sha256(KERAS_MODEL_PATH)
            logger.info("Keras model loaded: path=%s sha256=%s", KERAS_MODEL_PATH, sha)
            return model
        except Exception as exc:
            logger.warning("Failed to load Keras model: %s — using heuristic", exc)
            return None

    @staticmethod
    def _compile_keras(model):
        """Eager calls cost ~112 ms per window, over the 20 ms budget at 50 Hz; a traced call costs ~1.5 ms."""
        if model is None:
            return None
        import tensorflow as tf

        return tf.function(
            lambda x: model(x, training=False),
            input_signature=[tf.TensorSpec([1, WINDOW_SIZE, NUM_CHANNELS], tf.float32)],
            autograph=False,
        )

    def add_sample(
        self,
        patient_id: Hashable,
        sample: np.ndarray,
        event_time: datetime | None = None,
        fall_threshold: float | None = None,   # V.6: adaptive threshold
    ) -> PredictionResult:
        if sample.shape != (NUM_CHANNELS,):
            raise ValueError(
                f"Sensor sample phai co shape ({NUM_CHANNELS},), nhan duoc {sample.shape}"
            )

        buffer = self.buffers[patient_id]
        buffer.append(sample.astype(np.float32))

        if len(buffer) < WINDOW_SIZE:
            return PredictionResult(
                state="BUFFERING",
                window_ready=False,
                samples_buffered=len(buffer),
                model_source=self.model_source,
            )

        window = np.stack(buffer, axis=0).astype(np.float32)
        peak_svm = float(np.max(window[:, 6]))
        _t0_ns = time.perf_counter_ns()
        fall_probability, instability_probability = self.predict_fall_probability(window)
        inference_latency_ms = (time.perf_counter_ns() - _t0_ns) / 1_000_000
        active_threshold = fall_threshold if fall_threshold is not None else ML_CONFIDENCE_THRESHOLD
        raw_state = self.classify_state(fall_probability, instability_probability, peak_svm, active_threshold)

        # Sustained prediction: require FALL_CONFIRM_WINDOWS consecutive FALL predictions
        # (one per sample, i.e. FALL_CONFIRM_SECONDS of sustained FALL) before
        # triggering an alert. Resets streak on any non-FALL window.
        if raw_state == "FALL":
            self._fall_streak[patient_id] += 1
        else:
            self._fall_streak[patient_id] = 0

        if raw_state == "FALL" and self._fall_streak[patient_id] < FALL_CONFIRM_WINDOWS:
            state = "NORMAL"
        else:
            state = raw_state

        alert_created = False
        alert_suppressed = False
        if state in ("FALL", "VESTIBULAR_INSTABILITY"):
            alert_created, alert_suppressed = self._consume_alert_slot(
                patient_id,
                event_time or datetime.now(timezone.utc),
            )

        return PredictionResult(
            state=state,
            window_ready=True,
            samples_buffered=len(buffer),
            fall_probability=fall_probability,
            instability_probability=instability_probability,
            peak_svm=peak_svm,
            alert_created=alert_created,
            alert_suppressed=alert_suppressed,
            model_source=self.model_source,
            inference_latency_ms=inference_latency_ms,
            active_threshold=active_threshold,
            decision_source=self.fall_decision_source(peak_svm) if raw_state == "FALL" else None,
        )

    def classify_state(
        self,
        fall_probability: float,
        instability_probability: float,
        peak_svm: float,
        fall_threshold: float | None = None,   # V.6: None → dùng config mặc định
    ) -> str:
        # Hard SVM safety fallback — catches extreme high-impact events regardless of ML.
        if peak_svm >= SVM_FALL_THRESHOLD:
            return "FALL"
        # V.6: dùng adaptive threshold nếu có, ngược lại dùng config mặc định
        threshold = fall_threshold if fall_threshold is not None else ML_CONFIDENCE_THRESHOLD
        if fall_probability >= threshold:
            return "FALL"
        if instability_probability >= ML_INSTABILITY_CONFIDENCE_THRESHOLD:
            return "VESTIBULAR_INSTABILITY"
        return "NORMAL"

    def fall_decision_source(self, peak_svm: float) -> str:
        """Mirrors classify_state's order: the SVM rule is checked before any probability."""
        return "svm_rule" if peak_svm >= SVM_FALL_THRESHOLD else self.model_source

    def predict_fall_probability(self, window: np.ndarray) -> tuple[float, float]:
        """Returns (fall_probability, instability_probability).

        For 3-class model output [NORMAL, INSTABILITY, FALL]:
          fall_probability        = output[2]
          instability_probability = output[1]
        Falls back to heuristic when TFLite model is unavailable.
        """
        normalized = (window - self.scaler_mean) / self.scaler_std
        normalized = normalized.astype(np.float32)

        if self.interpreter is None or self.input_details is None or self.output_details is None:
            # Try Keras model first
            if self._keras_infer is not None:
                x = normalized.reshape(1, WINDOW_SIZE, NUM_CHANNELS).astype(np.float32)
                with self._inference_lock:
                    output = self._keras_infer(x).numpy().reshape(-1)
                if output.size >= 3:
                    return float(output[2]), float(output[1])
            # Final fallback: heuristic SVM-based estimate
            peak_svm = float(np.max(window[:, 6]))
            fall_prob = min(1.0, peak_svm / max(SVM_FALL_THRESHOLD, 1e-6))
            instab_prob = 0.0
            if SVM_SWAY_THRESHOLD <= peak_svm < SVM_FALL_THRESHOLD:
                instab_prob = min(
                    1.0,
                    (peak_svm - SVM_SWAY_THRESHOLD)
                    / max(SVM_FALL_THRESHOLD - SVM_SWAY_THRESHOLD, 1e-6),
                )
            return fall_prob, instab_prob

        input_info = self.input_details[0]
        output_info = self.output_details[0]
        model_input = normalized.reshape(1, WINDOW_SIZE, NUM_CHANNELS)

        with self._inference_lock:
            if input_info["dtype"] == np.int8:
                scale, zero_point = input_info["quantization"]
                if scale == 0:
                    quantized = model_input.astype(np.int8)
                else:
                    quantized = np.round(model_input / scale + zero_point).astype(np.int8)
                self.interpreter.set_tensor(input_info["index"], quantized)
            else:
                self.interpreter.set_tensor(input_info["index"], model_input.astype(input_info["dtype"]))

            self.interpreter.invoke()
            output = self.interpreter.get_tensor(output_info["index"]).copy()

        if output_info["dtype"] == np.int8:
            scale, zero_point = output_info["quantization"]
            if scale != 0:
                output = (output.astype(np.float32) - zero_point) * scale
            else:
                output = output.astype(np.float32)
        else:
            output = output.astype(np.float32)

        output = output.reshape(-1)
        if output.size >= 3:
            # 3-class: [NORMAL(0), INSTABILITY(1), FALL(2)]
            return float(output[2]), float(output[1])
        elif output.size == 2:
            # Legacy 2-class: [NORMAL(0), FALL(1)]
            return float(output[1]), 0.0
        else:
            return float(output[0]), 0.0

    def _consume_alert_slot(
        self,
        patient_id: int,
        event_time: datetime,
    ) -> tuple[bool, bool]:
        previous = self.last_alert_at.get(patient_id)
        if previous is not None:
            if self._to_utc(event_time) - self._to_utc(previous) < timedelta(
                seconds=ALERT_COOLDOWN_SECONDS
            ):
                return False, True

        self.last_alert_at[patient_id] = self._to_utc(event_time)
        return True, False

    def reset_patient(self, patient_id: Hashable) -> None:
        """Forget everything this service remembers about one patient.

        One call covering the sample buffer, the sustained-FALL streak and the
        alert cooldown, so callers that need a clean slate cannot reset two of the
        three. Test fixtures were clearing `buffers` and `last_alert_at` but not
        `_fall_streak`, which is private and was added later -- streaks therefore
        leaked between tests and made some of them pass on accumulated state
        rather than on what they actually posted.
        """

        self.buffers.pop(patient_id, None)
        self.last_alert_at.pop(patient_id, None)
        self._fall_streak.pop(patient_id, None)

    def rollback_last_samples(self, patient_id: int, count: int) -> None:
        """Remove the last `count` samples from the patient's buffer.

        Called when a DB commit fails so the in-memory buffer does not drift
        ahead of what is actually persisted.
        """
        buf = self.buffers.get(patient_id)
        if buf is None:
            return
        for _ in range(min(count, len(buf))):
            buf.pop()
        self._fall_streak[patient_id] = 0

    def rollback_alert_slot(
        self, patient_id: int, previous: datetime | None
    ) -> None:
        """Restore last_alert_at to the value it had before a failed commit.

        Prevents the cooldown from being permanently consumed when no alert
        row was actually written to the database.
        """
        if previous is None:
            self.last_alert_at.pop(patient_id, None)
        else:
            self.last_alert_at[patient_id] = previous

    @staticmethod
    def _to_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


fall_detection_service = FallDetectionService()
