#include "network.h"
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <cstring>

namespace network {

void connectWiFi(const char* ssid, const char* password, unsigned long timeout_ms) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < timeout_ms) {
        delay(250);
        Serial.print(".");
    }

    Serial.println();
    if (WiFi.status() == WL_CONNECTED) {
        Serial.print("WiFi connected, IP: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("WiFi connect timed out");
    }
}

bool ensureConnected(const char* ssid, const char* password, unsigned long timeout_ms) {
    if (WiFi.status() == WL_CONNECTED) {
        return true;
    }
    Serial.println("WiFi disconnected, reconnecting...");
    connectWiFi(ssid, password, timeout_ms);
    return WiFi.status() == WL_CONNECTED;
}

int sendBatch(const char* backend_url, const char* device_token, const std::string& json_payload) {
    HTTPClient http;
    std::string url = std::string(backend_url) + "/api/sensor/batch";
    http.begin(url.c_str());
    http.addHeader("Content-Type", "application/json");
    if (device_token != nullptr && strlen(device_token) > 0) {
        std::string auth = std::string("Bearer ") + device_token;
        http.addHeader("Authorization", auth.c_str());
    }

    int status = http.POST(json_payload.c_str());
    http.end();
    return status;
}

}
