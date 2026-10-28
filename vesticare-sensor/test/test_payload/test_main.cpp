#include <unity.h>
#include <ArduinoJson.h>
#include "payload.h"

void setUp(void) {}
void tearDown(void) {}

void test_buildBatchPayload_top_level_fields(void) {
    ImuSample samples[1] = { {0.01f, -0.02f, 0.99f, 0.1f, -0.1f, 0.05f, 1.0f} };
    std::string json = buildBatchPayload(1, "esp32-abc123", "1.0.0", false, samples, 1);

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, json);
    TEST_ASSERT_EQUAL(DeserializationError::Ok, err.code());

    TEST_ASSERT_EQUAL_INT(1, doc["patient_id"].as<int>());
    TEST_ASSERT_EQUAL_STRING("esp32-abc123", doc["device_id"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("1.0.0", doc["firmware_version"].as<const char*>());
    TEST_ASSERT_FALSE(doc["ntp_synced"].as<bool>());
    TEST_ASSERT_EQUAL_STRING("wifi", doc["transport"].as<const char*>());
    TEST_ASSERT_EQUAL_INT(1, doc["samples"].size());
}

void test_buildSamplePayload_contains_only_six_axes(void) {
    ImuSample sample = {0.01f, -0.02f, 0.99f, 0.1f, -0.1f, 0.05f, 1.0f};
    std::string json = buildSamplePayload(sample);

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, json);
    TEST_ASSERT_EQUAL(DeserializationError::Ok, err.code());
    TEST_ASSERT_EQUAL_INT(6, doc.size());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.01f, doc["ax"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.02f, doc["ay"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.99f, doc["az"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.1f, doc["gx"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.1f, doc["gy"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.05f, doc["gz"].as<float>());
}

void test_buildBatchPayload_sample_fields(void) {
    ImuSample samples[1] = { {0.01f, -0.02f, 0.99f, 0.1f, -0.1f, 0.05f, 1.0f} };
    std::string json = buildBatchPayload(1, "esp32-abc123", "1.0.0", false, samples, 1);

    JsonDocument doc;
    deserializeJson(doc, json);
    JsonObject sample0 = doc["samples"][0];

    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.01f, sample0["ax"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.02f, sample0["ay"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.99f, sample0["az"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.1f, sample0["gx"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.1f, sample0["gy"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.05f, sample0["gz"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 1.0f, sample0["svm"].as<float>());
}

void test_buildBatchPayload_multiple_samples(void) {
    ImuSample samples[2] = {
        {0.0f, 0.0f, 1.0f, 0.0f, 0.0f, 0.0f, 1.0f},
        {0.1f, 0.1f, 1.0f, 0.0f, 0.0f, 0.0f, 1.014f}
    };
    std::string json = buildBatchPayload(2, "esp32-def456", "1.0.0", false, samples, 2);

    JsonDocument doc;
    deserializeJson(doc, json);
    TEST_ASSERT_EQUAL_INT(2, doc["samples"].size());
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_buildBatchPayload_top_level_fields);
    RUN_TEST(test_buildSamplePayload_contains_only_six_axes);
    RUN_TEST(test_buildBatchPayload_sample_fields);
    RUN_TEST(test_buildBatchPayload_multiple_samples);
    return UNITY_END();
}
