#pragma once
#include <stdint.h>

// AbsoluteDRO Plus: REQ grounded, 52 bits, each nibble least-significant bit first.
// This protocol code has no board, GPIO, timer, or dashboard dependencies.
constexpr uint8_t DRO_FRAME_BITS = 52, DRO_HEADER_BITS = 16, DRO_FRAME_BYTES = 7;
constexpr unsigned long DRO_STALE_MS = 250UL;
constexpr unsigned long DRO_STATUS_MS = 200UL;

class DroFrameCapture {
 public:
  uint8_t frame[DRO_FRAME_BYTES] = {};

  // Called once per falling clock edge. True means frame[] is complete.
  bool feed(bool dataHigh) {
    if (bit == 0) {
      headerOnes = dataHigh ? headerOnes + 1 : 0;
      if (headerOnes == DRO_HEADER_BITS) {
        frame[0] = frame[1] = 0xff;
        for (uint8_t i = 2; i < DRO_FRAME_BYTES; ++i) frame[i] = 0;
        bit = DRO_HEADER_BITS;
      }
      return false;
    }
    if (dataHigh) frame[bit >> 3] |= uint8_t(1U << (bit & 7));
    if (++bit < DRO_FRAME_BITS) return false;
    bit = headerOnes = 0;
    return true;
  }

 private:
  uint8_t bit = 0, headerOnes = 0;
};

inline uint8_t droNibble(const uint8_t frame[DRO_FRAME_BYTES], uint8_t digitIndex) {
  uint8_t packed = frame[digitIndex >> 1];
  if (digitIndex & 0x01) packed >>= 4;
  return packed & 0x0F;
}

inline bool decodeDroFrame(
    const uint8_t frame[DRO_FRAME_BYTES],
    long *positionHundredthsMm) {
  // AbsoluteDRO Plus follows the 13-nibble Digimatic ordering, with every
  // nibble transmitted least-significant bit first:
  //   d1..d4=F, d5=sign, d6..d11=xxxx.xx, d12=2 decimals, d13=millimetres.
  for (uint8_t digit = 0; digit < 4; ++digit) {
    if (droNibble(frame, digit) != 0x0F) return false;
  }
  uint8_t sign = droNibble(frame, 4);
  if (sign != 0 && sign != 8) return false;

  long magnitudeHundredthsMm = 0L;
  for (uint8_t digit = 5; digit <= 10; ++digit) {
    uint8_t value = droNibble(frame, digit);
    if (value > 9) return false;
    magnitudeHundredthsMm = magnitudeHundredthsMm * 10L + value;
  }
  if (droNibble(frame, 11) != 2) return false;
  if (droNibble(frame, 12) != 0) return false;

  *positionHundredthsMm =
      sign == 8 ? -magnitudeHundredthsMm : magnitudeHundredthsMm;
  return true;
}

