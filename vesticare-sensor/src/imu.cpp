#include "imu.h"
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>

namespace {
Adafruit_MPU6050 mpu;
constexpr float STANDARD_GRAVITY = 9.80665f;   // m/s^2 per g
constexpr float RAD_TO_DEG_F = 57.2957795f;     // deg per rad; model was trained on deg/s
}

namespace imu {

bool init() {
    Wire.begin(21, 22);
    if (!mpu.begin()) {
        return false;
    }
    // The library default (+-2 g, +-500 deg/s) clips fall impacts, which reach
    // several g; SisFall was recorded at +-16 g and +-2000 deg/s.
    mpu.setAccelerometerRange(MPU6050_RANGE_16_G);
    mpu.setGyroRange(MPU6050_RANGE_2000_DEG);
    return true;
}

ImuSample readRaw() {
    sensors_event_t a, g, temp;
    mpu.getEvent(&a, &g, &temp);

    ImuSample sample;
    sample.ax = a.acceleration.x / STANDARD_GRAVITY;
    sample.ay = a.acceleration.y / STANDARD_GRAVITY;
    sample.az = a.acceleration.z / STANDARD_GRAVITY;
    sample.gx = g.gyro.x * RAD_TO_DEG_F;
    sample.gy = g.gyro.y * RAD_TO_DEG_F;
    sample.gz = g.gyro.z * RAD_TO_DEG_F;
    sample.svm = computeSVM(sample.ax, sample.ay, sample.az);
    return sample;
}

ImuOffsets calibrate(int sample_count) {
    ImuSample* samples = new ImuSample[sample_count];
    for (int i = 0; i < sample_count; i++) {
        samples[i] = readRaw();
        delay(20);
    }
    ImuOffsets offsets = computeOffsets(samples, sample_count);
    delete[] samples;
    return offsets;
}

}
