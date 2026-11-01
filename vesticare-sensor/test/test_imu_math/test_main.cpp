#include <unity.h>
#include "imu_math.h"

void setUp(void) {}
void tearDown(void) {}

void test_computeSVM_at_rest_gravity_only(void) {
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 1.0f, computeSVM(0.0f, 0.0f, 1.0f));
}

void test_computeSVM_pythagorean(void) {
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 5.0f, computeSVM(3.0f, 4.0f, 0.0f));
}

void test_computeOffsets_takes_only_the_gyro_bias(void) {
    ImuSample rest[3] = {
        {0.02f, -0.01f, 1.02f, 0.5f, -0.3f, 0.1f, 0.0f},
        {0.00f,  0.01f, 0.98f, 0.4f, -0.4f, 0.2f, 0.0f},
        {0.04f, -0.03f, 1.00f, 0.6f, -0.2f, 0.0f, 0.0f},
    };
    ImuOffsets offsets = computeOffsets(rest, 3);

    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.5f, offsets.gx);
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.3f, offsets.gy);
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.1f, offsets.gz);
}

void test_applyCalibration_keeps_raw_acceleration_in_any_orientation(void) {
    ImuOffsets offsets = {0.5f, -0.3f, 0.1f};
    // Worn sideways: gravity on x. Subtracting a resting accel mean would have moved it.
    ImuSample raw = {0.98f, 0.05f, -0.10f, 0.55f, -0.28f, 0.12f, 0.0f};

    ImuSample corrected = applyCalibration(raw, offsets);

    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.98f, corrected.ax);
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.05f, corrected.ay);
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.10f, corrected.az);
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.05f, corrected.gx);
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.02f, corrected.gy);
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.02f, corrected.gz);
    TEST_ASSERT_FLOAT_WITHIN(0.0001f, computeSVM(0.98f, 0.05f, -0.10f), corrected.svm);
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_computeSVM_at_rest_gravity_only);
    RUN_TEST(test_computeSVM_pythagorean);
    RUN_TEST(test_computeOffsets_takes_only_the_gyro_bias);
    RUN_TEST(test_applyCalibration_keeps_raw_acceleration_in_any_orientation);
    return UNITY_END();
}
