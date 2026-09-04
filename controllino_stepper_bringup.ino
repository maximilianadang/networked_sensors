// Operator-gated SRX02-S motion bring-up for CONTROLLINO MAXI Automation.
//
// X1 Digital Out 1 / Arduino D3 -> SRX02-S STEP-
// X1 Digital Out 3 / Arduino D5 -> SRX02-S DIR-
// X1 Digital Out 5 / Arduino D7 -> SRX02-S EN-
// X1 5 V -> STEP+, DIR+, EN+ (common anode)
//
// The SRX02-S optocouplers are active when a '-' terminal is LOW. EN active
// disables the amplifier. STEP idles HIGH and a pulse is HIGH->LOW->HIGH so
// the documented falling edge occurs without holding the STEP optocoupler on.

#include <SPI.h>
#include <Ethernet.h>

namespace {

const uint8_t PIN_STEP_NEG = 3;
const uint8_t PIN_DIRECTION_NEG = 5;
const uint8_t PIN_ENABLE_NEG = 7;

const uint8_t DRIVER_DISABLED_LEVEL = LOW;
const uint8_t DRIVER_ENABLED_LEVEL = HIGH;
const uint8_t STEP_IDLE_LEVEL = HIGH;
const uint8_t STEP_ACTIVE_LEVEL = LOW;

const unsigned long DRIVER_WAKE_MS = 250UL;
const unsigned long STEP_INTERVAL_US = 1000UL;  // 1,000 pulses/s.
const unsigned int STEP_RATE_PPS = 1000U;
const unsigned int STEP_ACTIVE_US = 10U;
const unsigned int TEST_PULSES = 50U;
const unsigned long JOG_WATCHDOG_MS = 500UL;

byte macAddress[] = {0x02, 0x43, 0x4F, 0x4E, 0x54, 0x01};
const IPAddress controllerIp(10, 77, 0, 10);
const IPAddress dnsServer(10, 77, 0, 2);
const IPAddress gateway(10, 77, 0, 2);
const IPAddress subnet(255, 255, 255, 0);
EthernetServer server(80);

bool moving = false;
bool stepActive = false;
bool jogMode = false;
bool jogTimerRunning = false;
char directionName = '-';
unsigned long pulsesCompleted = 0;
unsigned long enabledAtMs = 0;
unsigned long lastJogCommandMs = 0;
unsigned long jogTimerStartedAtMs = 0;
unsigned long nextStepAtUs = 0;
unsigned long stepActiveAtUs = 0;

// Arduino D3 on the ATmega2560 is PE5/OC3C. Timer3 Fast PWM mode 14
// generates a stable 1 kHz, 50% duty signal there without loop or Ethernet
// timing jitter. Timer0 (millis/micros) and the W5100 SPI interface are not
// affected.
void startJogPulseTimer() {
  if (jogTimerRunning) {
    return;
  }
  TCCR3A = 0;
  TCCR3B = 0;
  TCNT3 = 0;
  ICR3 = 1999;   // 16 MHz / 8 / (1999 + 1) = 1 kHz.
  OCR3C = 999;   // 50% duty on OC3C.
  TCCR3A = _BV(COM3C1) | _BV(WGM31);
  TCCR3B = _BV(WGM33) | _BV(WGM32) | _BV(CS31);
  jogTimerStartedAtMs = millis();
  jogTimerRunning = true;
}

void stopJogPulseTimer() {
  TCCR3A = 0;
  TCCR3B = 0;
  jogTimerRunning = false;
  digitalWrite(PIN_STEP_NEG, STEP_IDLE_LEVEL);
}

void disableDriver() {
  stopJogPulseTimer();
  digitalWrite(PIN_ENABLE_NEG, DRIVER_DISABLED_LEVEL);
  moving = false;
  stepActive = false;
  jogMode = false;
}

void printStatus() {
  Serial.print(F("STEPPER_TEST state="));
  Serial.print(moving ? F("moving") : F("stopped"));
  Serial.print(F(" direction="));
  Serial.print(directionName);
  Serial.print(F(" pulses="));
  Serial.print(pulsesCompleted);
  Serial.print('/');
  if (jogMode) {
    Serial.print(F("continuous"));
  } else {
    Serial.print(TEST_PULSES);
  }
  Serial.print(F(" driver="));
  Serial.println(moving ? F("enabled") : F("disabled"));
}

void stopMotion(const __FlashStringHelper *reason) {
  disableDriver();
  Serial.print(F("STEPPER_TEST stopped reason="));
  Serial.println(reason);
  printStatus();
}

void startMotion(char command, bool continuous = false) {
  if (moving) {
    Serial.println(F("STEPPER_TEST rejected reason=already_moving"));
    return;
  }

  directionName = command;
  pulsesCompleted = 0;
  // Installation reference: logical Forward is the mechanism direction
  // produced by a HIGH DIR- output; logical Reverse is LOW.
  digitalWrite(PIN_DIRECTION_NEG, command == 'F' ? HIGH : LOW);
  digitalWrite(PIN_STEP_NEG, STEP_IDLE_LEVEL);
  digitalWrite(PIN_ENABLE_NEG, DRIVER_ENABLED_LEVEL);
  enabledAtMs = millis();
  nextStepAtUs = micros();
  stepActive = false;
  jogMode = continuous;
  lastJogCommandMs = millis();
  moving = true;

  Serial.print(F("STEPPER_TEST accepted direction="));
  Serial.print(command);
  Serial.print(F(" pulses="));
  if (jogMode) {
    Serial.print(F("continuous watchdog_ms="));
    Serial.print(JOG_WATCHDOG_MS);
  } else {
    Serial.print(TEST_PULSES);
  }
  Serial.print(F(" rate_pps="));
  Serial.print(STEP_RATE_PPS);
  Serial.println(F(" wake_ms=250"));
}

void serviceMotion() {
  if (!moving) {
    return;
  }

  if (jogMode && millis() - lastJogCommandMs > JOG_WATCHDOG_MS) {
    stopMotion(F("jog_watchdog"));
    return;
  }

  if (millis() - enabledAtMs < DRIVER_WAKE_MS) {
    return;
  }

  if (jogMode) {
    startJogPulseTimer();
    pulsesCompleted =
        (millis() - jogTimerStartedAtMs) * static_cast<unsigned long>(STEP_RATE_PPS) /
        1000UL;
    return;
  }

  const unsigned long nowUs = micros();
  if (stepActive) {
    if (nowUs - stepActiveAtUs >= STEP_ACTIVE_US) {
      digitalWrite(PIN_STEP_NEG, STEP_IDLE_LEVEL);
      stepActive = false;
      ++pulsesCompleted;
      if (!jogMode && pulsesCompleted >= TEST_PULSES) {
        stopMotion(F("complete"));
      }
    }
    return;
  }

  if (static_cast<long>(nowUs - nextStepAtUs) >= 0) {
    digitalWrite(PIN_STEP_NEG, STEP_ACTIVE_LEVEL);
    stepActiveAtUs = nowUs;
    stepActive = true;
    nextStepAtUs += STEP_INTERVAL_US;
  }
}

void writeJsonStatus(EthernetClient &client, const __FlashStringHelper *result) {
  client.println(F("HTTP/1.1 200 OK"));
  client.println(F("Content-Type: application/json"));
  client.println(F("Cache-Control: no-store"));
  client.println(F("Connection: close"));
  client.println();
  client.print(F("{\"device\":\"CONTROLLINO MAXI Automation\","));
  client.print(F("\"test\":\"SRX02-S operator-gated bring-up\","));
  client.print(F("\"result\":\""));
  client.print(result);
  client.print(F("\",\"state\":\""));
  client.print(moving ? F("moving") : F("stopped"));
  client.print(F("\",\"direction\":\""));
  client.print(directionName);
  client.print(F("\",\"pulses_completed\":"));
  client.print(pulsesCompleted);
  client.print(F(",\"pulse_target\":"));
  if (jogMode) {
    client.print(F("null"));
  } else {
    client.print(TEST_PULSES);
  }
  client.print(F(",\"control_mode\":\""));
  client.print(jogMode ? F("hold_to_jog") : F("finite_test"));
  client.print(F("\",\"rate_pps\":"));
  client.print(STEP_RATE_PPS);
  client.print(F(",\"driver\":\""));
  client.print(moving ? F("enabled") : F("disabled"));
  client.println(F("\",\"limits_connected\":false,\"dro_connected\":false}"));
}

void serviceEthernet() {
  EthernetClient client = server.available();
  if (!client) {
    return;
  }

  char requestLine[96];
  uint8_t length = 0;
  const unsigned long deadline = millis() + 250UL;
  while (client.connected() && static_cast<long>(deadline - millis()) > 0) {
    serviceMotion();
    if (!client.available()) {
      continue;
    }
    const char character = client.read();
    if (character == '\n') {
      break;
    }
    if (character != '\r' && length + 1 < sizeof(requestLine)) {
      requestLine[length++] = character;
    }
  }
  requestLine[length] = '\0';

  const __FlashStringHelper *result = F("status");
  if (strstr(requestLine, "/jog/start?direction=forward") != NULL) {
    if (moving && jogMode && directionName == 'F') {
      lastJogCommandMs = millis();
      result = F("already_jogging");
    } else if (moving) {
      result = F("rejected_already_moving");
    } else {
      startMotion('F', true);
      result = F("accepted");
    }
  } else if (strstr(requestLine, "/jog/start?direction=reverse") != NULL) {
    if (moving && jogMode && directionName == 'R') {
      lastJogCommandMs = millis();
      result = F("already_jogging");
    } else if (moving) {
      result = F("rejected_already_moving");
    } else {
      startMotion('R', true);
      result = F("accepted");
    }
  } else if (strstr(requestLine, "/jog/keepalive?direction=forward") != NULL) {
    if (moving && jogMode && directionName == 'F') {
      lastJogCommandMs = millis();
      result = F("kept_alive");
    } else {
      result = F("ignored_not_jogging_forward");
    }
  } else if (strstr(requestLine, "/jog/keepalive?direction=reverse") != NULL) {
    if (moving && jogMode && directionName == 'R') {
      lastJogCommandMs = millis();
      result = F("kept_alive");
    } else {
      result = F("ignored_not_jogging_reverse");
    }
  } else if (strstr(requestLine, "/move?direction=forward") != NULL) {
    if (moving) {
      result = F("rejected_already_moving");
    } else {
      startMotion('F');
      result = F("accepted");
    }
  } else if (strstr(requestLine, "/move?direction=reverse") != NULL) {
    if (moving) {
      result = F("rejected_already_moving");
    } else {
      startMotion('R');
      result = F("accepted");
    }
  } else if (strstr(requestLine, "/stop") != NULL) {
    stopMotion(F("ethernet_operator"));
    result = F("stopped");
  } else if (strstr(requestLine, "/status") == NULL &&
             strstr(requestLine, "GET / ") == NULL) {
    result = F("unknown_command");
  }

  writeJsonStatus(client, result);
  delay(1);
  client.stop();
}

void handleSerial() {
  while (Serial.available() > 0) {
    char command = Serial.read();
    if (command >= 'a' && command <= 'z') {
      command -= ('a' - 'A');
    }
    switch (command) {
      case 'F':
      case 'R':
        startMotion(command);
        break;
      case 'X':
        stopMotion(F("operator"));
        break;
      case 'S':
        printStatus();
        break;
      case '\r':
      case '\n':
      case ' ':
        break;
      default:
        Serial.println(F("STEPPER_TEST commands=F,R,X,S"));
        break;
    }
  }
}

}  // namespace

void setup() {
  // Preload safe values before enabling the three output drivers.
  digitalWrite(PIN_STEP_NEG, STEP_IDLE_LEVEL);
  digitalWrite(PIN_DIRECTION_NEG, LOW);
  digitalWrite(PIN_ENABLE_NEG, DRIVER_DISABLED_LEVEL);
  pinMode(PIN_STEP_NEG, OUTPUT);
  pinMode(PIN_DIRECTION_NEG, OUTPUT);
  pinMode(PIN_ENABLE_NEG, OUTPUT);

  Serial.begin(9600);
  delay(250);
  Serial.println(F("CONTROLLINO SRX02-S operator-gated bring-up"));
  Serial.println(F("No automatic motion. USB commands: F, R, X=stop, S=status."));

  Ethernet.begin(macAddress, controllerIp, dnsServer, gateway, subnet);
  server.begin();
  Serial.println(F("Ethernet: http://10.77.0.10/status"));
  Serial.println(F("POST /move?direction=forward or reverse; POST /stop"));
  Serial.println(F("Hold-to-jog: POST /jog/start, then /jog/keepalive within 500 ms."));
  printStatus();
}

void loop() {
  handleSerial();
  serviceMotion();
  serviceEthernet();
}
