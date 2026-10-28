#pragma once
#include <string>

namespace network {

void connectWiFi(const char* ssid, const char* password, unsigned long timeout_ms);
bool ensureConnected(const char* ssid, const char* password, unsigned long timeout_ms);
int sendBatch(const char* backend_url, const char* device_token, const std::string& json_payload);

}
