#include "transport_mode.h"

TransportController::TransportController(unsigned long timeout_ms)
    : timeout_ms_(timeout_ms) {}

bool TransportController::handleCommand(
    const std::string& command,
    unsigned long now_ms
) {
    if (command == "MODE USB") {
        const bool changed = mode_ != TransportMode::Usb;
        mode_ = TransportMode::Usb;
        last_ping_ms_ = now_ms;
        return changed;
    }

    if (command == "MODE WIFI") {
        const bool changed = mode_ != TransportMode::Wifi;
        mode_ = TransportMode::Wifi;
        return changed;
    }

    if (command == "PING" && mode_ == TransportMode::Usb) {
        last_ping_ms_ = now_ms;
    }
    return false;
}

bool TransportController::tick(unsigned long now_ms) {
    if (mode_ != TransportMode::Usb) {
        return false;
    }
    if (now_ms - last_ping_ms_ < timeout_ms_) {
        return false;
    }
    mode_ = TransportMode::Wifi;
    return true;
}

bool TransportController::isWifi() const {
    return mode_ == TransportMode::Wifi;
}
