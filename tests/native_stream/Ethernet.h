#pragma once
#include "Arduino.h"
struct ClientState {
  bool connected = true;
  int space = 2048;
};
extern ClientState clients[2];
class EthernetClient {
 public:
  EthernetClient(uint8_t socket = 255): socket_(socket) {}
  operator bool() const { return socket_ < 2; }
  bool connected() const { return socket_ < 2 && clients[socket_].connected; }
  int availableForWrite() const { return clients[socket_].space; }
  uint8_t getSocketNumber() const { return socket_; }
 private:
  uint8_t socket_;
};
