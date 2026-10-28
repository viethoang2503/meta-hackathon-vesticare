#include <Arduino.h>
#include "config.h"
#include "imu.h"
#include "imu_math.h"
#include "network.h"
#include "payload.h"
#include "transport_mode.h"

namespace {
constexpr int SAMPLE_RATE_HZ = 50;
constexpr unsigned long SAMPLE_INTERVAL_MS = 1000 / SAMPLE_RATE_HZ;
constexpr int BATCH_SIZE = 10;
constexpr int CALIBRATION_SAMPLES = 100; // ~2s at 50Hz
constexpr unsigned long WIFI_TIMEOUT_MS = 15000;
constexpr int LED_PIN = 2;
constexpr unsigned long USB_WATCHDOG_MS = 6000;

ImuOffsets g_offsets;
ImuSample g_batch[BATCH_SIZE];
int g_batch_count = 0;
unsigned long g_last_sample_ms = 0;
TransportController g_transport(USB_WATCHDOG_MS);
String g_serial_command;

void clearPendingBatch() {
    g_batch_count = 0;
}

void processSerialCommands(unsigned long now) {
    while (Serial.available() > 0) {
        const char incoming = static_cast<char>(Serial.read());
        if (incoming == '\n') {
            g_serial_command.trim();
            if (g_transport.handleCommand(g_serial_command.c_str(), now)) {
                clearPendingBatch();
                Serial.print("Transport mode: ");
                Serial.println(g_transport.isWifi() ? "WIFI" : "USB");
            }
            g_serial_command = "";
        } else if (incoming != '\r' && g_serial_command.length() < 32) {
            g_serial_command += incoming;
        }
    }
}
}

void setup() {
    Serial.begin(115200);
    pinMode(LED_PIN, OUTPUT);

    network::connectWiFi(WIFI_SSID, WIFI_PASSWORD, WIFI_TIMEOUT_MS);

    while (!imu::init()) {
        Serial.println("MPU6050 init failed, retrying...");
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
        delay(2000);
    }
    Serial.println("MPU6050 initialized");

    Serial.println("Calibrating (keep sensor still)...");
    g_offsets = imu::calibrate(CALIBRATION_SAMPLES);
    Serial.println("Calibration done");

    g_last_sample_ms = millis();
}

void loop() {
    unsigned long now = millis();
    processSerialCommands(now);
    if (g_transport.tick(now)) {
        clearPendingBatch();
        Serial.println("USB heartbeat timeout, returning to WIFI");
    }

    if (now - g_last_sample_ms < SAMPLE_INTERVAL_MS) {
        return;
    }
    g_last_sample_ms = now;

    ImuSample raw = imu::readRaw();
    ImuSample corrected = applyCalibration(raw, g_offsets);
    Serial.println(buildSamplePayload(corrected).c_str());

    if (!g_transport.isWifi()) {
        return;
    }

    g_batch[g_batch_count++] = corrected;

    if (g_batch_count >= BATCH_SIZE) {
        std::string payload = buildBatchPayload(
            PATIENT_ID, DEVICE_ID, FIRMWARE_VERSION, false, g_batch, g_batch_count
        );

        if (network::ensureConnected(WIFI_SSID, WIFI_PASSWORD, WIFI_TIMEOUT_MS)) {
            int status = network::sendBatch(BACKEND_URL, DEVICE_TOKEN, payload);
            Serial.print("Batch sent, HTTP status: ");
            Serial.println(status);
        } else {
            Serial.println("WiFi unavailable, dropping batch");
        }

        g_batch_count = 0;
    }
}
