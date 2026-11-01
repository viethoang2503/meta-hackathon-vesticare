#pragma once
#include <cstdint>
#include <string>
#include "imu_math.h"
#include "sample_buffer.h"

std::string buildSamplePayload(const ImuSample& sample);

// Clock exchange (backend/app/services/clock_sync.py): t0/t3 are millis() on the device,
// t1/t2 the backend's receive and reply times.
struct ClockRound {
    double t0, t1, t2, t3;
};

std::string buildClockRequest(const std::string& device_id, const std::string& boot_id);
bool parseClockReply(const std::string& json, double* t1, double* t2);
std::string buildClockReport(const std::string& device_id, const std::string& boot_id,
                             const ClockRound rounds[], int count);

// The MVP 2 batch contract (docs/spatial/MVP2_ARCHITECTURE.md): every sample carries
// sample_seq and device_ms; dropped_at_source is cumulative since boot.
std::string buildBatchPayload(
    int patient_id,
    const std::string& device_id,
    const std::string& firmware_version,
    const std::string& boot_id,
    uint32_t batch_id,
    uint32_t dropped_at_source,
    const StampedSample samples[],
    int count
);
