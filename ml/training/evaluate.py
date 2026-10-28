"""
evaluate.py

Danh gia va so sanh cac model Fall Detection:
1. Load model Random Forest + 1D-CNN
2. Tinh Confusion Matrix, Classification Report, ROC-AUC
3. Ve bieu do so sanh
4. Luu ket qua vao ml/evaluation/
"""

import numpy as np
import matplotlib.pyplot as plt
import pickle
from pathlib import Path

# ===========================================================================
# CAU HINH
# ===========================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "ml" / "data" / "processed"
MODELS_DIR = PROJECT_ROOT / "ml" / "models"
EVAL_DIR = PROJECT_ROOT / "ml" / "evaluation"
EVAL_DIR.mkdir(parents=True, exist_ok=True)

LABEL_NAMES = ['NORMAL', 'FALL']


def load_data():
    """Load test data."""
    X_test = np.load(DATA_DIR / "X_test.npz")['data']
    y_test = np.load(DATA_DIR / "y_test.npz")['data']
    return X_test, y_test


def evaluate_random_forest(X_test, y_test):
    """Danh gia Random Forest model."""
    from sklearn.metrics import (
        accuracy_score, classification_report,
        confusion_matrix, roc_curve, auc
    )

    # Load model
    with open(MODELS_DIR / "random_forest.pkl", 'rb') as f:
        rf = pickle.load(f)

    # Load features da trich xuat
    X_test_feat = np.load(DATA_DIR / "X_test_features.npz")['data']

    # Du doan
    y_pred = rf.predict(X_test_feat)
    y_prob = rf.predict_proba(X_test_feat)[:, 1]  # Xac suat class FALL

    acc = accuracy_score(y_test, y_pred)
    cm = confusion_matrix(y_test, y_pred)
    report = classification_report(y_test, y_pred, target_names=LABEL_NAMES)
    fpr, tpr, _ = roc_curve(y_test, y_prob)
    roc_auc = auc(fpr, tpr)

    return {
        'name': 'Random Forest',
        'accuracy': acc,
        'confusion_matrix': cm,
        'report': report,
        'fpr': fpr,
        'tpr': tpr,
        'roc_auc': roc_auc,
        'y_pred': y_pred,
        'y_prob': y_prob,
    }


def evaluate_cnn(X_test, y_test):
    """Danh gia 1D-CNN model."""
    import tensorflow as tf
    from sklearn.metrics import (
        accuracy_score, classification_report,
        confusion_matrix, roc_curve, auc
    )

    # Load model
    # Thu load .keras truoc, neu khong co thi thu .h5
    keras_path = MODELS_DIR / "fall_detection.keras"
    h5_path = MODELS_DIR / "fall_detection.h5"
    if keras_path.exists():
        model = tf.keras.models.load_model(keras_path)
    else:
        model = tf.keras.models.load_model(h5_path)

    # Chuan hoa du lieu test (model da duoc train voi data chuan hoa)
    scaler_path = MODELS_DIR / "scaler_params.npz"
    if scaler_path.exists():
        scaler = np.load(scaler_path)
        X_test = (X_test - scaler['mean']) / scaler['std']

    # Du doan
    y_prob_all = model.predict(X_test, verbose=0)
    y_pred = np.argmax(y_prob_all, axis=1)
    y_prob = y_prob_all[:, 1]  # Xac suat class FALL

    acc = accuracy_score(y_test, y_pred)
    cm = confusion_matrix(y_test, y_pred)
    report = classification_report(y_test, y_pred, target_names=LABEL_NAMES)
    fpr, tpr, _ = roc_curve(y_test, y_prob)
    roc_auc = auc(fpr, tpr)

    return {
        'name': '1D-CNN',
        'accuracy': acc,
        'confusion_matrix': cm,
        'report': report,
        'fpr': fpr,
        'tpr': tpr,
        'roc_auc': roc_auc,
        'y_pred': y_pred,
        'y_prob': y_prob,
    }


def plot_confusion_matrices(results_list):
    """Ve Confusion Matrix cho tat ca model."""
    import seaborn as sns

    n = len(results_list)
    fig, axes = plt.subplots(1, n, figsize=(6 * n, 5))
    if n == 1:
        axes = [axes]

    for ax, result in zip(axes, results_list):
        cm = result['confusion_matrix']
        sns.heatmap(
            cm, annot=True, fmt='d', cmap='Blues',
            xticklabels=LABEL_NAMES, yticklabels=LABEL_NAMES,
            ax=ax, cbar=False
        )
        ax.set_title(f"{result['name']}\nAccuracy: {result['accuracy']:.1%}",
                     fontsize=13, fontweight='bold')
        ax.set_ylabel('Actual')
        ax.set_xlabel('Predicted')

    plt.tight_layout()
    path = EVAL_DIR / "confusion_matrices.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  Confusion Matrix da luu: {path.name}")


def plot_roc_curves(results_list):
    """Ve ROC Curve cho tat ca model tren 1 bieu do."""
    fig, ax = plt.subplots(figsize=(8, 6))

    colors = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12']

    for i, result in enumerate(results_list):
        ax.plot(
            result['fpr'], result['tpr'],
            color=colors[i % len(colors)], linewidth=2,
            label=f"{result['name']} (AUC = {result['roc_auc']:.3f})"
        )

    # Duong cheo (random classifier)
    ax.plot([0, 1], [0, 1], 'k--', alpha=0.5, label='Random (AUC = 0.500)')

    ax.set_title('ROC Curve - So sanh cac Model', fontsize=14, fontweight='bold')
    ax.set_xlabel('False Positive Rate')
    ax.set_ylabel('True Positive Rate')
    ax.legend(loc='lower right', fontsize=11)
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1.02])
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    path = EVAL_DIR / "roc_curves.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  ROC Curves da luu: {path.name}")


def plot_training_history():
    """Ve bieu do training history cua 1D-CNN."""
    history_path = MODELS_DIR / "training_history.npz"
    if not history_path.exists():
        print("  Khong tim thay training history.")
        return

    data = np.load(history_path)
    acc = data['accuracy']
    val_acc = data['val_accuracy']
    loss = data['loss']
    val_loss = data['val_loss']
    epochs = range(1, len(acc) + 1)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Accuracy
    ax1.plot(epochs, acc, 'b-', linewidth=2, label='Train Accuracy')
    ax1.plot(epochs, val_acc, 'r-', linewidth=2, label='Validation Accuracy')
    ax1.set_title('1D-CNN: Accuracy qua cac Epoch', fontsize=13, fontweight='bold')
    ax1.set_xlabel('Epoch')
    ax1.set_ylabel('Accuracy')
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # Loss
    ax2.plot(epochs, loss, 'b-', linewidth=2, label='Train Loss')
    ax2.plot(epochs, val_loss, 'r-', linewidth=2, label='Validation Loss')
    ax2.set_title('1D-CNN: Loss qua cac Epoch', fontsize=13, fontweight='bold')
    ax2.set_xlabel('Epoch')
    ax2.set_ylabel('Loss')
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    path = EVAL_DIR / "training_history.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  Training History da luu: {path.name}")


def plot_model_comparison(results_list):
    """Ve bieu do so sanh tong hop cac model."""
    fig, ax = plt.subplots(figsize=(8, 5))

    names = [r['name'] for r in results_list]
    accuracies = [r['accuracy'] for r in results_list]
    aucs = [r['roc_auc'] for r in results_list]

    x = np.arange(len(names))
    width = 0.35

    bars1 = ax.bar(x - width/2, accuracies, width, label='Accuracy',
                   color='#3498db', alpha=0.85)
    bars2 = ax.bar(x + width/2, aucs, width, label='ROC-AUC',
                   color='#e74c3c', alpha=0.85)

    # Gan gia tri len thanh
    for bar in bars1:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., h + 0.01,
                f'{h:.1%}', ha='center', va='bottom', fontweight='bold')
    for bar in bars2:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., h + 0.01,
                f'{h:.3f}', ha='center', va='bottom', fontweight='bold')

    ax.set_title('So sanh Model: Accuracy vs ROC-AUC', fontsize=14, fontweight='bold')
    ax.set_ylabel('Score')
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=12)
    ax.legend(fontsize=11)
    ax.set_ylim(0, 1.15)
    ax.grid(True, axis='y', alpha=0.3)

    plt.tight_layout()
    path = EVAL_DIR / "model_comparison.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  Model Comparison da luu: {path.name}")


def main():
    print("=" * 60)
    print("  DANH GIA MODEL FALL DETECTION")
    print("=" * 60)

    # Load du lieu test
    X_test, y_test = load_data()
    print(f"\n  Test set: {len(y_test)} windows")
    print(f"  NORMAL: {np.sum(y_test==0)}, FALL: {np.sum(y_test==1)}")

    results = []

    # 1. Danh gia Random Forest
    print("\n" + "-" * 60)
    print("  DANH GIA: RANDOM FOREST")
    print("-" * 60)
    rf_result = evaluate_random_forest(X_test, y_test)
    results.append(rf_result)
    print(f"\n  Accuracy: {rf_result['accuracy']:.1%}")
    print(f"  ROC-AUC : {rf_result['roc_auc']:.3f}")
    print(f"\n{rf_result['report']}")

    # 2. Danh gia 1D-CNN
    print("-" * 60)
    print("  DANH GIA: 1D-CNN")
    print("-" * 60)
    cnn_result = evaluate_cnn(X_test, y_test)
    results.append(cnn_result)
    print(f"\n  Accuracy: {cnn_result['accuracy']:.1%}")
    print(f"  ROC-AUC : {cnn_result['roc_auc']:.3f}")
    print(f"\n{cnn_result['report']}")

    # 3. Ve bieu do
    print("-" * 60)
    print("  DANG VE BIEU DO...")
    print("-" * 60)
    plot_confusion_matrices(results)
    plot_roc_curves(results)
    plot_training_history()
    plot_model_comparison(results)

    # 4. Kiem tra TFLite model size
    tflite_path = MODELS_DIR / "fall_detection.tflite"
    if tflite_path.exists():
        tflite_size = tflite_path.stat().st_size / 1024
        print(f"\n  TFLite model size: {tflite_size:.1f} KB", end="")
        if tflite_size < 100:
            print(" -> PASS (< 100KB)")
        else:
            print(" -> CANH BAO (> 100KB)")

    # 5. Tong ket
    print("\n" + "=" * 60)
    print("  TONG KET")
    print("=" * 60)
    print(f"\n  {'Model':<20} {'Accuracy':>10} {'ROC-AUC':>10}")
    print(f"  {'-'*40}")
    for r in results:
        print(f"  {r['name']:<20} {r['accuracy']:>9.1%} {r['roc_auc']:>9.3f}")

    best = max(results, key=lambda r: r['accuracy'])
    print(f"\n  Model tot nhat: {best['name']} ({best['accuracy']:.1%})")

    print(f"\n  Tat ca bieu do da luu tai: {EVAL_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
