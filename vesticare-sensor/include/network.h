#pragma once
#include <string>

namespace network {

void connectWiFi(const char* ssid, const char* password, unsigned long timeout_ms);
bool ensureConnected(const char* ssid, const char* password, unsigned long timeout_ms);
int sendBatch(const char* backend_url, const char* device_token, const std::string& json_payload);
// POSTs `json_payload` to backend_url + path; fills `response` on any HTTP reply. Returns the status.
int post(const char* backend_url, const char* path, const char* device_token,
         const std::string& json_payload, std::string* response);

}
