"""
M1.2 - Experiment Runner
Config-driven training and evaluation. Saves all artifacts reproducibly.

Usage:
    python -m ml.experiments.runner --config ml/experiments/configs/exp_cnn_3class.yaml
    python -m ml.experiments.runner --config ml/experiments/configs/exp_lstm_3class.yaml
    python -m ml.experiments.runner --config ml/experiments/configs/exp_rf_3class.yaml
    python -m ml.experiments.runner --config ml/experiments/configs/exp_cnn_3class.yaml --eval-only
    python -m ml.experiments.runner --config ml/experiments/configs/exp_cnn_3class.yaml --overwrite

Output: artifacts/experiments/exp_YYYYMMDD_<name>_subject_independent_w<window>/
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "ml"))

from experiments.metrics import (
    CLASS_NAMES,
    aggregate_repeated_runs,
    compute_metrics,
    measure_inference_time,
    plot_confusion_matrix,
    plot_training_history,
    save_metrics,
    save_predictions,
)
from experiments.data_quality import run as run_data_quality


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:
        # Fallback: basic YAML parser for simple configs
        import re
        result = {}
        current = result
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or not line.strip():
                continue
            m = re.match(r"^(\w[\w_]*):\s*(.*)", line)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if val:
                    try:
                        current[key] = json.loads(val)
                    except Exception:
                        current[key] = val
        return result


def _get_environment() -> dict:
    def run(cmd):
        try:
            return subprocess.check_output(cmd, cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            return "unknown"

    env = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "git_commit": run(["git", "rev-parse", "HEAD"]),
        "git_commit_short": run(["git", "rev-parse", "--short", "HEAD"]),
        "git_branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "packages": {},
    }
    for pkg in ["tensorflow", "numpy", "scikit-learn"]:
        try:
            import importlib.metadata
            env["packages"][pkg] = importlib.metadata.version(pkg)
        except Exception:
            env["packages"][pkg] = "unknown"
    return env


def _make_output_dir(config: dict, overwrite: bool = False) -> Path:
    exp_name = config.get("experiment", {}).get("name", "unnamed")
    date_str = datetime.now().strftime("%Y%m%d")
    window = config.get("signal", {}).get("window_size", 100)
    dir_name = f"exp_{date_str}_{exp_name}_subject_independent_w{window}"
    out_dir = ROOT / "artifacts" / "experiments" / dir_name

    if out_dir.exists() and not overwrite:
        print(f"\n  ERROR: Output directory already exists: {out_dir.relative_to(ROOT)}")
        print("  Use --overwrite to replace, or rename the config to create a new experiment.")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _load_data(config: dict) -> tuple:
    data_dir = ROOT / config.get("dataset", {}).get("data_dir", "ml/data/processed_3class")
    print(f"  Loading data from: {data_dir.relative_to(ROOT)}")
    X_train = np.load(data_dir / "X_train.npz")["data"]
    X_test  = np.load(data_dir / "X_test.npz")["data"]
    y_train = np.load(data_dir / "y_train.npz")["data"].astype(np.int32)
    y_test  = np.load(data_dir / "y_test.npz")["data"].astype(np.int32)
    print(f"  X_train: {X_train.shape}  X_test: {X_test.shape}")
    for i, name in enumerate(CLASS_NAMES):
        print(f"    Train {name}: {(y_train == i).sum()}  Test: {(y_test == i).sum()}")
    return X_train, X_test, y_train, y_test


def _normalize(X_train, X_test, models_dir: Path):
    print("  Normalizing (channel-wise StandardScaler)...")
    mean = X_train.mean(axis=(0, 1))
    std  = X_train.std(axis=(0, 1))
    std[std == 0] = 1.0
    np.savez(models_dir / "scaler_params.npz", mean=mean, std=std)
    return (X_train - mean) / std, (X_test - mean) / std


def _augment(X_train, y_train, config: dict):
    aug_cfg = config.get("training", {}).get("augmentation", {})
    if not aug_cfg.get("enabled", True):
        return X_train, y_train

    print("  Augmenting minority classes...")
    normal_count = int((y_train == 0).sum())
    aug_X = [X_train]
    aug_y = [y_train]
    jitter_std = aug_cfg.get("jitter_std", 0.05)
    scale_range = aug_cfg.get("scale_range", [0.85, 1.15])
    shift_range = aug_cfg.get("shift_range", [-3, 3])

    for cls in [1, 2]:
        mask = (y_train == cls)
        X_cls = X_train[mask]
        count = len(X_cls)
        if count == 0:
            continue
        repeats = max(1, (normal_count // count) - 1)
        print(f"    {CLASS_NAMES[cls]}: {count} → ×{repeats + 1}")
        for _ in range(repeats):
            X_new = X_cls.copy()
            choice = np.random.randint(0, 3, size=len(X_new))
            jm = (choice == 0)
            if jm.any():
                X_new[jm] += np.random.normal(0, jitter_std, X_new[jm].shape)
            sm = (choice == 1)
            if sm.any():
                scale = np.random.uniform(scale_range[0], scale_range[1], (sm.sum(), 1, 1))
                X_new[sm] *= scale
            tm = (choice == 2)
            if tm.any():
                shift = np.random.randint(shift_range[0], shift_range[1] + 1)
                X_new[tm] = np.roll(X_new[tm], shift, axis=1)
            aug_X.append(X_new)
            aug_y.append(np.full(len(X_new), cls, dtype=np.int32))

    X_aug = np.concatenate(aug_X, axis=0)
    y_aug = np.concatenate(aug_y, axis=0)
    perm = np.random.permutation(len(X_aug))
    return X_aug[perm], y_aug[perm]


def _extract_features(X: np.ndarray) -> np.ndarray:
    feats = []
    for w in X:
        ax, ay, az = w[:, 0], w[:, 1], w[:, 2]
        gx, gy, gz = w[:, 3], w[:, 4], w[:, 5]
        svm = np.sqrt(ax**2 + ay**2 + az**2)
        gyro_mag = np.sqrt(gx**2 + gy**2 + gz**2)
        jerk = np.abs(np.diff(svm))
        tilt = np.arctan2(ay, az)
        sway_freq_power = float(np.mean(np.abs(np.fft.rfft(svm)[1:5]) ** 2))
        feats.append([
            svm.mean(), svm.max(), svm.min(), svm.std(), svm.max() - svm.min(),
            ax.mean(), ay.mean(), az.mean(), ax.std(), ay.std(), az.std(),
            gyro_mag.mean(), gyro_mag.max(), gyro_mag.std(),
            jerk.mean(), jerk.max(), tilt.mean(), tilt.std(), sway_freq_power,
        ])
    return np.array(feats)


# ── Model trainers ────────────────────────────────────────────

def _train_rf(X_train, y_train, X_test, y_test, config: dict, seed: int, models_dir: Path):
    from sklearn.ensemble import RandomForestClassifier

    cfg = config.get("model", {})
    print(f"  Training Random Forest (seed={seed})...")
    X_tr_f = _extract_features(X_train)
    X_te_f = _extract_features(X_test)

    rf = RandomForestClassifier(
        n_estimators=cfg.get("n_estimators", 200),
        max_depth=cfg.get("max_depth", 20),
        min_samples_leaf=cfg.get("min_samples_leaf", 5),
        class_weight=cfg.get("class_weight", "balanced"),
        random_state=seed,
        n_jobs=-1,
    )
    t0 = time.time()
    rf.fit(X_tr_f, y_train)
    train_time = time.time() - t0

    y_pred = rf.predict(X_te_f)
    metrics = compute_metrics(y_test, y_pred)

    out_path = models_dir / "random_forest_3class.pkl"
    with open(out_path, "wb") as f:
        pickle.dump(rf, f)
    size_kb = out_path.stat().st_size / 1024

    timing = measure_inference_time(
        lambda x: rf.predict(_extract_features(x)), X_test, n=200
    )

    return {
        "metrics": metrics,
        "y_pred": y_pred,
        "timing": timing,
        "size_kb": size_kb,
        "train_time_s": round(train_time, 1),
        "history": None,
    }


def _train_cnn(X_train, y_train, X_test, y_test, config: dict, seed: int, models_dir: Path):
    import tensorflow as tf
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    from tensorflow.keras.layers import (BatchNormalization, Conv1D, Dense,
                                          Dropout, GlobalAveragePooling1D,
                                          MaxPooling1D)
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.utils import to_categorical
    from sklearn.utils.class_weight import compute_class_weight

    tf.random.set_seed(seed)
    np.random.seed(seed)

    tcfg = config.get("training", {})
    window_size = config.get("signal", {}).get("window_size", 100)
    num_channels = config.get("signal", {}).get("num_channels", 7)

    y_tr_cat = to_categorical(y_train, 3)
    y_te_cat = to_categorical(y_test, 3)

    model = Sequential([
        Conv1D(16, 5, activation="relu", padding="same", input_shape=(window_size, num_channels)),
        BatchNormalization(), MaxPooling1D(2), Dropout(0.2),
        Conv1D(32, 3, activation="relu", padding="same"),
        BatchNormalization(), MaxPooling1D(2), Dropout(0.2),
        Conv1D(32, 3, activation="relu", padding="same"),
        BatchNormalization(), GlobalAveragePooling1D(),
        Dense(32, activation="relu"), Dropout(0.4),
        Dense(3, activation="softmax"),
    ])
    model.compile(
        optimizer=tf.keras.optimizers.Adam(tcfg.get("learning_rate", 0.001)),
        loss="categorical_crossentropy", metrics=["accuracy"],
    )

    cw_vals = compute_class_weight("balanced", classes=np.unique(y_train), y=y_train)
    callbacks = [
        EarlyStopping(monitor="val_loss", patience=10, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=4, min_lr=1e-6, verbose=0),
    ]

    print(f"  Training CNN (seed={seed}, epochs≤{tcfg.get('epochs', 50)})...")
    t0 = time.time()
    history = model.fit(
        X_train, y_tr_cat,
        epochs=tcfg.get("epochs", 50),
        batch_size=tcfg.get("batch_size", 64),
        validation_split=tcfg.get("validation_split", 0.15),
        callbacks=callbacks,
        class_weight=dict(enumerate(cw_vals)),
        verbose=0,
    )
    train_time = time.time() - t0
    print(f"  Done: {len(history.history['accuracy'])} epochs, {train_time:.1f}s")

    y_pred_prob = model.predict(X_test, verbose=0)
    y_pred = np.argmax(y_pred_prob, axis=1)
    metrics = compute_metrics(y_test, y_pred)

    # Save float32 Keras
    keras_path = models_dir / "fall_detection_3class.keras"
    model.save(keras_path)
    size_kb = keras_path.stat().st_size / 1024

    # Keras-based inference timing (always available)
    timing = measure_inference_time(
        lambda x: model.predict(x, verbose=0), X_test, n=200
    )

    # TFLite export (optional — skipped if TFLite DLLs unavailable on this system)
    try:
        conv_f32 = tf.lite.TFLiteConverter.from_keras_model(model)
        f32_bytes = conv_f32.convert()
        f32_path = models_dir / "fall_detection_3class_f32.tflite"
        f32_path.write_bytes(f32_bytes)
        size_kb = f32_path.stat().st_size / 1024
        print(f"  TFLite f32: {f32_path.name} ({size_kb:.1f} KB)")

        # TFLite inference timing (replaces Keras timing if available)
        interp = tf.lite.Interpreter(model_path=str(f32_path))
        interp.allocate_tensors()
        inp_det = interp.get_input_details()[0]
        out_det = interp.get_output_details()[0]

        def tflite_predict(x):
            interp.set_tensor(inp_det["index"], x.astype(np.float32))
            interp.invoke()
            return interp.get_tensor(out_det["index"])

        timing = measure_inference_time(tflite_predict, X_test, n=500)

        # INT8 export (optional)
        quant_cfg = config.get("quantization", {})
        if quant_cfg.get("export_int8_tflite", True):
            samples_per_cls = quant_cfg.get("calibration_samples_per_class", 200)
            cal_idx = []
            for cls in range(3):
                idx = np.where(y_train == cls)[0]
                chosen = np.random.choice(idx, size=min(samples_per_cls, len(idx)), replace=False)
                cal_idx.extend(chosen.tolist())
            np.random.shuffle(cal_idx)
            X_cal = X_train[cal_idx].astype(np.float32)

            def rep_dataset():
                for i in range(len(X_cal)):
                    yield [X_cal[i:i+1]]

            conv_i8 = tf.lite.TFLiteConverter.from_keras_model(model)
            conv_i8.optimizations = [tf.lite.Optimize.DEFAULT]
            conv_i8.representative_dataset = rep_dataset
            conv_i8.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
            conv_i8.inference_input_type = tf.int8
            conv_i8.inference_output_type = tf.int8
            i8_bytes = conv_i8.convert()
            i8_path = models_dir / "fall_detection_3class.tflite"
            i8_path.write_bytes(i8_bytes)
            print(f"  INT8 TFLite: {i8_path.name} ({len(i8_bytes)/1024:.1f} KB)")
    except Exception as tflite_err:
        print(f"  WARNING: TFLite export skipped ({type(tflite_err).__name__}: {tflite_err})")
        print("  Metrics are from Keras evaluation — all classification metrics are valid.")

    return {
        "metrics": metrics,
        "y_pred": y_pred,
        "timing": timing,
        "size_kb": size_kb,
        "train_time_s": round(train_time, 1),
        "history": {k: [float(v) for v in vals] for k, vals in history.history.items()},
    }


def _train_lstm(X_train, y_train, X_test, y_test, config: dict, seed: int, models_dir: Path):
    import tensorflow as tf
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    from tensorflow.keras.layers import LSTM, BatchNormalization, Dense, Dropout
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.utils import to_categorical
    from sklearn.utils.class_weight import compute_class_weight

    tf.random.set_seed(seed)
    np.random.seed(seed)

    tcfg = config.get("training", {})
    window_size = config.get("signal", {}).get("window_size", 100)
    num_channels = config.get("signal", {}).get("num_channels", 7)

    y_tr_cat = to_categorical(y_train, 3)
    y_te_cat = to_categorical(y_test, 3)

    model = Sequential([
        LSTM(32, input_shape=(window_size, num_channels), return_sequences=False),
        BatchNormalization(), Dropout(0.3),
        Dense(32, activation="relu"), Dropout(0.3),
        Dense(3, activation="softmax"),
    ])
    model.compile(
        optimizer=tf.keras.optimizers.Adam(tcfg.get("learning_rate", 0.001)),
        loss="categorical_crossentropy", metrics=["accuracy"],
    )

    cw_vals = compute_class_weight("balanced", classes=np.unique(y_train), y=y_train)
    callbacks = [
        EarlyStopping(monitor="val_loss", patience=10, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=4, min_lr=1e-6, verbose=0),
    ]

    print(f"  Training LSTM (seed={seed}, epochs≤{tcfg.get('epochs', 50)})...")
    t0 = time.time()
    history = model.fit(
        X_train, y_tr_cat,
        epochs=tcfg.get("epochs", 50),
        batch_size=tcfg.get("batch_size", 64),
        validation_split=tcfg.get("validation_split", 0.15),
        callbacks=callbacks,
        class_weight=dict(enumerate(cw_vals)),
        verbose=0,
    )
    train_time = time.time() - t0
    print(f"  Done: {len(history.history['accuracy'])} epochs, {train_time:.1f}s")

    y_pred_prob = model.predict(X_test, verbose=0)
    y_pred = np.argmax(y_pred_prob, axis=1)
    metrics = compute_metrics(y_test, y_pred)

    keras_path = models_dir / "lstm_3class.keras"
    model.save(keras_path)
    size_kb = keras_path.stat().st_size / 1024

    # Keras-based inference timing (always available)
    timing = measure_inference_time(
        lambda x: model.predict(x, verbose=0), X_test, n=200
    )

    # TFLite export (optional — skipped if TFLite DLLs unavailable on this system)
    try:
        conv_f32 = tf.lite.TFLiteConverter.from_keras_model(model)
        f32_bytes = conv_f32.convert()
        f32_path = models_dir / "lstm_3class.tflite"
        f32_path.write_bytes(f32_bytes)
        size_kb = f32_path.stat().st_size / 1024
        print(f"  TFLite f32: {f32_path.name} ({size_kb:.1f} KB)")
    except Exception as tflite_err:
        print(f"  WARNING: TFLite export skipped ({type(tflite_err).__name__}: {tflite_err})")
        print("  Metrics are from Keras evaluation — all classification metrics are valid.")

    return {
        "metrics": metrics,
        "y_pred": y_pred,
        "timing": timing,
        "size_kb": size_kb,
        "train_time_s": round(train_time, 1),
        "history": {k: [float(v) for v in vals] for k, vals in history.history.items()},
    }


# ── Main runner ───────────────────────────────────────────────

def run_experiment(config_path: Path, eval_only: bool = False, overwrite: bool = False):
    print(f"\n{'='*60}")
    print(f"  Experiment Runner — M1.2")
    print(f"  Config: {config_path.relative_to(ROOT)}")
    print(f"{'='*60}\n")

    # Load config
    try:
        import yaml
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except ImportError:
        print("  WARNING: PyYAML not installed — using basic config parser")
        config = _load_yaml(config_path)

    exp_name = config.get("experiment", {}).get("name", "unnamed")
    model_type = config.get("experiment", {}).get("model", config.get("model", {}).get("type", "unknown"))

    # Output directory
    out_dir = _make_output_dir(config, overwrite)
    print(f"  Output: {out_dir.relative_to(ROOT)}\n")

    # Save config copy
    import shutil
    shutil.copy(config_path, out_dir / "config.yaml")

    # Environment
    env = _get_environment()
    (out_dir / "environment.json").write_text(
        json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Data quality check
    data_dir = ROOT / config.get("dataset", {}).get("data_dir", "ml/data/processed_3class")
    print("  Running data quality checks...")
    dq = run_data_quality(data_dir, save_report=True)
    if dq["failed"] > 0:
        print(f"\n  FATAL: Data quality checks failed ({dq['failed']} failures). Fix before training.")
        sys.exit(1)
    print()

    # Load data
    X_train, X_test, y_train, y_test = _load_data(config)

    # Normalize and augment (for neural models)
    models_dir = ROOT / config.get("output", {}).get("model_dir", "ml/models_3class")
    models_dir.mkdir(parents=True, exist_ok=True)

    # Seeds
    seeds = config.get("training", {}).get("seeds", [42])
    repeated = config.get("training", {}).get("repeated_seeds", 1)
    seeds = seeds[:repeated]

    all_run_metrics = []
    last_y_pred = None
    last_timing = None
    last_size_kb = None
    last_history = None

    for seed in seeds:
        np.random.seed(seed)
        print(f"\n  --- Seed {seed} ---")

        if model_type == "random_forest":
            X_tr_aug, y_tr_aug = _augment(X_train, y_train, config)
            result = _train_rf(X_tr_aug, y_tr_aug, X_test, y_test, config, seed, models_dir)
        elif model_type == "cnn":
            X_tr_aug, y_tr_aug = _augment(X_train, y_train, config)
            X_tr_norm, X_te_norm = _normalize(X_tr_aug, X_test, models_dir)
            result = _train_cnn(X_tr_norm, y_tr_aug, X_te_norm, y_test, config, seed, models_dir)
        elif model_type == "lstm":
            X_tr_aug, y_tr_aug = _augment(X_train, y_train, config)
            X_tr_norm, X_te_norm = _normalize(X_tr_aug, X_test, models_dir)
            result = _train_lstm(X_tr_norm, y_tr_aug, X_te_norm, y_test, config, seed, models_dir)
        else:
            print(f"  ERROR: Unknown model type '{model_type}'")
            sys.exit(1)

        all_run_metrics.append(result["metrics"])
        last_y_pred = result["y_pred"]
        last_timing = result["timing"]
        last_size_kb = result["size_kb"]
        last_history = result["history"]

        print(f"  Seed {seed}: acc={result['metrics']['accuracy']:.4f}  "
              f"R-FALL={result['metrics']['per_class']['FALL']['recall']:.4f}  "
              f"F1-FALL={result['metrics']['per_class']['FALL']['f1']:.4f}")

    # Aggregate
    final_metrics = aggregate_repeated_runs(all_run_metrics)
    final_metrics["model"] = exp_name
    final_metrics["model_type"] = model_type
    final_metrics["seeds"] = seeds
    final_metrics["timing"] = last_timing
    final_metrics["size_kb"] = last_size_kb
    final_metrics["train_time_s"] = result.get("train_time_s")

    # Save artifacts
    save_metrics(final_metrics, out_dir)
    save_predictions(y_test, last_y_pred, out_dir)
    plot_confusion_matrix(all_run_metrics[-1]["confusion_matrix"], out_dir,
                           title=f"{exp_name} (seed={seeds[-1]})")
    if last_history:
        plot_training_history(last_history, out_dir)

    # Notes file
    notes_path = out_dir / "notes.md"
    if not notes_path.exists():
        notes_path.write_text(
            f"# Experiment Notes: {exp_name}\n\n"
            f"Generated: {env['generated_at']}\n"
            f"Git: {env['git_commit_short']} ({env['git_branch']})\n\n"
            f"## Results\n\n"
            f"acc={final_metrics.get('accuracy', '?')}  "
            f"macro_f1={final_metrics.get('macro_f1', '?')}  "
            f"R-FALL={final_metrics.get('per_class', {}).get('FALL', {}).get('recall', '?')}\n\n"
            f"## Notes\n\n_(add observations here)_\n",
            encoding="utf-8",
        )

    # Print summary
    print(f"\n{'='*60}")
    print(f"  EXPERIMENT COMPLETE: {exp_name}")
    print(f"{'='*60}")
    print(f"  accuracy       : {final_metrics.get('accuracy', '?')}")
    print(f"  balanced_acc   : {final_metrics.get('balanced_accuracy', '?')}")
    print(f"  macro_f1       : {final_metrics.get('macro_f1', '?')}")
    pc = final_metrics.get("per_class", {})
    for cls in CLASS_NAMES:
        m = pc.get(cls, {})
        print(f"  {cls:<28}: P={m.get('precision','?')}  R={m.get('recall','?')}  F1={m.get('f1','?')}")
    if last_timing:
        print(f"  inference      : {last_timing['mean_ms']:.4f} ms/window (p95={last_timing['p95_ms']:.4f})")
    if last_size_kb:
        print(f"  model size     : {last_size_kb:.1f} KB")
    print(f"\n  Artifacts: {out_dir.relative_to(ROOT)}/")
    print(f"{'='*60}\n")

    return final_metrics


def main():
    parser = argparse.ArgumentParser(description="M1.2 Experiment Runner")
    parser.add_argument("--config", required=True, help="Path to experiment YAML config")
    parser.add_argument("--eval-only", action="store_true", help="Evaluate existing model, skip training")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing experiment directory")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    if not config_path.exists():
        print(f"ERROR: Config not found: {config_path}")
        sys.exit(1)

    run_experiment(config_path, eval_only=args.eval_only, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
