#pragma once
#include <Arduino.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

// Shared USB/TCP framing and bounded output. No sockets, waits, or actuator logic.
namespace StreamIO {
constexpr uint16_t STATUS_BYTES = 1792;
constexpr uint8_t ACK_BYTES = 96, ACK_SLOTS = 2;

class Line {
 public:
  enum Result { MORE, READY, TOO_LONG };
  Result push(char c) {
    if (c == '\r') return MORE;
    if (c == '\n') {
      data_[length_] = 0;
      const bool bad = overflow_;
      length_ = 0; overflow_ = false;
      return bad ? TOO_LONG : data_[0] ? READY : MORE;
    }
    if (size_t(length_) + 1 < sizeof(data_) && !overflow_) data_[length_++] = c;
    else overflow_ = true; // Discard the entire line, never execute a suffix.
    return MORE;
  }
  char *data() { return data_; }
  void clear() { length_ = 0; overflow_ = false; }
 private:
  char data_[64] = {};
  uint8_t length_ = 0;
  bool overflow_ = false;
};

// Optional request ID is transport metadata; V1 commands remain unchanged.
inline bool request(char *&line, uint32_t &id) {
  id = 0;
  if (*line != '@') return true;
  char *space = strchr(line, ' ');
  if (!space || space == line + 1) return false;
  uint32_t value = 0;
  for (char *p = line + 1; p != space; ++p) {
    if (*p < '0' || *p > '9') return false;
    const uint8_t digit = *p - '0';
    if (value > (0x7fffffffUL - digit) / 10) return false;
    value = value * 10 + digit;
  }
  if (!value || !space[1]) return false;
  id = value; line = space + 1;
  return true;
}

template <uint16_t Capacity>
class Frame : public Print {
 public:
  using Print::write;
  size_t write(uint8_t value) override {
    if (length_ == Capacity) { overflow_ = true; return 0; }
    data_[length_++] = value;
    if (service_ && !(length_ % 64)) service_();
    return 1;
  }
  void service(void (*callback)()) { service_ = callback; }
  void clear() { length_ = 0; overflow_ = false; }
  bool complete() const { return length_ && !overflow_ && data_[length_ - 1] == '\n'; }
  const uint8_t *data() const { return data_; }
  uint16_t size() const { return length_; }
 private:
  void (*service_)() = nullptr;
  uint8_t data_[Capacity];
  uint16_t length_ = 0;
  bool overflow_ = false;
};

class Output {
 public:
  Frame<STATUS_BYTES> status;
  bool statusDue = true;
  bool canReply() const { return replies_ < ACK_SLOTS; }
  Frame<ACK_BYTES> &reply() { return acks_[(head_ + replies_) % ACK_SLOTS]; }
  bool commitReply() {
    if (!canReply() || !reply().complete()) return false;
    ++replies_; return true;
  }
  bool canStatus() const { return !active_ && !status.size() && !replies_; }
  void commitStatus() { if (!status.complete()) status.clear(); statusDue = false; }
  const uint8_t *data() {
    if (!active_) {
      if (replies_) active_ = 1;
      else if (status.complete()) active_ = 2;
    }
    return active_ == 1 ? acks_[head_].data() + offset_ : status.data() + offset_;
  }
  uint16_t size() {
    data();
    return active_ == 1 ? acks_[head_].size() - offset_ :
           active_ == 2 ? status.size() - offset_ : 0;
  }
  void advance(uint16_t count) {
    offset_ += count;
    if (size()) return;
    if (active_ == 1) { acks_[head_].clear(); head_ = (head_ + 1) % ACK_SLOTS; --replies_; }
    else status.clear();
    offset_ = 0; active_ = 0;
  }
  void clear() {
    status.clear();
    for (uint8_t i = 0; i < ACK_SLOTS; ++i) acks_[i].clear();
    head_ = replies_ = active_ = 0; offset_ = 0; statusDue = true;
  }
 private:
  Frame<ACK_BYTES> acks_[ACK_SLOTS];
  uint8_t head_ = 0, replies_ = 0, active_ = 0;
  uint16_t offset_ = 0;
};
} // namespace StreamIO
