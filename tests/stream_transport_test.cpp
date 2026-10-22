#include <cassert>
#include <string>
#include "stream_io.h"
#include "ethernet_stream.h"
FakeSPI SPI;
FakeW5100 W5100;
ClientState clients[2];

static std::string drain(StreamIO::Output &out, unsigned chunk = 7) {
  std::string result;
  while (out.size()) {
    unsigned count = min(unsigned(out.size()), chunk);
    result.append(reinterpret_cast<const char*>(out.data()), count);
    out.advance(count);
  }
  return result;
}
int main() {
  StreamIO::Line line;
  // Fragmentation and CRLF keep exactly one V1 command.
  for (char c : std::string("@17 V1 M")) assert(line.push(c) == StreamIO::Line::MORE);
  assert(line.push('1') == StreamIO::Line::MORE);
  assert(line.push('\r') == StreamIO::Line::MORE);
  assert(line.push('\n') == StreamIO::Line::READY);
  char *command = line.data(); uint32_t id = 0;
  assert(StreamIO::request(command, id) && id == 17 && std::string(command) == "V1 M1");
  // Oversized lines cannot execute a valid-looking suffix.
  for (unsigned i = 0; i < 80; ++i) line.push('x');
  for (char c : std::string("V1 R1")) line.push(c);
  assert(line.push('\n') == StreamIO::Line::TOO_LONG);
  for (char c : std::string("V1 M0")) line.push(c);
  assert(line.push('\n') == StreamIO::Line::READY);
  command = line.data(); assert(StreamIO::request(command, id) && id == 0);
  for (std::string invalid : {"@0 V1 M1", "@2147483648 V1 M1", "@-1 V1 M1", "@x V1 M1", "@1 "}) {
    char text[80]; strcpy(text, invalid.c_str()); command = text;
    assert(!StreamIO::request(command, id));
  }
  StreamIO::Frame<4> small;
  small.print("abcdef\n"); assert(!small.complete());
  small.clear(); small.println("x"); assert(small.complete());

  StreamIO::Output output;
  output.status.println("old"); output.commitStatus();
  output.reply().println("ack"); assert(output.commitReply());
  assert(drain(output) == "ack\r\nold\r\n"); // Replies precede an unstarted status.
  output.status.println("frame"); output.commitStatus();
  assert(output.size()); output.advance(2); // A partially sent frame cannot be interleaved.
  output.reply().println("reply"); assert(output.commitReply());
  assert(drain(output) == "ame\r\nreply\r\n");
  for (unsigned i = 0; i < StreamIO::ACK_SLOTS; ++i) {
    assert(output.canReply()); output.reply().println("ack"); assert(output.commitReply());
  }
  assert(!output.canReply());
  assert(!drain(output).empty()); assert(output.canReply());
  output.status.print("incomplete"); output.commitStatus(); assert(!output.size());
  output.clear(); assert(output.statusDue && output.canStatus());

  EthernetStream stream; stream.client = EthernetClient(0);
  uint8_t bytes[300] = {};
  W5100.pointer = 2040;
  stream.send(bytes, sizeof(bytes));
  assert(stream.busy() && W5100.sends == 1);
  assert(W5100.writes.size() == 2 && W5100.writes[0].second.size() == 8 && W5100.writes[1].second.size() == 248);
  // Missing SEND_OK never blocks a loop or submits the same bytes again.
  for (unsigned i = 0; i < 1000; ++i) { assert(stream.completed() == 0); stream.send(bytes, 10); }
  assert(W5100.sends == 1);
  W5100.irq = SnIR::SEND_OK;
  assert(stream.completed() == 256 && !stream.busy());
  clients[0].space = 0; stream.send(bytes, 10); assert(!stream.busy());
  clients[0].space = 2048; stream.send(bytes, 10); assert(stream.busy());
  W5100.irq = SnIR::TIMEOUT;
  assert(stream.completed() == -1 && !stream.client && !stream.busy() && W5100.closes == 1);
  assert(SPI.begins == SPI.ends);
}
