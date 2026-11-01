#include "imu_math.h"
#include <cmath>

float computeSVM(float ax, float ay, float az) {
    return std::sqrt(ax * ax + ay * ay + az * az);
}

ImuOffsets computeOffsets(const ImuSample raw_samples[], int count) {
    float sum_gx = 0, sum_gy = 0, sum_gz = 0;
    for (int i = 0; i < count; i++) {
        sum_gx += raw_samples[i].gx;
        sum_gy += raw_samples[i].gy;
        sum_gz += raw_samples[i].gz;
    }
    return ImuOffsets{sum_gx / count, sum_gy / count, sum_gz / count};
}

ImuSample applyCalibration(const ImuSample& raw, const ImuOffsets& offsets) {
    ImuSample corrected = raw;
    corrected.gx = raw.gx - offsets.gx;
    corrected.gy = raw.gy - offsets.gy;
    corrected.gz = raw.gz - offsets.gz;
    corrected.svm = computeSVM(raw.ax, raw.ay, raw.az);
    return corrected;
}
