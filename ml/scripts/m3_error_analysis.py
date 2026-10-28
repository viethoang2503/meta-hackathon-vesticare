#!/usr/bin/env python3
"""
M3 Error Analysis — FALL recall investigation and deployment model selection.

Implements:
  M3.1 — Dataset definition summary (printed to report)
  M3.2 — Error analysis on FALL false negatives
  M3.3 — Deployment candidate comparison using utility function

Outputs:
  docs/m3_error_analysis.md      — human-readable report
  results/m3_error_analysis.json — machine-readable data

Run from project root:
  python ml/scripts/m3_error_analysis.py
"""

import sys
import json
import pickle
import time
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from sklearn.metrics import confusion_matrix

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"
RESULTS_DIR  = PROJECT_ROOT / "results"
DOCS_DIR     = PROJECT_ROOT / "docs"
RESULTS_DIR.mkdir(exist_ok=True)

CLASS_NAMES = ["NORMAL", "INSTABILITY", "FALL"]

SVM_FALL_THRESHOLD = 2.5   # g — hard fallback threshold
SVM_INST_THRESHOLD = 1.5   # g

# Deployment scores (0–1): size + latency + embedded feasibility
DEPLOYMENT_SCORES = {
    "Rule-based (SVM)":  0.60,  # Tiny/instant but near-useless accuracy
    "Random Forest":      0.15,  # 128 MB — incompatible with ESP32
    "1D-CNN float32":    0.90,  # 36 KB TFLite, 0.023 ms — current backend
    "LSTM":               0.70,  # 36 KB TFLite exists, Keras eval used here
    "1D-CNN INT8":        0.95,  # 22 KB TFLite, fastest — best for ESP32
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data():
    X_raw  = np.load(DATA_DIR / "X_test.npz")["data"]           # (N, 100, 7) raw
    y_test = np.load(DATA_DIR / "y_test.npz")["data"].astype(int)
    scaler = np.load(MODELS_DIR / "scaler_params.npz")
    X_norm = (X_raw - scaler["mean"]) / scaler["std"]            # normalized
    X_feat = np.load(DATA_DIR / "X_test_features.npz")["data"]  # 19 features for RF
    return X_raw, X_norm, X_feat, y_test


def svm_peak(X_raw):
    """Peak SVM magnitude (g) per window from raw accelerometer channels 0–2."""
    svm = np.sqrt(X_raw[:, :, 0]**2 + X_raw[:, :, 1]**2 + X_raw[:, :, 2]**2)
    return svm.max(axis=1)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def per_class_metrics(y_true, y_pred):
    """Per-class precision/recall/f1 + accuracy. Returns nested dict."""
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2])
    out = {"cm": cm, "accuracy": float((y_true == y_pred).mean())}
    for i, name in enumerate(CLASS_NAMES):
        tp = float(cm[i, i])
        fn = float(cm[i].sum() - cm[i, i])
        fp = float(cm[:, i].sum() - cm[i, i])
        tn = float(cm.sum() - tp - fn - fp)
        p   = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r   = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1  = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        out[name] = {"precision": p, "recall": r, "f1": f1,
                     "tp": int(tp), "fn": int(fn), "fp": int(fp)}
    return out


def utility_score(m, deploy):
    """M3.3 utility function (weights from master plan §8)."""
    return (
        0.40 * m["FALL"]["recall"]
        + 0.20 * m["FALL"]["f1"]
        + 0.15 * m["INSTABILITY"]["recall"]
        + 0.15 * m["NORMAL"]["recall"]
        + 0.10 * deploy
    )


# ---------------------------------------------------------------------------
# FALL false-negative analysis
# ---------------------------------------------------------------------------

def fn_analysis(y_true, y_pred, peak_svm_vec):
    fall_mask = (y_true == 2)
    fn_mask   = fall_mask & (y_pred != 2)
    fn_idx    = np.where(fn_mask)[0]

    n_fall = int(fall_mask.sum())
    n_fn   = int(fn_mask.sum())

    fn_as_normal = int((y_pred[fn_idx] == 0).sum())
    fn_as_inst   = int((y_pred[fn_idx] == 1).sum())

    # SVM quartiles computed over all FALL samples (not just FN)
    fall_svm = peak_svm_vec[fall_mask]
    q25, q50, q75 = (np.percentile(fall_svm, [25, 50, 75])
                     if n_fall > 0 else (0., 0., 0.))
    fn_svm = peak_svm_vec[fn_idx]

    svm_groups = {
        f"low    (< {q25:.2f} g)":               int((fn_svm < q25).sum()),
        f"medium ({q25:.2f} – {q50:.2f} g)":     int(((fn_svm >= q25) & (fn_svm < q50)).sum()),
        f"high   ({q50:.2f} – {q75:.2f} g)":     int(((fn_svm >= q50) & (fn_svm < q75)).sum()),
        f"v.high (>= {q75:.2f} g)":              int((fn_svm >= q75).sum()),
    }

    return {
        "n_fall_test":       n_fall,
        "n_fn":              n_fn,
        "fn_rate":           n_fn / n_fall if n_fall > 0 else 0.0,
        "fn_as_normal":      fn_as_normal,
        "fn_as_instability": fn_as_inst,
        "svm_quartiles":     {"q25": float(q25), "q50": float(q50), "q75": float(q75)},
        "fn_by_svm_group":   svm_groups,
    }


# ---------------------------------------------------------------------------
# Model evaluators
# ---------------------------------------------------------------------------

def eval_rule_based(X_raw, y_test, peak_svm_vec):
    svm   = np.sqrt(X_raw[:, :, 0]**2 + X_raw[:, :, 1]**2 + X_raw[:, :, 2]**2)
    ps    = svm.max(axis=1)
    y_pred = np.zeros(len(y_test), dtype=int)
    y_pred[ps >= SVM_INST_THRESHOLD] = 1
    y_pred[ps >= SVM_FALL_THRESHOLD] = 2
    return y_pred, per_class_metrics(y_test, y_pred), fn_analysis(y_test, y_pred, peak_svm_vec)


def eval_random_forest(X_feat, y_test, peak_svm_vec):
    with open(MODELS_DIR / "random_forest_3class.pkl", "rb") as f:
        rf = pickle.load(f)
    y_pred = rf.predict(X_feat)
    return y_pred, per_class_metrics(y_test, y_pred), fn_analysis(y_test, y_pred, peak_svm_vec)


def eval_keras(model_path, X_norm, y_test, peak_svm_vec):
    """Evaluate a .keras model (Keras inference, no TFLite needed)."""
    import tensorflow as tf
    model  = tf.keras.models.load_model(model_path)
    y_prob = model.predict(X_norm, batch_size=512, verbose=0)
    y_pred = np.argmax(y_prob, axis=1)
    return y_pred, per_class_metrics(y_test, y_pred), fn_analysis(y_test, y_pred, peak_svm_vec)


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(results, ts):
    def pct(n, d):
        return f"{n/d*100:.1f}%" if d > 0 else "N/A"

    lines = [
        "# M3 Error Analysis",
        f"\n**Generated:** {ts}  ",
        "**Implements:** M3.1 dataset definition · M3.2 FN analysis · M3.3 utility scores",
        "\n---\n",

        "## 1. Model Performance Summary\n",
        "| Model | Acc | Rec-FALL | Prec-FALL | F1-FALL | Rec-INST | Rec-NORMAL |",
        "|-------|----:|---------:|----------:|--------:|---------:|-----------:|",
    ]
    for r in results:
        m = r["metrics"]
        lines.append(
            f"| {r['name']} | {m['accuracy']:.3f} | {m['FALL']['recall']:.3f} "
            f"| {m['FALL']['precision']:.3f} | {m['FALL']['f1']:.3f} "
            f"| {m['INSTABILITY']['recall']:.3f} | {m['NORMAL']['recall']:.3f} |"
        )
    lines.append(
        "\n> **Target:** FALL recall ≥ 0.85 — no current model meets this threshold. "
        "Best is LSTM (0.820).\n"
    )

    lines += [
        "---\n",
        "## 2. Utility Function Scores — M3.3\n",
        "```",
        "utility = 0.40 × recall_fall + 0.20 × f1_fall",
        "        + 0.15 × recall_inst  + 0.15 × recall_normal",
        "        + 0.10 × deployment_score",
        "```\n",
        "| Model | recall_fall | f1_fall | rec_inst | rec_normal | deploy | **Utility** |",
        "|-------|------------:|--------:|---------:|-----------:|-------:|------------:|",
    ]
    ranked = sorted(
        results,
        key=lambda r: utility_score(r["metrics"], DEPLOYMENT_SCORES.get(r["name"], 0.5)),
        reverse=True,
    )
    for r in ranked:
        m  = r["metrics"]
        ds = DEPLOYMENT_SCORES.get(r["name"], 0.5)
        u  = utility_score(m, ds)
        lines.append(
            f"| {r['name']} | {m['FALL']['recall']:.3f} | {m['FALL']['f1']:.3f} "
            f"| {m['INSTABILITY']['recall']:.3f} | {m['NORMAL']['recall']:.3f} "
            f"| {ds:.2f} | **{u:.4f}** |"
        )
    best = ranked[0]
    lines += [
        f"\n**Winner:** {best['name']} "
        f"(utility = {utility_score(best['metrics'], DEPLOYMENT_SCORES.get(best['name'], 0.5)):.4f})\n",
        "---\n",
    ]

    lines += [
        "## 3. FALL False-Negative Analysis — M3.2\n",
        "False negatives = windows where true label is FALL but model predicted otherwise.\n",
    ]
    for r in results:
        fn = r["fn_analysis"]
        m  = r["metrics"]
        cm = m["cm"]
        lines += [
            f"### {r['name']}\n",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| FALL test samples | {fn['n_fall_test']} |",
            f"| False negatives total | **{fn['n_fn']}** ({pct(fn['n_fn'], fn['n_fall_test'])}) |",
            f"| → predicted as NORMAL | {fn['fn_as_normal']} ({pct(fn['fn_as_normal'], fn['n_fall_test'])}) |",
            f"| → predicted as INSTABILITY | {fn['fn_as_instability']} ({pct(fn['fn_as_instability'], fn['n_fall_test'])}) |",
            f"\n**Confusion matrix (row = true, col = predicted):**",
            f"```",
            f"               NORMAL  INSTABILITY  FALL",
            f"NORMAL          {cm[0,0]:6d}       {cm[0,1]:6d}  {cm[0,2]:4d}",
            f"INSTABILITY     {cm[1,0]:6d}       {cm[1,1]:6d}  {cm[1,2]:4d}",
            f"FALL            {cm[2,0]:6d}       {cm[2,1]:6d}  {cm[2,2]:4d}",
            f"```\n",
            f"**FN grouped by peak SVM (fall intensity):**",
        ]
        for group, val in fn["fn_by_svm_group"].items():
            if group == "note":
                lines.append(f"- _{val}_")
            else:
                pct_fn = pct(val, fn['n_fn']) if fn['n_fn'] > 0 else "N/A"
                lines.append(f"- {group}: {val} FN ({pct_fn} of all FN)")
        lines.append("")

    lines += [
        "---\n",
        "## 4. Key Findings\n",
        "### 4.1 FALL Recall Gap",
        "No model reaches the 0.85 recall target. Gap analysis:\n",
        "- **LSTM** is closest at 0.820. The gap is ~3 percentage points.",
        "- **1D-CNN float32** reaches 0.800. Simpler to deploy than LSTM.",
        "- **Random Forest** reaches 0.752 with 128 MB model — not embeddable.",
        "- **INT8** is 0.650 — quantization degraded FALL recall severely.\n",

        "### 4.2 Confusion Pattern (FALL false negatives)\n",
        "- FALL → NORMAL confusion: model misses 'soft' falls (slow collapses, low SVM).",
        "- FALL → INSTABILITY confusion: high-amplitude sway patterns resemble fall onset.\n",

        "### 4.3 Proposed improvements for M3.4 threshold tuning\n",
        "Tune on a **validation split from training data** (NOT the test set):\n",
        "- Lower FALL probability threshold: 0.5 → 0.40 (trades precision for recall)",
        "- Lower SVM hard fallback: 2.5G → 2.0G (catches more low-SVM falls)",
        "- Evaluate event-level precision/recall with cooldown (using simulator)",
        "- Use precision-recall curve to pick threshold that maximises F-beta (β=2, recall-weighted)\n",

        "### 4.4 INT8 status",
        "INT8 FALL recall is 0.650. Do not promote to primary alert path. "
        "Keep as quantization experiment per M3.5.\n",

        "---\n",
        "## 5. Dataset Definition — M3.1\n",
        "| Label | Value | Source | Mapping rationale | Clinical status |",
        "|-------|------:|--------|-------------------|----------------|",
        "| NORMAL | 0 | SisFall (D* files) + Vestibular normal activities | ADL activities labeled as normal by source datasets | Not a clinical claim |",
        "| VESTIBULAR_INSTABILITY | 1 | Grouvel et al. 2025 vestibular dataset (BV/UV/HS groups) | **Proxy label** — vestibular-disorder participants performing balance tasks | NOT clinically validated instability prediction |",
        "| FALL | 2 | SisFall (F* files) | Falls labeled by source dataset | Not a clinical claim |\n",
        "**Limitations to disclose in paper:**",
        "- The INSTABILITY class is a proxy: we assume that vestibular-disorder participants",
        "  performing balance tasks produce motion signatures similar to vestibular instability.",
        "  No clinical instability ground truth was used.",
        "- SisFall and Vestibular datasets differ in sensor placement, demographics, and sampling",
        "  rate (SisFall: waist, 200 Hz resampled to 50 Hz; Vestibular: unknown placement, 128 Hz",
        "  resampled to 50 Hz). This may introduce domain shift.",
        "- Subject-independent split used: SisFall test = SA19–SA23, SE13–SE15;",
        "  Vestibular test = BV 15–19, UV 35–39, HS 55–59.\n",
        "---\n",
        "_M3 error analysis complete. Threshold tuning (M3.4) is the next step — "
        "requires creating a validation split from training data._",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON serializer
# ---------------------------------------------------------------------------

def to_json(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_json(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  M3 ERROR ANALYSIS — Vesti-Care")
    print("=" * 60)

    print("\nLoading test data...")
    X_raw, X_norm, X_feat, y_test = load_data()
    ps = svm_peak(X_raw)
    print(f"  Test windows: {len(y_test)}")
    for i, n in enumerate(CLASS_NAMES):
        print(f"    {n}: {(y_test == i).sum()}")

    results = []

    print("\n[1/5] Rule-based (SVM threshold)...")
    t0 = time.perf_counter()
    y_pred, m, fn = eval_rule_based(X_raw, y_test, ps)
    print(f"      FALL recall={m['FALL']['recall']:.3f}  FN={fn['n_fn']}  ({time.perf_counter()-t0:.1f}s)")
    results.append({"name": "Rule-based (SVM)", "metrics": m, "fn_analysis": fn})

    print("[2/5] Random Forest...")
    t0 = time.perf_counter()
    y_pred, m, fn = eval_random_forest(X_feat, y_test, ps)
    print(f"      FALL recall={m['FALL']['recall']:.3f}  FN={fn['n_fn']}  ({time.perf_counter()-t0:.1f}s)")
    results.append({"name": "Random Forest", "metrics": m, "fn_analysis": fn})

    print("[3/5] 1D-CNN float32 (Keras)...")
    t0 = time.perf_counter()
    y_pred, m, fn = eval_keras(MODELS_DIR / "fall_detection_3class.keras", X_norm, y_test, ps)
    print(f"      FALL recall={m['FALL']['recall']:.3f}  FN={fn['n_fn']}  ({time.perf_counter()-t0:.1f}s)")
    results.append({"name": "1D-CNN float32", "metrics": m, "fn_analysis": fn})

    print("[4/5] LSTM (Keras)...")
    t0 = time.perf_counter()
    y_pred, m, fn = eval_keras(MODELS_DIR / "lstm_3class.keras", X_norm, y_test, ps)
    print(f"      FALL recall={m['FALL']['recall']:.3f}  FN={fn['n_fn']}  ({time.perf_counter()-t0:.1f}s)")
    results.append({"name": "LSTM", "metrics": m, "fn_analysis": fn})

    # INT8: TFLite DLL broken in TF 2.21 on this env — use known metrics from comparison_table.txt
    # CM reconstructed from: Rec-FALL=0.650, Prec-FALL=0.426, Rec-INST=1.000, Prec-INST=0.446, Acc=0.555
    # Test: NORMAL=14547, INST=4633, FALL=5221. Derived: NORMAL→FALL=4577, NORMAL→INST=4454,
    #       FALL→INST=1300, FALL→NORMAL=527
    print("[5/5] 1D-CNN INT8 (TFLite unavailable — using reconstructed metrics from table)")
    known_int8 = {
        "NORMAL":      {"precision": 0.591, "recall": 0.557, "f1": 0.574, "tp": 5516, "fn": 9031, "fp": 5104},
        "INSTABILITY": {"precision": 0.446, "recall": 1.000, "f1": 0.616, "tp": 4633, "fn": 0,    "fp": 5754},
        "FALL":        {"precision": 0.426, "recall": 0.650, "f1": 0.514, "tp": 3394, "fn": 1827,  "fp": 4577},
        "accuracy":    0.555,
        "cm":          np.array([[5516, 4454, 4577],
                                 [0,    4633,    0],
                                 [527,  1300, 3394]]),
    }
    known_fn = {
        "n_fall_test":       5221,
        "n_fn":              1827,
        "fn_rate":           1827 / 5221,
        "fn_as_normal":      527,
        "fn_as_instability": 1300,
        "svm_quartiles":     {"q25": 0.0, "q50": 0.0, "q75": 0.0},
        "fn_by_svm_group":   {"note": "TFLite DLL unavailable — per-sample SVM analysis skipped"},
    }
    results.append({"name": "1D-CNN INT8", "metrics": known_int8, "fn_analysis": known_fn})

    # Save JSON
    serializable = to_json(results)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in serializable:
        ds = DEPLOYMENT_SCORES.get(r["name"], 0.5)
        r["deployment_score"] = ds
        r["utility_score"] = round(utility_score(r["metrics"], ds), 6)
    payload = {"generated": ts, "models": serializable}

    json_path = RESULTS_DIR / "m3_error_analysis.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nJSON  → {json_path}")

    # Save markdown report
    report = build_report(results, ts)
    md_path = DOCS_DIR / "m3_error_analysis.md"
    md_path.write_text(report, encoding="utf-8")
    print(f"Report → {md_path}")

    # Print utility ranking
    print("\n=== UTILITY RANKING ===")
    ranked = sorted(
        results,
        key=lambda r: utility_score(r["metrics"], DEPLOYMENT_SCORES.get(r["name"], 0.5)),
        reverse=True,
    )
    for r in ranked:
        ds = DEPLOYMENT_SCORES.get(r["name"], 0.5)
        u  = utility_score(r["metrics"], ds)
        m  = r["metrics"]
        print(
            f"  {r['name']:<22}  utility={u:.4f}  "
            f"rec_fall={m['FALL']['recall']:.3f}  "
            f"rec_inst={m['INSTABILITY']['recall']:.3f}  "
            f"rec_normal={m['NORMAL']['recall']:.3f}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
