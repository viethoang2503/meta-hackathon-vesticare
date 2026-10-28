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
    bool ntp_synced,
    const ImuSample samples[],
    int count
) {
    JsonDocument doc;
    doc["patient_id"] = patient_id;
    doc["device_id"] = device_id;
    doc["firmware_version"] = firmware_version;
    doc["ntp_synced"] = ntp_synced;
    doc["transport"] = "wifi";

    JsonArray arr = doc["samples"].to<JsonArray>();
    for (int i = 0; i < count; i++) {
        JsonObject s = arr.add<JsonObject>();
        s["ax"] = samples[i].ax;
        s["ay"] = samples[i].ay;
        s["az"] = samples[i].az;
        s["gx"] = samples[i].gx;
        s["gy"] = samples[i].gy;
        s["gz"] = samples[i].gz;
        s["svm"] = samples[i].svm;
    }

    std::string output;
    serializeJson(doc, output);
    return output;
}
