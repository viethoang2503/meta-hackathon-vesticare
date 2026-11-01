#pragma once

struct ImuSample {
    float ax, ay, az;
    float gx, gy, gz;
    float svm;
};

// Gyro bias only. The accelerometer stays raw, gravity included, like SisFall:
// subtracting a resting mean would bake one orientation into every later sample.
struct ImuOffsets {
    float gx, gy, gz;
};

float computeSVM(float ax, float ay, float az);
ImuOffsets computeOffsets(const ImuSample raw_samples[], int count);
ImuSample applyCalibration(const ImuSample& raw, const ImuOffsets& offsets);
