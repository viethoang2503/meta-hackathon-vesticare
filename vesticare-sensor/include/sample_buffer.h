#pragma once
#include <cstddef>
#include <cstdint>
#include "imu_math.h"

struct StampedSample {
    uint32_t seq;        // counts every acquired sample since boot, kept or not
    uint32_t device_ms;  // millis() when it was read
    ImuSample imu;
};

// Fixed-size FIFO between the sampling task and the upload task. Not thread-safe:
// the caller holds a lock. When full, the oldest sample is dropped and counted,
// because a fall now matters more than a backlog from before a network stall.
template <size_t N>
class SampleBuffer {
public:
    void push(const StampedSample& s) {
        if (size_ == N) {
            head_ = (head_ + 1) % N;
            size_--;
            dropped_++;
        }
        items_[(head_ + size_) % N] = s;
        size_++;
    }

    // Copies up to `max` oldest samples without removing them.
    size_t peek(StampedSample* out, size_t max) const {
        size_t n = size_ < max ? size_ : max;
        for (size_t i = 0; i < n; i++) {
            out[i] = items_[(head_ + i) % N];
        }
        return n;
    }

    // Removes samples up to and including `seq`; already-dropped ones are simply gone.
    void popThrough(uint32_t seq) {
        while (size_ > 0 && items_[head_].seq <= seq) {
            head_ = (head_ + 1) % N;
            size_--;
        }
    }

    void clear() { head_ = 0; size_ = 0; }
    size_t size() const { return size_; }
    uint32_t dropped() const { return dropped_; }

private:
    StampedSample items_[N];
    size_t head_ = 0;
    size_t size_ = 0;
    uint32_t dropped_ = 0;
};
