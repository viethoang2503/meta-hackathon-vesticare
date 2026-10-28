#pragma once
#include "imu_math.h"

namespace imu {

bool init();
ImuSample readRaw();
ImuOffsets calibrate(int sample_count);

}
