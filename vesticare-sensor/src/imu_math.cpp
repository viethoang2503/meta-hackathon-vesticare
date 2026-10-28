#include "imu_math.h"
#include <cmath>

float computeSVM(float ax, float ay, float az) {
    return std::sqrt(ax * ax + ay * ay + az * az);
}

ImuOffsets computeOffsets(const ImuSample raw_samples[], int count) {
    float sum_ax = 0, sum_ay = 0, sum_az = 0;
    float sum_gx = 0, sum_gy = 0, sum_gz = 0;

    for (int i = 0; i < count; i++) {
        sum_ax += raw_samples[i].ax;
        sum_ay += raw_samples[i].ay;
        sum_az += raw_samples[i].az;
        sum_gx += raw_samples[i].gx;
        sum_gy += raw_samples[i].gy;
        sum_gz += raw_samples[i].gz;
    }

    ImuOffsets offsets;
    offsets.ax = sum_ax / count;
    offsets.ay = sum_ay / count;
    offsets.az = (sum_az / count) - 1.0f;  // az reads 1g at rest, not 0
    offsets.gx = sum_gx / count;
    offsets.gy = sum_gy / count;
    offsets.gz = sum_gz / count;
    return offsets;
}

ImuSample applyCalibration(const ImuSample& raw, const ImuOffsets& offsets) {
    ImuSample corrected;
    corrected.ax = raw.ax - offsets.ax;
    corrected.ay = raw.ay - offsets.ay;
    corrected.az = raw.az - offsets.az;
    corrected.gx = raw.gx - offsets.gx;
    corrected.gy = raw.gy - offsets.gy;
    corrected.gz = raw.gz - offsets.gz;
    corrected.svm = computeSVM(corrected.ax, corrected.ay, corrected.az);
    return corrected;
}
