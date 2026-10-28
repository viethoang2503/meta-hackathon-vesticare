"""
M1.3 - Locked Evaluation Rules
Single source of truth for all paper metrics.
Every model comparison must use these functions — never raw sklearn calls.

Rules:
- Subject-independent split (enforced by data prep, verified by data_quality.py)
- Same held-out test set for all model comparisons
- Per-class precision, recall, F1, support
- Macro-F1 and balanced accuracy
- Confusion matrix (raw counts + row-normalized %)
- Model size (KB) and inference time (ms/window)
- Mean ± std when repeated_seeds > 1
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

CLASS_NAMES = ["NORMAL", "VESTIBULAR_INSTABILITY", "FALL"]
N_CLASSES = 3


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Compute the full locked metric set for one run."""
    from sklearn.metrics import (
        accuracy_score,
        balanced_accuracy_score,
        confusion_matrix,
        f1_score,
        precision_recall_fscore_support,
    )

    acc = float(accuracy_score(y_true, y_pred))
    bal_acc = float(balanced_accuracy_score(y_true, y_pred))
    macro_f1 = float(f1_score(y_true, y_pred, average="macro", zero_division=0))

    prec, rec, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=[0, 1, 2], zero_division=0
    )

    per_class = {}
    for i, name in enumerate(CLASS_NAMES):
        per_class[name] = {
            "precision": round(float(prec[i]), 4),
            "recall": round(float(rec[i]), 4),
            "f1": round(float(f1[i]), 4),
            "support": int(support[i]),
        }

    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2])

    return {
        "accuracy": round(acc, 4),
        "balanced_accuracy": round(bal_acc, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion_matrix": cm.tolist(),
    }


def measure_inference_time(predict_fn, X_bench: np.ndarray, n: int = 500) -> dict:
    """Time predict_fn on n windows. Returns mean/std/min/max in ms."""
    X_bench = X_bench[:n]
    times = []
    for i in range(len(X_bench)):
        t0 = time.perf_counter_ns()
        predict_fn(X_bench[i : i + 1])
        times.append((time.perf_counter_ns() - t0) / 1_000_000)

    times = np.array(times)
    return {
        "mean_ms": round(float(times.mean()), 4),
        "std_ms": round(float(times.std()), 4),
        "min_ms": round(float(times.min()), 4),
        "max_ms": round(float(times.max()), 4),
        "p95_ms": round(float(np.percentile(times, 95)), 4),
        "n": len(times),
    }


def aggregate_repeated_runs(runs: list[dict]) -> dict:
    """Aggregate metrics from repeated seeds. Returns mean ± std."""
    if len(runs) == 1:
        return runs[0]

    keys = ["accuracy", "balanced_accuracy", "macro_f1"]
    result = {}
    for k in keys:
        vals = [r[k] for r in runs]
        result[k] = {"mean": round(float(np.mean(vals)), 4), "std": round(float(np.std(vals)), 4)}

    # Per-class: mean ± std
    result["per_class"] = {}
    for name in CLASS_NAMES:
        result["per_class"][name] = {}
        for metric in ["precision", "recall", "f1"]:
            vals = [r["per_class"][name][metric] for r in runs]
            result["per_class"][name][metric] = {
                "mean": round(float(np.mean(vals)), 4),
                "std": round(float(np.std(vals)), 4),
            }
        result["per_class"][name]["support"] = runs[0]["per_class"][name]["support"]

    # Confusion matrix: sum across runs
    cms = np.array([r["confusion_matrix"] for r in runs])
    result["confusion_matrix_sum"] = cms.sum(axis=0).tolist()
    result["n_runs"] = len(runs)
    return result


def format_metrics_table(metrics: dict, model_name: str, size_kb: float | None, timing: dict | None) -> str:
    """Format a single-row summary for paper table."""
    acc = metrics.get("accuracy", "?")
    bal = metrics.get("balanced_accuracy", "?")
    mf1 = metrics.get("macro_f1", "?")
    pc = metrics.get("per_class", {})
    fall = pc.get("FALL", {})
    inst = pc.get("VESTIBULAR_INSTABILITY", {})

    size_str = f"{size_kb:.1f}" if size_kb else "—"
    inf_str = f"{timing['mean_ms']:.4f}" if timing else "—"

    return (
        f"  {model_name:<22} "
        f"acc={acc:.4f}  bal={bal:.4f}  macro_f1={mf1:.4f}  "
        f"R-FALL={fall.get('recall', '?'):.4f}  F1-FALL={fall.get('f1', '?'):.4f}  "
        f"R-INST={inst.get('recall', '?'):.4f}  "
        f"size={size_str}KB  inf={inf_str}ms"
    )


def save_metrics(metrics: dict, out_dir: Path, filename: str = "metrics.json"):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def save_predictions(y_true: np.ndarray, y_pred: np.ndarray, out_dir: Path):
    """Save predictions CSV for offline analysis."""
    import csv
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "predictions.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "y_true", "y_true_name", "y_pred", "y_pred_name"])
        for i, (yt, yp) in enumerate(zip(y_true, y_pred)):
            writer.writerow([i, int(yt), CLASS_NAMES[int(yt)], int(yp), CLASS_NAMES[int(yp)]])
    return path


def plot_confusion_matrix(cm: list | np.ndarray, out_dir: Path, title: str = ""):
    import matplotlib.pyplot as plt
    import seaborn as sns

    cm = np.array(cm)
    row_sum = cm.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0] = 1
    cm_pct = cm / row_sum * 100

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))

    # Raw counts
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=CLASS_NAMES, yticklabels=CLASS_NAMES,
                ax=axes[0], cbar=False)
    axes[0].set_title(f"{title}\nCounts")
    axes[0].set_ylabel("Actual")
    axes[0].set_xlabel("Predicted")

    # Row-normalized %
    sns.heatmap(cm_pct, annot=True, fmt=".1f", cmap="Blues",
                xticklabels=CLASS_NAMES, yticklabels=CLASS_NAMES,
                ax=axes[1], cbar=False)
    axes[1].set_title(f"{title}\nRow-normalized %")
    axes[1].set_ylabel("Actual")
    axes[1].set_xlabel("Predicted")

    plt.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "confusion_matrix.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return path


def plot_training_history(history: dict, out_dir: Path):
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))

    axes[0].plot(history.get("accuracy", []), label="train")
    axes[0].plot(history.get("val_accuracy", []), label="val")
    axes[0].set_title("Accuracy")
    axes[0].set_xlabel("Epoch")
    axes[0].legend()

    axes[1].plot(history.get("loss", []), label="train")
    axes[1].plot(history.get("val_loss", []), label="val")
    axes[1].set_title("Loss")
    axes[1].set_xlabel("Epoch")
    axes[1].legend()

    plt.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "training_history.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return path
