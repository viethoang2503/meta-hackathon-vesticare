#include "payload.h"
#include <ArduinoJson.h>

std::string buildSamplePayload(const ImuSample& sample) {
    JsonDocument doc;
    doc["ax"] = sample.ax;
    doc["ay"] = sample.ay;
    doc["az"] = sample.az;
    doc["gx"] = sample.gx;
    doc["gy"] = sample.gy;
    doc["gz"] = sample.gz;

    std::string output;
    serializeJson(doc, output);
    return output;
}

std::string buildBatchPayload(
    int patient_id,
    const std::string& device_id,
    const std::string& firmware_version,
    const std::string& boot_id,
    uint32_t batch_id,
    uint32_t dropped_at_source,
    const StampedSample samples[],
    int count
) {
    JsonDocument doc;
    doc["patient_id"] = patient_id;
    doc["device_id"] = device_id;
    doc["firmware_version"] = firmware_version;
    doc["transport"] = "wifi";
    doc["boot_id"] = boot_id;
    doc["batch_id"] = batch_id;
    doc["dropped_at_source"] = dropped_at_source;

    JsonArray arr = doc["samples"].to<JsonArray>();
    for (int i = 0; i < count; i++) {
        const ImuSample& imu = samples[i].imu;
        JsonObject s = arr.add<JsonObject>();
        s["sample_seq"] = samples[i].seq;
        s["device_ms"] = samples[i].device_ms;
        s["ax"] = imu.ax;
        s["ay"] = imu.ay;
        s["az"] = imu.az;
        s["gx"] = imu.gx;
        s["gy"] = imu.gy;
        s["gz"] = imu.gz;
        s["svm"] = imu.svm;
    }

    std::string output;
    serializeJson(doc, output);
    return output;
}

std::string buildClockRequest(const std::string& device_id, const std::string& boot_id) {
    JsonDocument doc;
    doc["device_id"] = device_id;
    doc["boot_id"] = boot_id;
    std::string output;
    serializeJson(doc, output);
    return output;
}

bool parseClockReply(const std::string& json, double* t1, double* t2) {
    JsonDocument doc;
    if (deserializeJson(doc, json) != DeserializationError::Ok || !doc["t1"].is<double>() || !doc["t2"].is<double>()) {
        return false;
    }
    *t1 = doc["t1"].as<double>();
    *t2 = doc["t2"].as<double>();
    return true;
}

std::string buildClockReport(const std::string& device_id, const std::string& boot_id,
                             const ClockRound rounds[], int count) {
    JsonDocument doc;
    doc["device_id"] = device_id;
    doc["boot_id"] = boot_id;
    JsonArray arr = doc["rounds"].to<JsonArray>();
    for (int i = 0; i < count; i++) {
        JsonObject r = arr.add<JsonObject>();
        r["t0"] = rounds[i].t0;
        r["t1"] = rounds[i].t1;
        r["t2"] = rounds[i].t2;
        r["t3"] = rounds[i].t3;
    }
    std::string output;
    serializeJson(doc, output);
    return output;
}
