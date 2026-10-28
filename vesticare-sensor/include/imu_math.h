#pragma once

struct ImuSample {
    float ax, ay, az;
    float gx, gy, gz;
    float svm;
};

struct ImuOffsets {
    float ax, ay, az;
    float gx, gy, gz;
};

float computeSVM(float ax, float ay, float az);
ImuOffsets computeOffsets(const ImuSample raw_samples[], int count);
ImuSample applyCalibration(const ImuSample& raw, const ImuOffsets& offsets);
