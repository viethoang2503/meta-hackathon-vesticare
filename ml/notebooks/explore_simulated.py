import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os

# Create directories if not exist
os.makedirs('../evaluation', exist_ok=True)

# 1. Simulate "Normal Walking" Data
# Walking has a steady rhythm. The SVM (Signal Vector Magnitude) varies around 1G (gravity)
# Usually peaks around 1.2G to 1.5G during footsteps.
time_steps = np.linspace(0, 5, 250) # 5 seconds, 50Hz = 250 samples

# Base gravity + walking oscillations
walk_svm = 1.0 + 0.3 * np.sin(2 * np.pi * 1.5 * time_steps) + np.random.normal(0, 0.05, len(time_steps))

# 2. Simulate "Fall" Data
# Fall starts with standing/walking, then a sudden drop in SVM (freefall < 1G),
# followed by a huge spike when hitting the ground (> 2.5G), and then stillness (~1G).
fall_svm = 1.0 + np.random.normal(0, 0.05, len(time_steps))

# Inject the fall at t=2.0s
fall_idx = 100 # corresponding to 2.0s at 50Hz
# Freefall phase (weightlessness)
fall_svm[fall_idx:fall_idx+15] = np.linspace(1.0, 0.3, 15)
# Impact phase (huge spike)
fall_svm[fall_idx+15:fall_idx+20] = [1.8, 3.5, 4.2, 2.8, 1.5]
# Post-fall stillness (lying down)
fall_svm[fall_idx+20:] = 1.0 + np.random.normal(0, 0.02, len(time_steps) - (fall_idx+20))

# 3. Plotting the data to understand the thresholds
plt.figure(figsize=(12, 6))

plt.plot(time_steps, walk_svm, label="Normal Walking (ADL)", color='green', alpha=0.7)
plt.plot(time_steps, fall_svm, label="Sudden Fall", color='red', linewidth=2)

# Draw Threshold Lines based on our research
plt.axhline(y=2.5, color='orange', linestyle='--', label="Fall Threshold (> 2.5G)")
plt.axhline(y=1.5, color='blue', linestyle=':', label="Sway/Walk Peak (~1.5G)")

plt.title("Vesti-Care: Fall Detection SVM Analysis (Simulated SisFall Data)")
plt.xlabel("Time (Seconds)")
plt.ylabel("Signal Vector Magnitude (G)")
plt.legend()
plt.grid(True, alpha=0.3)

# Save chart
plt.tight_layout()
chart_path = '../evaluation/svm_analysis_chart.png'
plt.savefig(chart_path, dpi=150)
print(f"Chart saved successfully at {chart_path}")
