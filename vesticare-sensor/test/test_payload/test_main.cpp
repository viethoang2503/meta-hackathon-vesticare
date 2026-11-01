#include <unity.h>
#include <ArduinoJson.h>
#include "payload.h"

void setUp(void) {}
void tearDown(void) {}

namespace {
StampedSample stamped(uint32_t seq, uint32_t device_ms) {
    return StampedSample{seq, device_ms, {0.01f, -0.02f, 0.99f, 0.1f, -0.1f, 0.05f, 1.0f}};
}
}

void test_batch_carries_the_live_contract_fields(void) {
    StampedSample samples[2] = {stamped(41, 12340), stamped(42, 12360)};
    std::string json = buildBatchPayload(1, "esp32-01", "2.0.0", "a1b2c3d4", 7, 3, samples, 2);

    JsonDocument doc;
    TEST_ASSERT_EQUAL(DeserializationError::Ok, deserializeJson(doc, json).code());
    TEST_ASSERT_EQUAL_INT(1, doc["patient_id"].as<int>());
    TEST_ASSERT_EQUAL_STRING("esp32-01", doc["device_id"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("2.0.0", doc["firmware_version"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("wifi", doc["transport"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("a1b2c3d4", doc["boot_id"].as<const char*>());
    TEST_ASSERT_EQUAL_UINT32(7, doc["batch_id"].as<uint32_t>());
    TEST_ASSERT_EQUAL_UINT32(3, doc["dropped_at_source"].as<uint32_t>());
    TEST_ASSERT_EQUAL_INT(2, doc["samples"].size());
    TEST_ASSERT_EQUAL_UINT32(42, doc["samples"][1]["sample_seq"].as<uint32_t>());
    TEST_ASSERT_EQUAL_UINT32(12360, doc["samples"][1]["device_ms"].as<uint32_t>());
}

void test_batch_sample_values(void) {
    StampedSample samples[1] = {stamped(0, 0)};
    JsonDocument doc;
    deserializeJson(doc, buildBatchPayload(1, "esp32-01", "2.0.0", "b", 0, 0, samples, 1));
    JsonObject s = doc["samples"][0];
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.01f, s["ax"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.02f, s["ay"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.99f, s["az"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.1f, s["gx"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.1f, s["gy"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.05f, s["gz"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 1.0f, s["svm"].as<float>());
}

void test_buildSamplePayload_contains_only_six_axes(void) {
    ImuSample sample = {0.01f, -0.02f, 0.99f, 0.1f, -0.1f, 0.05f, 1.0f};
    JsonDocument doc;
    TEST_ASSERT_EQUAL(DeserializationError::Ok, deserializeJson(doc, buildSamplePayload(sample)).code());
    TEST_ASSERT_EQUAL_INT(6, doc.size());
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.99f, doc["az"].as<float>());
}

void test_clock_reply_is_parsed_and_rejected_when_malformed(void) {
    double t1 = 0, t2 = 0;
    TEST_ASSERT_TRUE(parseClockReply("{\"t1\": 123456.25, \"t2\": 123456.75}", &t1, &t2));
    TEST_ASSERT_EQUAL_DOUBLE(123456.25, t1);
    TEST_ASSERT_EQUAL_DOUBLE(123456.75, t2);
    TEST_ASSERT_FALSE(parseClockReply("{\"detail\": \"Set DEVICE_TOKEN\"}", &t1, &t2));
    TEST_ASSERT_FALSE(parseClockReply("not json", &t1, &t2));
}

void test_clock_report_lists_every_round(void) {
    ClockRound rounds[2] = {{100, 5000.5, 5001.0, 140}, {200, 5100.5, 5101.0, 230}};
    JsonDocument doc;
    deserializeJson(doc, buildClockReport("esp32-01", "a1b2c3d4", rounds, 2));
    TEST_ASSERT_EQUAL_STRING("a1b2c3d4", doc["boot_id"].as<const char*>());
    TEST_ASSERT_EQUAL_INT(2, doc["rounds"].size());
    TEST_ASSERT_EQUAL_DOUBLE(5100.5, doc["rounds"][1]["t1"].as<double>());
    TEST_ASSERT_EQUAL_DOUBLE(230, doc["rounds"][1]["t3"].as<double>());
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_batch_carries_the_live_contract_fields);
    RUN_TEST(test_batch_sample_values);
    RUN_TEST(test_buildSamplePayload_contains_only_six_axes);
    RUN_TEST(test_clock_reply_is_parsed_and_rejected_when_malformed);
    RUN_TEST(test_clock_report_lists_every_round);
    return UNITY_END();
}
