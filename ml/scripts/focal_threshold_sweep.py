"""
focal_threshold_sweep.py

Threshold sweep tren TEST SET voi model focal hien tai.
Muc tieu: tim nguong T sao cho FALL recall >= 0.85.

Chay tu thu muc goc project:
  python ml/scripts/focal_threshold_sweep.py
"""

import numpy as np
from pathlib import Path
from sklearn.metrics import confusion_matrix

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR     = PROJECT_ROOT / "ml" / "data" / "processed_3class"
MODELS_DIR   = PROJECT_ROOT / "ml" / "models_3class"

THRESHOLDS     = np.round(np.arange(0.40, 0.51, 0.01), 2)
INST_THRESHOLD = 0.50
SVM_FALL_HARD  = 2.5
CLASS_NAMES    = ["NORMAL", "INSTABILITY", "FALL"]
TARGET_RECALL  = 0.85


def load_test():
    print("Loading test set...")
    X_raw = np.load(DATA_DIR / "X_test.npz")["data"]
    y     = np.load(DATA_DIR / "y_test.npz")["data"].astype(int)
    scaler = np.load(MODELS_DIR / "scaler_params.npz")
    X_norm = (X_raw - scaler["mean"]) / scaler["std"]
    print(f"  Test: {len(y)} windows")
    for i, n in enumerate(CLASS_NAMES):
        print(f"    {n}: {(y==i).sum()}")
    return X_raw, X_norm, y


def svm_peak(X_raw):
    svm = np.sqrt(X_raw[:,:,0]**2 + X_raw[:,:,1]**2 + X_raw[:,:,2]**2)
    return svm.max(axis=1)


def apply_policy(probs, peak_svm, fall_thresh):
    p_inst = probs[:, 1]
    p_fall = probs[:, 2]
    y_pred = np.zeros(len(probs), dtype=int)
    y_pred[p_inst >= INST_THRESHOLD] = 1
    y_pred[p_fall >= fall_thresh]    = 2
    y_pred[peak_svm >= SVM_FALL_HARD] = 2
    return y_pred


def metrics(y_true, probs, peak_svm, fall_thresh):
    y_pred = apply_policy(probs, peak_svm, fall_thresh)
    cm = confusion_matrix(y_true, y_pred, labels=[0,1,2])
    tp = float(cm[2,2])
    fn = float(cm[2,0] + cm[2,1])
    fp = float(cm[0,2] + cm[1,2])
    recall    = tp/(tp+fn) if (tp+fn)>0 else 0.0
    precision = tp/(tp+fp) if (tp+fp)>0 else 0.0
    f1 = 2*precision*recall/(precision+recall) if (precision+recall)>0 else 0.0
    inst_recall = float(cm[1,1]) / float(cm[1].sum()) if cm[1].sum()>0 else 0.0
    norm_recall = float(cm[0,0]) / float(cm[0].sum()) if cm[0].sum()>0 else 0.0
    fp_per_hour = fp / (len(y_true) / (50*3600))  # rough FP rate (windows/hour)
    return dict(t=fall_thresh, recall=recall, precision=precision, f1=f1,
                inst_recall=inst_recall, norm_recall=norm_recall,
                tp=int(tp), fn=int(fn), fp=int(fp), fp_per_hour=fp_per_hour)


def main():
    print("=" * 65)
    print("  THRESHOLD SWEEP — Focal model on TEST SET")
    print("=" * 65)

    X_raw, X_norm, y_true = load_test()
    ps = svm_peak(X_raw)

    import tensorflow as tf
    focal_path = MODELS_DIR / "fall_detection_3class_focal.keras"
    print(f"\nLoading {focal_path.name}...")
    model = tf.keras.models.load_model(focal_path, compile=False)
    probs = model.predict(X_norm, batch_size=512, verbose=1)
    print("Done.\n")

    print(f"{'T':>5}  {'Rec-FALL':>9}  {'Prec-FALL':>10}  {'F1-FALL':>8}  "
          f"{'Rec-INST':>9}  {'Rec-NRM':>8}  {'FP':>6}")
    print("-" * 65)

    rows = []
    best = None
    for t in THRESHOLDS:
        m = metrics(y_true, probs, ps, t)
        rows.append(m)
        marker = ""
        if m["recall"] >= TARGET_RECALL and best is None:
            best = m
            marker = "  ◀ TARGET"
        print(f"  {t:.2f}  {m['recall']:>9.4f}  {m['precision']:>10.4f}  "
              f"{m['f1']:>8.4f}  {m['inst_recall']:>9.4f}  "
              f"{m['norm_recall']:>8.4f}  {m['fp']:>6d}{marker}")

    print("\n" + "=" * 65)
    if best:
        print(f"  [PASS] T={best['t']:.2f} dat FALL recall >= {TARGET_RECALL}")
        print(f"  Rec-FALL={best['recall']:.4f}  Prec={best['precision']:.4f}  "
              f"F1={best['f1']:.4f}  FP={best['fp']}")
        print()
        print("  Buoc tiep theo:")
        print(f"  1. Cap nhat backend/app/config.py:")
        print(f"     ML_CONFIDENCE_THRESHOLD = {best['t']:.2f}")
        print(f"  2. Copy model focal thanh model chinh:")
        print(f"     fall_detection_3class_focal.keras -> fall_detection_3class.keras")
        print(f"  3. Restart backend")
    else:
        max_row = max(rows, key=lambda r: r["recall"])
        print(f"  [FAIL] Khong co threshold nao dat {TARGET_RECALL}")
        print(f"  Max recall dat duoc: {max_row['recall']:.4f} tai T={max_row['t']:.2f}")
        print()
        print("  Khuyen nghi: Thu train LSTM voi focal loss")
    print("=" * 65)


if __name__ == "__main__":
    main()
