#include <Arduino.h>
#include <atomic>
#include <esp_random.h>
#include "config.h"
#include "imu.h"
#include "imu_math.h"
#include "network.h"
#include "payload.h"
#include "sample_buffer.h"
#include "transport_mode.h"

// Sampling and upload run as separate tasks joined by a bounded buffer
// (docs/spatial/MVP2_ARCHITECTURE.md, "Firmware và đồng hồ"): an HTTP request
// can take hundreds of ms, and doing it in the sampling loop skipped samples.

namespace {
constexpr int SAMPLE_RATE_HZ = 50;
constexpr TickType_t SAMPLE_PERIOD = pdMS_TO_TICKS(1000 / SAMPLE_RATE_HZ);
constexpr size_t BATCH_SIZE = 10;
constexpr size_t CATCH_UP_BATCH = 50;   // after a stall, send the backlog faster than it grows
constexpr size_t BUFFER_SAMPLES = 1500; // 30 s at 50 Hz
constexpr int CALIBRATION_SAMPLES = 100;
constexpr unsigned long WIFI_TIMEOUT_MS = 15000;
constexpr int LED_PIN = 2;
constexpr unsigned long USB_WATCHDOG_MS = 6000;
constexpr unsigned long STATUS_EVERY_MS = 5000;
constexpr int CLOCK_ROUNDS = 5;
constexpr unsigned long CLOCK_RESYNC_MS = 60000;

ImuOffsets g_offsets;
SampleBuffer<BUFFER_SAMPLES> g_buffer;
portMUX_TYPE g_buffer_lock = portMUX_INITIALIZER_UNLOCKED;
std::atomic<bool> g_wifi_mode{true};
std::atomic<uint32_t> g_acquired{0};
std::atomic<uint32_t> g_late_reads{0};
std::atomic<int> g_last_http{0};
char g_boot_id[9];
TransportController g_transport(USB_WATCHDOG_MS);
String g_serial_command;

void sampleTask(void*) {
    TickType_t wake = xTaskGetTickCount();
    uint32_t seq = 0;
    for (;;) {
        // xTaskDelayUntil returns pdFALSE when the deadline had already passed.
        if (xTaskDelayUntil(&wake, SAMPLE_PERIOD) == pdFALSE) {
            g_late_reads++;
        }
        StampedSample s{seq++, static_cast<uint32_t>(millis()), applyCalibration(imu::readRaw(), g_offsets)};
        g_acquired = seq;
        if (g_wifi_mode) {
            portENTER_CRITICAL(&g_buffer_lock);
            g_buffer.push(s);
            portEXIT_CRITICAL(&g_buffer_lock);
        } else {
            Serial.println(buildSamplePayload(s.imu).c_str());
        }
    }
}

// Five request/reply rounds, then one report; the backend keeps the lowest-latency round.
bool syncClock() {
    ClockRound rounds[CLOCK_ROUNDS];
    const std::string request = buildClockRequest(DEVICE_ID, g_boot_id);
    for (int i = 0; i < CLOCK_ROUNDS; i++) {
        std::string reply;
        double t0 = millis();
        int status = network::post(BACKEND_URL, "/api/sensor/clock", DEVICE_TOKEN, request, &reply);
        double t3 = millis();
        if (status != 200 || !parseClockReply(reply, &rounds[i].t1, &rounds[i].t2)) {
            Serial.printf("Clock round failed, HTTP %d\n", status);
            return false;
        }
        rounds[i].t0 = t0;
        rounds[i].t3 = t3;
    }
    std::string reply;
    int status = network::post(BACKEND_URL, "/api/sensor/clock/report", DEVICE_TOKEN,
                               buildClockReport(DEVICE_ID, g_boot_id, rounds, CLOCK_ROUNDS), &reply);
    Serial.printf("Clock sync HTTP %d %s\n", status, reply.c_str());
    return status == 200;
}

void uploadTask(void*) {
    static StampedSample batch[CATCH_UP_BATCH];
    uint32_t batch_id = 0;
    unsigned long last_sync = 0;
    bool synced = false;
    for (;;) {
        if (g_wifi_mode && (!synced || millis() - last_sync >= CLOCK_RESYNC_MS)
                && network::ensureConnected(WIFI_SSID, WIFI_PASSWORD, WIFI_TIMEOUT_MS)) {
            synced = syncClock();
            last_sync = millis();  // a failed sync retries on the next minute, not in a tight loop
        }
        size_t waiting, n = 0;
        uint32_t dropped;
        portENTER_CRITICAL(&g_buffer_lock);
        waiting = g_buffer.size();
        dropped = g_buffer.dropped();
        if (g_wifi_mode && waiting >= BATCH_SIZE) {
            n = g_buffer.peek(batch, waiting > 2 * BATCH_SIZE ? CATCH_UP_BATCH : BATCH_SIZE);
        }
        portEXIT_CRITICAL(&g_buffer_lock);
        if (n == 0) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (!network::ensureConnected(WIFI_SSID, WIFI_PASSWORD, WIFI_TIMEOUT_MS)) {
            continue;  // samples stay buffered; the oldest drop if the outage outlasts the buffer
        }
        std::string body = buildBatchPayload(PATIENT_ID, DEVICE_ID, FIRMWARE_VERSION, g_boot_id,
                                             batch_id, dropped, batch, static_cast<int>(n));
        int status = network::sendBatch(BACKEND_URL, DEVICE_TOKEN, body);
        g_last_http = status;
        // 2xx: delivered. 4xx: the backend will refuse it again, so do not retry forever.
        // Network errors and 5xx keep the batch; the backend skips samples it already has.
        if (status >= 200 && status < 500) {
            portENTER_CRITICAL(&g_buffer_lock);
            g_buffer.popThrough(batch[n - 1].seq);
            portEXIT_CRITICAL(&g_buffer_lock);
            batch_id++;
        } else {
            vTaskDelay(pdMS_TO_TICKS(200));
        }
    }
}

void processSerialCommands(unsigned long now) {
    while (Serial.available() > 0) {
        const char incoming = static_cast<char>(Serial.read());
        if (incoming == '\n') {
            g_serial_command.trim();
            if (g_transport.handleCommand(g_serial_command.c_str(), now)) {
                g_wifi_mode = g_transport.isWifi();
                portENTER_CRITICAL(&g_buffer_lock);
                g_buffer.clear();
                portEXIT_CRITICAL(&g_buffer_lock);
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
    snprintf(g_boot_id, sizeof(g_boot_id), "%08lx", static_cast<unsigned long>(esp_random()));

    network::connectWiFi(WIFI_SSID, WIFI_PASSWORD, WIFI_TIMEOUT_MS);

    while (!imu::init()) {
        Serial.println("MPU6050 init failed, retrying...");
        digitalWrite(LED_PIN, !digitalRead(LED_PIN));
        delay(2000);
    }
    Serial.println("MPU6050 initialized");

    Serial.println("Calibrating gyro bias (keep sensor still)...");
    g_offsets = imu::calibrate(CALIBRATION_SAMPLES);
    Serial.printf("Calibration done, boot_id %s\n", g_boot_id);

    xTaskCreatePinnedToCore(sampleTask, "sample", 4096, nullptr, 3, nullptr, 1);
    xTaskCreatePinnedToCore(uploadTask, "upload", 8192, nullptr, 1, nullptr, 0);
}

void loop() {
    static unsigned long last_status = 0;
    unsigned long now = millis();
    processSerialCommands(now);
    if (g_transport.tick(now)) {
        g_wifi_mode = true;
        Serial.println("USB heartbeat timeout, returning to WIFI");
    }
    if (g_wifi_mode && now - last_status >= STATUS_EVERY_MS) {
        last_status = now;
        portENTER_CRITICAL(&g_buffer_lock);
        size_t waiting = g_buffer.size();
        uint32_t dropped = g_buffer.dropped();
        portEXIT_CRITICAL(&g_buffer_lock);
        Serial.printf("acquired %lu, buffered %u, dropped %lu, late reads %lu, last HTTP %d\n",
                      static_cast<unsigned long>(g_acquired.load()), static_cast<unsigned>(waiting),
                      static_cast<unsigned long>(dropped), static_cast<unsigned long>(g_late_reads.load()),
                      g_last_http.load());
    }
    delay(10);
}
