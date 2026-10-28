#pragma once

#include <string>

enum class TransportMode { Wifi, Usb };

class TransportController {
public:
    explicit TransportController(unsigned long timeout_ms);

    bool handleCommand(const std::string& command, unsigned long now_ms);
    bool tick(unsigned long now_ms);
    bool isWifi() const;

private:
    TransportMode mode_ = TransportMode::Wifi;
    unsigned long timeout_ms_;
    unsigned long last_ping_ms_ = 0;
};
