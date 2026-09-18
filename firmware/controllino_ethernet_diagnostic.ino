// Motion-disabled Ethernet identification firmware for CONTROLLINO MAXI Automation.
//
// This sketch deliberately does not configure or write any motion, relay, or
// industrial I/O pin. The official MAXI Automation board definition supplies
// the built-in W5100 chip-select mapping (PIN_SPI_SS_ETHERNET_LIB = 70).

#include <SPI.h>
#include <Ethernet.h>

namespace {

byte macAddress[] = {0x02, 0x43, 0x4F, 0x4E, 0x54, 0x01};
const IPAddress fallbackIp(10, 77, 0, 10);
const IPAddress fallbackDns(10, 77, 0, 2);
const IPAddress fallbackGateway(10, 77, 0, 2);
const IPAddress fallbackSubnet(255, 255, 255, 0);

EthernetServer server(80);
bool usedDhcp = false;
unsigned long lastStatusMs = 0;

const __FlashStringHelper *hardwareName() {
  switch (Ethernet.hardwareStatus()) {
    case EthernetW5100:
      return F("W5100");
    case EthernetW5200:
      return F("W5200");
    case EthernetW5500:
      return F("W5500");
    default:
      return F("not_detected");
  }
}

const __FlashStringHelper *linkName() {
  switch (Ethernet.linkStatus()) {
    case LinkON:
      return F("on");
    case LinkOFF:
      return F("off");
    default:
      return F("unknown");
  }
}

void printIp(Print &output, const IPAddress &address) {
  for (uint8_t index = 0; index < 4; ++index) {
    if (index != 0) {
      output.print('.');
    }
    output.print(address[index]);
  }
}

void printStatus(Print &output) {
  output.print(F("CONTROLLINO_ETHERNET_DIAGNOSTIC ip="));
  printIp(output, Ethernet.localIP());
  output.print(F(" address_source="));
  output.print(usedDhcp ? F("dhcp") : F("static_fallback"));
  output.print(F(" hardware="));
  output.print(hardwareName());
  output.print(F(" link="));
  output.println(linkName());
}

void serveStatus() {
  EthernetClient client = server.available();
  if (!client) {
    return;
  }

  const unsigned long deadline = millis() + 1000UL;
  bool blankLine = true;
  while (client.connected() && static_cast<long>(deadline - millis()) > 0) {
    if (!client.available()) {
      continue;
    }
    const char character = client.read();
    if (character == '\n' && blankLine) {
      break;
    }
    if (character == '\n') {
      blankLine = true;
    } else if (character != '\r') {
      blankLine = false;
    }
  }

  client.println(F("HTTP/1.1 200 OK"));
  client.println(F("Content-Type: application/json"));
  client.println(F("Connection: close"));
  client.println();
  client.print(F("{\"device\":\"CONTROLLINO MAXI Automation\",\"diagnostic\":true,\"motion_outputs_configured\":false,\"ip\":\""));
  printIp(client, Ethernet.localIP());
  client.print(F("\",\"address_source\":\""));
  client.print(usedDhcp ? F("dhcp") : F("static_fallback"));
  client.print(F("\",\"hardware\":\""));
  client.print(hardwareName());
  client.print(F("\",\"link\":\""));
  client.print(linkName());
  client.println(F("\"}"));
  delay(1);
  client.stop();
}

}  // namespace

void setup() {
  Serial.begin(9600);
  delay(250);
  Serial.println(F("CONTROLLINO MAXI Automation Ethernet diagnostic"));
  Serial.println(F("Motion/relay/industrial outputs are not configured."));

  // Keep DHCP bounded for a directly connected laptop with no DHCP server.
  usedDhcp = Ethernet.begin(macAddress, 5000UL, 1000UL) != 0;
  if (!usedDhcp) {
    Ethernet.begin(
        macAddress,
        fallbackIp,
        fallbackDns,
        fallbackGateway,
        fallbackSubnet);
  }

  server.begin();
  printStatus(Serial);
}

void loop() {
  if (usedDhcp) {
    Ethernet.maintain();
  }
  serveStatus();

  const unsigned long now = millis();
  if (now - lastStatusMs >= 5000UL) {
    lastStatusMs = now;
    printStatus(Serial);
  }
}
