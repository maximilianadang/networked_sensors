#pragma once
#include <Arduino.h>
#include <util/atomic.h>

// X1 DO1 / Arduino D3 is OC3C. Timer3 owns both STEP edges; interrupts
// account for pulses and disconnect the output at the final period boundary.
class StepperPwm {
 public:
  void begin() {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      pinMode(3, INPUT); // Seed the compare latch before enabling the pin driver.
      idle();
      pinMode(3, OUTPUT);
    }
  }

  void start(int8_t direction, bool finite, long target, long sps) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      idle();
      direction_ = direction; finite_ = finite; target_ = target;
      reached_ = finishing_ = false;
      activeSps_ = wantedSps_ = sps; bufferedSps_ = 0;
      const uint16_t top = topFor(sps);
      OCR3A = top; OCR3C = (top + 1U) / 2U;
      TCNT3 = 0; TIFR3 = _BV(TOV3) | _BV(OCF3C);
      running_ = true;
      // Fast PWM mode 15: OCR3A is double-buffered TOP, OC3C is
      // HIGH at BOTTOM and LOW at compare. Approximately 50% duty.
      TCCR3A = _BV(COM3C1) | _BV(WGM31) | _BV(WGM30);
      TIMSK3 = _BV(TOIE3) | _BV(OCIE3C);
      TCCR3B = _BV(WGM33) | _BV(WGM32) | _BV(CS31) | _BV(CS30);
    }
  }

  void stop() {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      idle(); reached_ = false;
    }
  }

  void setSpeed(long sps) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { wantedSps_ = sps; }
  }
  void zero() {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { position_ = 0; }
  }
  bool moving() const {
    bool value; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { value = running_; } return value;
  }
  bool reached() const {
    bool value; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { value = reached_; } return value;
  }
  long position() const {
    long value; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { value = position_; } return value;
  }
  long signedSps() const {
    long value; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      value = activeSps_ * direction_;
    } return value;
  }
  int8_t direction() const { return direction_; }

  void onStep() {
    if (!running_ || finishing_) return;
    if (bufferedSps_) { activeSps_ = bufferedSps_; bufferedSps_ = 0; }
    position_ += direction_;
    finishing_ = finite_ && (direction_ > 0 ? position_ >= target_ : position_ <= target_);
    if (!finishing_ && wantedSps_ != activeSps_) {
      // Write both double-buffered registers midway through the cycle.
      // They latch together at the next BOTTOM without restarting the timer.
      const uint16_t top = topFor(wantedSps_);
      OCR3A = top; OCR3C = (top + 1U) / 2U; bufferedSps_ = wantedSps_;
    }
  }

  void onPeriodEnd() {
    if (!running_) return;
    if (finishing_) {
      // Overflow is raised at TOP, near the end of the LOW half-cycle.
      // Return HIGH and disconnect before another falling edge, retaining
      // the completed count and completion indication.
      idle(); reached_ = true; return;
    }
  }

 private:
  static uint16_t topFor(long sps) {
    const unsigned long rate = F_CPU / 64UL;
    return uint16_t((rate + sps / 2) / sps - 1);
  }
  void idle() {
    TCCR3B = 0; TIMSK3 = 0;
    // Seed the OC3C latch HIGH in non-PWM mode before disconnecting it.
    // Reconnecting PWM later must not introduce a spurious initial LOW.
    TCCR3A = _BV(COM3C1) | _BV(COM3C0); TCCR3C = _BV(FOC3C);
    digitalWrite(3, HIGH); TCCR3A = 0;
    running_ = finishing_ = false; activeSps_ = bufferedSps_ = 0;
  }
  volatile bool running_ = false, finite_ = false, reached_ = false, finishing_ = false;
  volatile int8_t direction_ = 1;
  volatile long position_ = 0, target_ = 0, activeSps_ = 0, wantedSps_ = 0, bufferedSps_ = 0;
};
