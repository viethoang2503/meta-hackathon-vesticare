#include <unity.h>
#include "sample_buffer.h"

void setUp(void) {}
void tearDown(void) {}

namespace {
StampedSample at(uint32_t seq) { return StampedSample{seq, seq * 20, {0, 0, 1, 0, 0, 0, 1}}; }
}

void test_peek_returns_oldest_first_without_removing(void) {
    SampleBuffer<8> b;
    for (uint32_t i = 0; i < 5; i++) b.push(at(i));
    StampedSample out[3];
    TEST_ASSERT_EQUAL_UINT(3, b.peek(out, 3));
    TEST_ASSERT_EQUAL_UINT32(0, out[0].seq);
    TEST_ASSERT_EQUAL_UINT32(2, out[2].seq);
    TEST_ASSERT_EQUAL_UINT(5, b.size());
}

void test_popThrough_removes_what_was_sent(void) {
    SampleBuffer<8> b;
    for (uint32_t i = 0; i < 5; i++) b.push(at(i));
    b.popThrough(2);
    StampedSample out[8];
    TEST_ASSERT_EQUAL_UINT(2, b.peek(out, 8));
    TEST_ASSERT_EQUAL_UINT32(3, out[0].seq);
}

void test_full_buffer_drops_the_oldest_and_counts_it(void) {
    SampleBuffer<4> b;
    for (uint32_t i = 0; i < 7; i++) b.push(at(i));
    StampedSample out[4];
    TEST_ASSERT_EQUAL_UINT(4, b.peek(out, 4));
    TEST_ASSERT_EQUAL_UINT32(3, out[0].seq);
    TEST_ASSERT_EQUAL_UINT32(6, out[3].seq);
    TEST_ASSERT_EQUAL_UINT32(3, b.dropped());
}

void test_samples_dropped_while_a_batch_was_in_flight_are_not_resent(void) {
    SampleBuffer<4> b;
    for (uint32_t i = 0; i < 4; i++) b.push(at(i));
    StampedSample inflight[4];
    b.peek(inflight, 4);              // upload task copies 0..3
    for (uint32_t i = 4; i < 7; i++) b.push(at(i));  // overflow drops 0..2 meanwhile
    b.popThrough(inflight[3].seq);    // the send succeeded
    StampedSample out[4];
    TEST_ASSERT_EQUAL_UINT(3, b.peek(out, 4));
    TEST_ASSERT_EQUAL_UINT32(4, out[0].seq);
}

int main(int argc, char **argv) {
    UNITY_BEGIN();
    RUN_TEST(test_peek_returns_oldest_first_without_removing);
    RUN_TEST(test_popThrough_removes_what_was_sent);
    RUN_TEST(test_full_buffer_drops_the_oldest_and_counts_it);
    RUN_TEST(test_samples_dropped_while_a_batch_was_in_flight_are_not_resent);
    return UNITY_END();
}
