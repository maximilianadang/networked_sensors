#pragma once
#include <Arduino.h>
#include "qualified_limit.h"

// Industrial inputs: HIGH while the NC contact is closed, LOW when tripped
// or disconnected. No internal pull-ups; the terminal circuitry sets the level.
class DirectionalLimits {
 public:
  QualifiedLimitInput positive = {}, negative = {};
  uint8_t positiveRaw = HIGH, negativeRaw = HIGH;

  void begin(uint8_t positivePin, uint8_t negativePin) {
    positivePin_ = positivePin; negativePin_ = negativePin;
    pinMode(positivePin_, INPUT); pinMode(negativePin_, INPUT);
    positiveRaw = digitalRead(positivePin_); negativeRaw = digitalRead(negativePin_);
    const uint32_t now = micros();
    positive.initialize(positiveRaw == LOW, now);
    negative.initialize(negativeRaw == LOW, now);
  }

  bool poll(uint32_t nowUs) {
    const uint32_t before = diagnostics();
    const uint8_t previousPositive = positiveRaw, previousNegative = negativeRaw;
    positiveRaw = digitalRead(positivePin_); negativeRaw = digitalRead(negativePin_);
    positive.update(positiveRaw == LOW, nowUs);
    negative.update(negativeRaw == LOW, nowUs);
    return before != diagnostics() || positiveRaw != previousPositive || negativeRaw != previousNegative;
  }

  bool blocks(int8_t direction) const {
    return (direction > 0 && positive.qualifiedActive)
        || (direction < 0 && negative.qualifiedActive);
  }

  uint32_t diagnostics() const {
    return (uint32_t(positive.qualifiedActive) << 17)
        | (uint32_t(negative.qualifiedActive) << 16)
        | (uint32_t(positive.rejectedGlitches) << 8) | negative.rejectedGlitches;
  }

  void writeStatus(Print &out) const {
    // Serviced printing may poll the live inputs between individual fields.
    const DirectionalLimits snapshot = *this;
    out.print(F(",\"d6\":")); out.print(snapshot.positiveRaw);
    out.print(F(",\"d8\":")); out.print(snapshot.negativeRaw);
    out.print(F(",\"lx\":")); out.print(snapshot.diagnostics());
    out.print(F(",\"lp\":0,\"ln\":0")); // Legacy fields; these inputs never retain trips.
    out.print(F(",\"lppin\":")); out.print(snapshot.positivePin_);
    out.print(F(",\"lnpin\":")); out.print(snapshot.negativePin_);
  }

 private:
  uint8_t positivePin_ = 0, negativePin_ = 0;
};
