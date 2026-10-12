#pragma once
#include <Arduino.h>

// Raw acquisition only. Channel gains and sensor pressure calibration belong
// to the host. One channel per due slice bounds ADC work to about 210 us at16MHz.
class PressureAdc {
 public:
  static constexpr byte CHANNELS = 7;
  void begin(const byte (&pins)[CHANNELS]) {
    channels = pins;
    for (byte i = 0; i < CHANNELS; ++i) pinMode(channels[i], INPUT);
  }
  void poll(uint32_t nowMs) {
    if (uint32_t(nowMs - lastSliceMs) < 10) return;
    lastSliceMs = nowMs;
    if (!index) scanStartedMs = nowMs;
    analogRead(channels[index]); // Discard first conversion after switching MUX.
    pending[index] = analogRead(channels[index]);
    if (++index != CHANNELS) return;
    for (byte i = 0; i < CHANNELS; ++i) values[i] = pending[i];
    oldestSampleMs = scanStartedMs; index = 0;
    if (++sequence == 0) ++sequence;
  }
  void writeStatus(Print &out, uint32_t nowMs) const {
    // Publish complete scans only; age conservatively measures their oldest
    // channel acquisition. Snapshot before ServicedPrint can advance the scan.
    uint16_t snapshot[CHANNELS];
    for (byte i = 0; i < CHANNELS; ++i) snapshot[i] = values[i];
    const uint32_t seq = sequence, age = nowMs - oldestSampleMs;
    out.print(F(",\"adc\":["));
    for (byte i = 0; i < CHANNELS; ++i) {
      if (i) out.print(',');
      out.print(snapshot[i]);
    }
    out.print(F("],\"adcage\":")); out.print(seq ? long(min(age, 0x7fffffffUL)) : -1L);
    out.print(F(",\"adcseq\":")); out.print(seq);
  }

 private:
  const byte *channels = nullptr;
  byte index = 0;
  uint16_t pending[CHANNELS] = {}, values[CHANNELS] = {};
  uint32_t lastSliceMs = 0, scanStartedMs = 0, oldestSampleMs = 0, sequence = 0;
};
