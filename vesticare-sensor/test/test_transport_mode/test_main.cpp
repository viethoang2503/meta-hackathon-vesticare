#include <unity.h>
#include "transport_mode.h"

void setUp(void) {}
void tearDown(void) {}

void test_defaults_to_wifi_and_switches_modes(void) {
    TransportController controller(6000);
    TEST_ASSERT_TRUE(controller.isWifi());
    TEST_ASSERT_TRUE(controller.handleCommand("MODE USB", 100));
    TEST_ASSERT_FALSE(controller.isWifi());
    TEST_ASSERT_FALSE(controller.handleCommand("MODE USB", 200));
    TEST_ASSERT_TRUE(controller.handleCommand("MODE WIFI", 300));
    TEST_ASSERT_TRUE(controller.isWifi());
}

void test_unknown_command_does_not_change_mode(void) {
    TransportController controller(6000);
    TEST_ASSERT_FALSE(controller.handleCommand("HELLO", 100));
    TEST_ASSERT_TRUE(controller.isWifi());
}

void test_usb_mode_times_out_to_wifi(void) {
    TransportController controller(6000);
    controller.handleCommand("MODE USB", 100);
    TEST_ASSERT_FALSE(controller.tick(6099));
    TEST_ASSERT_TRUE(controller.tick(6100));
    TEST_ASSERT_TRUE(controller.isWifi());
}

void test_ping_refreshes_usb_watchdog(void) {
    TransportController controller(6000);
    controller.handleCommand("MODE USB", 100);
    TEST_ASSERT_FALSE(controller.handleCommand("PING", 5000));
    TEST_ASSERT_FALSE(controller.tick(10999));
    TEST_ASSERT_TRUE(controller.tick(11000));
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_defaults_to_wifi_and_switches_modes);
    RUN_TEST(test_unknown_command_does_not_change_mode);
    RUN_TEST(test_usb_mode_times_out_to_wifi);
    RUN_TEST(test_ping_refreshes_usb_watchdog);
    return UNITY_END();
}
