#include <cassert>
#include <climits>
#include <cstdio>
#include <iostream>
#include "absolute_dro_avr.h"

unsigned long testNow = 0;
volatile uint8_t testPort = 0;
void (*testClock)() = nullptr;
AbsoluteDroReader reader;
void clockEdge() { reader.onClock(); }

std::string frameFor(long position) {
  char frame[14];
  std::snprintf(frame, sizeof(frame), "ffff%c%06ld20",
                position < 0 ? '8' : '0', position < 0 ? -position : position);
  return frame;
}

void sendFrame(const std::string &nibbles) {
  assert(nibbles.size() == 13);
  for (char c : nibbles) {
    unsigned digit = c <= '9' ? c - '0' : c - 'a' + 10;
    for (unsigned bit = 0; bit < 4; ++bit) {
      testPort = digit & (1 << bit) ? 1 << 3 : 0;
      testClock();
    }
  }
}

std::string status(const char *label) {
  Print out;
  reader.writeStatus(out, testNow);
  std::string json = "{" + out.text.substr(1) + "}";
  std::cout << label << '\t' << json << '\n';
  return json;
}

void expect(const std::string &json, const char *field) {
  assert(json.find(field) != std::string::npos);
}

void testProtocol() {
  // Signed range, all six decimal positions, zero, and consecutive frames.
  DroFrameCapture capture;
  for (long expected : {0L, 1L, 999999L, -1L, -999999L, 123456L}) {
    bool complete = false;
    auto frame = frameFor(expected);
    unsigned edges = 0;
    for (char c : frame) {
      unsigned digit = c <= '9' ? c - '0' : c - 'a' + 10;
      for (unsigned bit = 0; bit < 4; ++bit) {
        complete = capture.feed(digit & (1 << bit));
        assert(complete == (++edges == DRO_FRAME_BITS));
      }
    }
    long decoded = 0;
    assert(complete && decodeDroFrame(capture.frame, &decoded));
    assert(decoded == expected);
  }
  // Independently exercise each validation rule on a known-good frame.
  for (unsigned nibble : {0U, 4U, 5U, 11U, 12U}) {
    uint8_t bad[DRO_FRAME_BYTES];
    std::copy(capture.frame, capture.frame + DRO_FRAME_BYTES, bad);
    unsigned shift = (nibble & 1) * 4;
    bad[nibble / 2] &= ~(15 << shift);
    unsigned invalid = nibble == 5 ? 10 : nibble == 0 ? 0 : 1;
    bad[nibble / 2] |= invalid << shift;
    long decoded = 42;
    assert(!decodeDroFrame(bad, &decoded));
    assert(decoded == 42);
  }
}

int main() {
  testProtocol();
  reader.begin(2, 6, clockEdge);
  expect(status("boot"), "\"da\":-1");
  // Noise without a 16-one header must not create a frame.
  for (unsigned i = 0; i < 80; ++i) {
    testPort = i % 2 ? 1 << 3 : 0;
    testClock();
  }
  testPort = 0; testClock();
  reader.poll();
  expect(status("noise"), "\"dq\":0");

  testNow = 100;
  sendFrame(frameFor(12345)); reader.poll();
  auto first = status("first");
  expect(first, "\"df\":1"); expect(first, "\"dr\":12345");
  expect(first, "\"dd\":0"); expect(first, "\"dq\":1");
  testNow = 300;
  sendFrame(frameFor(12365)); reader.poll();
  expect(status("second"), "\"dd\":20");
  testNow = 550;
  expect(status("boundary"), "\"df\":1");
  testNow = 551;
  expect(status("stale"), "\"df\":0");

  // Malformed frames cannot refresh the last good sample; next valid frame recovers.
  auto invalid = frameFor(54321); invalid[12] = '1';
  sendFrame(invalid); reader.poll();
  auto rejected = status("rejected");
  expect(rejected, "\"dr\":12365"); expect(rejected, "\"dx\":256");
  testNow = 600;
  sendFrame(frameFor(-100)); reader.poll();
  expect(status("recovered"), "\"dr\":-100");

  // Delaying poll must not turn an old captured sample into a fresh one.
  testNow = 700; sendFrame(frameFor(777));
  testNow = 1000; reader.poll();
  auto delayed = status("delayed");
  expect(delayed, "\"df\":0"); expect(delayed, "\"da\":300");

  // While loop is busy, newest complete frame wins and overwritten frames are counted.
  testNow = 1100; sendFrame(frameFor(100));
  testNow = 1110; sendFrame(frameFor(200)); reader.poll();
  auto latest = status("latest");
  expect(latest, "\"dr\":200"); expect(latest, "\"dx\":257");
  for (unsigned i = 0; i < 300; ++i) sendFrame(frameFor(300));
  reader.poll();
  expect(status("saturated_drops"), "\"dx\":511");
  for (unsigned i = 0; i < 300; ++i) { sendFrame(invalid); reader.poll(); }
  expect(status("saturated_errors"), "\"dx\":65535");

  testNow = ULONG_MAX - 10; sendFrame(frameFor(9)); reader.poll();
  testNow = 20;
  auto rollover = status("rollover");
  expect(rollover, "\"df\":1"); expect(rollover, "\"da\":31");
}
