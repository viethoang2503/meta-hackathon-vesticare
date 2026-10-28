#pragma once
#include <string>
#include "imu_math.h"

std::string buildSamplePayload(const ImuSample& sample);

std::string buildBatchPayload(
    int patient_id,
    const std::string& device_id,
    const std::string& firmware_version,
    bool ntp_synced,
    const ImuSample samples[],
    int count
);
