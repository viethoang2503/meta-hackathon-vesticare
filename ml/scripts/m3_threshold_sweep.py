#!/usr/bin/env python3
"""
M3.4 Threshold Sweep — tune FALL probability threshold on validation data.

Creates a stratified 20% validation split from training data (NOT test set).
Sweeps P(FALL) threshold from 0.25 to 0.65 for both CNN float32 and LSTM.

Outputs:
  docs/m3_threshold_sweep.md
  results/m3_threshold_sweep.json

Run from project root:
  python ml/scripts/m3_threshold_sweep.py
"""

import json
import time
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"
RESULTS_DIR  = PROJECT_ROOT / "results"
DOCS_DIR     = PROJECT_ROOT / "docs"
RESULTS_DIR.mkdir(exist_ok=True)

CLASS_NAMES = ["NORMAL", "INSTABILITY", "FALL"]

# Backend thresholds
SVM_FALL_HARD   = 2.5   # g  → FALL regardless of ML (keep)
SVM_FALL_ASSIST = 1.5   # g  → current (BROKEN: blocks 67% of fall windows)
INST_THRESHOLD  = 0.50  # P(INSTABILITY) threshold (keep fixed)
THRESHOLDS      = np.round(np.arange(0.30, 0.66, 0.05), 2)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_and_split():
    print("Loading training data...")
    X_raw  = np.load(DATA_DIR / "X_train.npz")["data"]
    y      = np.load(DATA_DIR / "y_train.npz")["data"].astype(int)
    scaler = np.load(MODELS_DIR / "scaler_params.npz")

    # 20% stratified validation split from training data
    # (subject-level split not possible without metadata — stratified is standard)
    X_tr, X_val, y_tr, y_val = train_test_split(
        X_raw, y, test_size=0.20, random_state=42, stratify=y
    )

    X_val_norm = (X_val - scaler["mean"]) / scaler["std"]

    print(f"  Validation: {len(y_val)} samples")
    for i, name in enumerate(CLASS_NAMES):
        print(f"    {name}: {(y_val == i).sum()}")

    return X_val, X_val_norm, y_val


def svm_peak(X_raw):
    svm = np.sqrt(X_raw[:, :, 0]**2 + X_raw[:, :, 1]**2 + X_raw[:, :, 2]**2)
    return svm.max(axis=1)


# ---------------------------------------------------------------------------
# Policy: replicate backend decision logic with tunable thresholds
# ---------------------------------------------------------------------------

def apply_policy(probs, peak_svm_vec, fall_thresh, svm_assist=SVM_FALL_ASSIST,
                 inst_thresh=INST_THRESHOLD):
    """
    Mirrors backend hybrid policy:
      1. peak_svm >= 2.5g          → FALL (hard safety fallback)
      2. P(FALL) >= fall_thresh
         AND peak_svm >= svm_assist → FALL (ML + SVM combined)
      3. P(INST) >= inst_thresh    → INSTABILITY
      4. else                      → NORMAL
    """
    p_inst = probs[:, 1]
    p_fall = probs[:, 2]

    y_pred = np.zeros(len(probs), dtype=int)

    # Rule 3 first (lowest priority, overridden by fall rules)
    y_pred[p_inst >= inst_thresh] = 1

    # Rule 2: ML + SVM
    y_pred[(p_fall >= fall_thresh) & (peak_svm_vec >= svm_assist)] = 2

    # Rule 1: hard SVM fallback (highest priority)
    y_pred[peak_svm_vec >= SVM_FALL_HARD] = 2

    return y_pred


def metrics_at_threshold(y_true, probs, peak_svm_vec, fall_thresh,
                         svm_assist=SVM_FALL_ASSIST):
    y_pred = apply_policy(probs, peak_svm_vec, fall_thresh, svm_assist)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2])

    tp = float(cm[2, 2])
    fn = float(cm[2, 0] + cm[2, 1])
    fp = float(cm[0, 2] + cm[1, 2])

    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    fp_rate   = fp / (len(y_true) - (tp + fn)) if (len(y_true) - (tp + fn)) > 0 else 0.0

    # Count hard-SVM-triggered falls (catches soft-fall misses via SVM path)
    hard_svm_fall = int((peak_svm_vec >= SVM_FALL_HARD).sum())

    return {
        "fall_threshold": float(fall_thresh),
        "svm_assist":     float(svm_assist),
        "recall_fall":    round(recall, 4),
        "precision_fall": round(precision, 4),
        "f1_fall":        round(f1, 4),
        "fp_rate":        round(fp_rate, 4),
        "tp":             int(tp),
        "fn":             int(fn),
        "fp":             int(fp),
        "hard_svm_hits":  hard_svm_fall,
    }


# ---------------------------------------------------------------------------
# Get model probabilities
# ---------------------------------------------------------------------------

def get_probs(model_path, X_norm):
    import tensorflow as tf
    print(f"  Loading {model_path.name}...")
    t0 = time.perf_counter()
    model = tf.keras.models.load_model(model_path)
    probs = model.predict(X_norm, batch_size=512, verbose=0)
    print(f"  Done ({time.perf_counter()-t0:.1f}s)")
    return probs


# ---------------------------------------------------------------------------
# Sweep + recommend
# ---------------------------------------------------------------------------

def sweep(probs, peak_svm_vec, y_val, model_name):
    rows = []
    # Fixed policy: no SVM assist (svm_assist=0 means pure ML decision)
    for t in THRESHOLDS:
        m = metrics_at_threshold(y_val, probs, peak_svm_vec, t, svm_assist=0.0)
        m["model"] = model_name
        m["policy"] = "no_svm_assist"
        rows.append(m)
    # Also record current (broken) policy for comparison
    cur = metrics_at_threshold(y_val, probs, peak_svm_vec, 0.50, svm_assist=1.5)
    cur["model"] = model_name
    cur["policy"] = "current_1.5g_assist"
    rows.append(cur)
    return rows


def find_best(rows, target_recall=0.85, min_precision=0.60):
    """
    Best threshold = highest recall >= target while precision >= min_precision.
    If no row meets both, pick the one closest to target recall.
    """
    candidates = [r for r in rows
                  if r["recall_fall"] >= target_recall
                  and r["precision_fall"] >= min_precision]
    if candidates:
        return min(candidates, key=lambda r: r["fall_threshold"])

    # Fallback: row with highest recall (may sacrifice precision)
    return max(rows, key=lambda r: r["recall_fall"])


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(all_rows, ts):
    lines = [
        "# M3.4 Threshold Sweep Report",
        f"\n**Generated:** {ts}  ",
        "**Validation set:** 20% stratified split from training data (NOT test set)  ",
        f"**Thresholds swept:** {list(THRESHOLDS)}  ",
        "**Policy:** peak_svm ≥ 2.5g → FALL (hard) · P(FALL) ≥ T AND svm ≥ 1.5g → FALL · P(INST) ≥ 0.50 → INSTABILITY",
        "\n---\n",
    ]

    for model_name in ["1D-CNN float32", "LSTM"]:
        rows = [r for r in all_rows if r["model"] == model_name]
        best = find_best(rows)

        lines += [
            f"## {model_name}\n",
            "| Threshold | Rec-FALL | Prec-FALL | F1-FALL | FP-rate | TP | FN | FP |",
            "|----------:|---------:|----------:|--------:|--------:|---:|---:|---:|",
        ]
        for r in rows:
            marker = " ◀ **recommended**" if r["fall_threshold"] == best["fall_threshold"] else ""
            lines.append(
                f"| **{r['fall_threshold']:.2f}** | {r['recall_fall']:.4f} "
                f"| {r['precision_fall']:.4f} | {r['f1_fall']:.4f} "
                f"| {r['fp_rate']:.4f} | {r['tp']} | {r['fn']} | {r['fp']} |{marker}"
            )

        lines += [
            f"\n**Recommended threshold:** {best['fall_threshold']:.2f}  ",
            f"**At this threshold:** Rec-FALL={best['recall_fall']:.4f}, "
            f"Prec-FALL={best['precision_fall']:.4f}, F1={best['f1_fall']:.4f}\n",
        ]

        # Hard SVM hits
        lines += [
            f"> Hard SVM fallback (≥ 2.5g) catches **{rows[0]['hard_svm_hits']}** samples "
            f"regardless of threshold — these are high-impact falls already handled.\n",
        ]

    lines += [
        "---\n",
        "## Summary & Next Step\n",
        "| Model | Current (T=0.50) | Best threshold | Best Rec-FALL | Δ recall |",
        "|-------|-----------------|----------------|--------------|---------|",
    ]

    for model_name in ["1D-CNN float32", "LSTM"]:
        rows = [r for r in all_rows if r["model"] == model_name]
        cur  = next(r for r in rows if r["fall_threshold"] == 0.50)
        best = find_best(rows)
        delta = best["recall_fall"] - cur["recall_fall"]
        lines.append(
            f"| {model_name} | {cur['recall_fall']:.4f} | {best['fall_threshold']:.2f} "
            f"| {best['recall_fall']:.4f} | +{delta:.4f} |"
        )

    lines += [
        "\n**If best recall < 0.85 after sweep:** proceed to retrain with focal loss + "
        "2× FALL class weight + targeted soft-fall augmentation (bước 2+3).\n",
        "**If best recall ≥ 0.85:** update backend threshold and close M3.4.\n",
        "\n---\n",
        "_Threshold tuning uses validation data only — final evaluation on test set after model selection._",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  M3.4 THRESHOLD SWEEP — Vesti-Care")
    print("=" * 60)

    X_val_raw, X_val_norm, y_val = load_and_split()
    ps = svm_peak(X_val_raw)

    all_rows = []

    print("\n[1/2] 1D-CNN float32...")
    probs_cnn = get_probs(MODELS_DIR / "fall_detection_3class.keras", X_val_norm)
    all_rows += sweep(probs_cnn, ps, y_val, "1D-CNN float32")

    print("[2/2] LSTM...")
    probs_lstm = get_probs(MODELS_DIR / "lstm_3class.keras", X_val_norm)
    all_rows += sweep(probs_lstm, ps, y_val, "LSTM")

    # Console table
    print("\n=== RESULTS (fixed policy: no SVM assist) ===")
    for model_name in ["1D-CNN float32", "LSTM"]:
        main_rows = [r for r in all_rows
                     if r["model"] == model_name and r.get("policy") == "no_svm_assist"]
        broken = next(r for r in all_rows
                      if r["model"] == model_name and r.get("policy") == "current_1.5g_assist")
        best = find_best(main_rows)

        print(f"\n{model_name}")
        print(f"  [Current broken policy T=0.50, SVM>=1.5G]: recall={broken['recall_fall']:.4f}  ← BUG")
        print(f"  {'T':>5}  {'Rec-FALL':>9}  {'Prec-FALL':>10}  {'F1':>7}  {'FP-rate':>8}")
        for r in main_rows:
            marker = " ◀ recommended" if r["fall_threshold"] == best["fall_threshold"] else ""
            print(f"  {r['fall_threshold']:>5.2f}  {r['recall_fall']:>9.4f}  "
                  f"{r['precision_fall']:>10.4f}  {r['f1_fall']:>7.4f}  "
                  f"{r['fp_rate']:>8.4f}{marker}")

        print(f"\n  Best (T={best['fall_threshold']:.2f}, no SVM assist): "
              f"recall={best['recall_fall']:.4f}  prec={best['precision_fall']:.4f}")
        if best["recall_fall"] >= 0.85:
            print(f"  ✓ TARGET 0.85 REACHED — fix policy only, no retrain needed")
        else:
            gap = 0.85 - best["recall_fall"]
            print(f"  ✗ Still {gap:.4f} below — retrain with focal loss after policy fix")

    # Save
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    json_path = RESULTS_DIR / "m3_threshold_sweep.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"generated": ts, "results": all_rows}, f, indent=2)
    print(f"\nJSON   → {json_path}")

    md_path = DOCS_DIR / "m3_threshold_sweep.md"
    md_path.write_text(build_report(all_rows, ts), encoding="utf-8")
    print(f"Report → {md_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
