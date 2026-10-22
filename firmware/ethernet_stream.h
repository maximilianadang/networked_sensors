#pragma once
#include <Ethernet.h>
#include <utility/w5100.h>

// EthernetClient::write waits for SEND_OK. This adapter starts one bounded
// hardware send and checks its completion on later loop iterations instead.
// Keep all WIZnet register access here; the shared stream buffers know no SPI.
class EthernetStream {
 public:
  EthernetClient client;
  bool connected() { return client && client.connected(); }
  bool busy() const { return pending_ != 0; }
  int completed() {
    if (!pending_) return 0;
    const uint8_t socket = client.getSocketNumber();
    SPI.beginTransaction(SPI_ETHERNET_SETTINGS);
    const uint8_t flags = W5100.readSnIR(socket);
    if (flags & SnIR::TIMEOUT) {
      SPI.endTransaction(); close(); return -1;
    }
    if (!(flags & SnIR::SEND_OK)) { SPI.endTransaction(); return 0; }
    W5100.writeSnIR(socket, SnIR::SEND_OK);
    SPI.endTransaction();
    const uint16_t sent = pending_; pending_ = 0;
    return sent;
  }
  void send(const uint8_t *data, uint16_t count) {
    if (busy() || !count || !connected()) return;
    const int space = client.availableForWrite();
    if (space <= 0) return;
    count = min(count, uint16_t(min(space, 256)));
    const uint8_t socket = client.getSocketNumber();
    SPI.beginTransaction(SPI_ETHERNET_SETTINGS);
    const uint16_t pointer = W5100.readSnTX_WR(socket);
    const uint16_t offset = pointer & W5100.SMASK;
    const uint16_t first = min(count, uint16_t(W5100.SSIZE - offset));
    W5100.write(W5100.SBASE(socket) + offset, data, first);
    if (first < count) W5100.write(W5100.SBASE(socket), data + first, count - first);
    W5100.writeSnTX_WR(socket, pointer + count);
    W5100.writeSnIR(socket, SnIR::SEND_OK | SnIR::TIMEOUT);
    W5100.execCmdSn(socket, Sock_SEND);
    SPI.endTransaction();
    pending_ = count;
  }
  static void close(EthernetClient &connection) {
    if (connection) {
      // Hardware CLOSE is immediate; never wait for a peer's FIN/ACK.
      SPI.beginTransaction(SPI_ETHERNET_SETTINGS);
      W5100.execCmdSn(connection.getSocketNumber(), Sock_CLOSE);
      SPI.endTransaction();
      connection = EthernetClient();
    }
  }
  void close() { close(client); pending_ = 0; }
 private:
  uint16_t pending_ = 0;
};
