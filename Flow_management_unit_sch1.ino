/*
 * Flow-management ESP32 I/O firmware (headless supervisor source)
 *
 * Hardware:
 *   - 3 pressure channels on ADS1115 0x48 A0-A2
 *   - 3 flow channels on ADS1115 0x49 A0-A2
 *   - 4 active-low solenoid relays on GPIO 5, 6, 9, and 10
 *
 * Network API:
 *   - GET  /          small JSON service description (no hosted webpage)
 *   - GET  /events    SSE: 10 Hz version-2 "reading" + "sol" state events
 *   - POST /solenoid/toggle?n=0..3
 *
 * The laptop-hosted networked_sensors/dashboard.py owns the UI, plots,
 * metadata, recording, and CSV export.  The previous self-hosted dashboard is
 * archived under networked_sensors/legacy/Flow_management_unit_sch1/.
 *
 * Libraries: Adafruit ADS1X15, ESPAsyncWebServer (ESP32Async), AsyncTCP
 */

#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>

// ── WiFi ─────────────────────────────────────────────────────────────
const char* WIFI_SSID = "Dynamic_2p4Ghz";
const char* WIFI_PASS = "So@ring24";
const char* MDNS_NAME = "testbench";
// ────────────────────────────────────────────────────────────────────

// ── Pins ─────────────────────────────────────────────────────────────
const int I2C_SDA = 3;
const int I2C_SCL = 4;
const int SOLENOID_COUNT = 4;
const int SOLENOID_PINS[SOLENOID_COUNT] = {5, 6, 9, 10};
const bool RELAY_ACTIVE_LOW = true;
// ────────────────────────────────────────────────────────────────────

// ── Sensor scaling ───────────────────────────────────────────────────
const float P_V_MIN = 0.5, P_V_MAX = 4.5, P_MIN = 0.0, P_MAX = 10.0;
const float F_V_MIN = 1.0, F_V_MAX = 5.0, F_MIN = 0.0, F_MAX = 258.58;
const float VOLTS_PER_BIT = 0.0001875;
const unsigned long SAMPLE_PERIOD_MS = 100;
// ────────────────────────────────────────────────────────────────────

Adafruit_ADS1115 adsP;
Adafruit_ADS1115 adsF;
AsyncWebServer server(80);
AsyncEventSource events("/events");

bool solenoidOn[SOLENOID_COUNT] = {false, false, false, false};
unsigned long lastSampleMs = 0;

float mapFloat(float x, float a1, float a2, float b1, float b2) {
  return (x - a1) * (b2 - b1) / (a2 - a1) + b1;
}

void setSolenoid(int index, bool on) {
  if (index < 0 || index >= SOLENOID_COUNT) return;
  solenoidOn[index] = on;
  bool pinHigh = RELAY_ACTIVE_LOW ? !on : on;
  digitalWrite(SOLENOID_PINS[index], pinHigh ? HIGH : LOW);
  Serial.printf("Solenoid %d: %s\n", index + 1, on ? "ON" : "OFF");
}

void broadcastSolenoidState() {
  char json[40];
  snprintf(
      json, sizeof(json), "[%s,%s,%s,%s]",
      solenoidOn[0] ? "true" : "false",
      solenoidOn[1] ? "true" : "false",
      solenoidOn[2] ? "true" : "false",
      solenoidOn[3] ? "true" : "false");
  events.send(json, "sol", millis());
}

void readSensors(float pressure[3], float flow[3],
                 float pressureVolts[3], float flowVolts[3]) {
  for (int i = 0; i < 3; i++) {
    int16_t rawPressure = adsP.readADC_SingleEnded(i);
    pressureVolts[i] = constrain(
        rawPressure * VOLTS_PER_BIT, P_V_MIN, P_V_MAX);
    pressure[i] = mapFloat(
        pressureVolts[i], P_V_MIN, P_V_MAX, P_MIN, P_MAX);

    int16_t rawFlow = adsF.readADC_SingleEnded(i);
    flowVolts[i] = constrain(rawFlow * VOLTS_PER_BIT, F_V_MIN, F_V_MAX);
    flow[i] = mapFloat(flowVolts[i], F_V_MIN, F_V_MAX, F_MIN, F_MAX);
  }
}

void broadcastReading(unsigned long sampleMs) {
  float pressure[3], flow[3], pressureVolts[3], flowVolts[3];
  readSensors(pressure, flow, pressureVolts, flowVolts);

  // Version 2 is one complete, atomic supervisor row.  p[]/f[] are engineering
  // values, p_v[]/f_v[] are the corresponding clamped sensor voltages, and
  // sol[] snapshots output state at the same sampling instant.  The laptop
  // rejects unversioned/incomplete readings so a firmware mismatch is visible.
  char json[384];
  int jsonLength = snprintf(
      json, sizeof(json),
      "{\"v\":2,\"sample_ms\":%lu,"
      "\"p\":[%.3f,%.3f,%.3f],\"f\":[%.2f,%.2f,%.2f],"
      "\"p_v\":[%.4f,%.4f,%.4f],\"f_v\":[%.4f,%.4f,%.4f],"
      "\"sol\":[%s,%s,%s,%s]}",
      sampleMs,
      pressure[0], pressure[1], pressure[2],
      flow[0], flow[1], flow[2],
      pressureVolts[0], pressureVolts[1], pressureVolts[2],
      flowVolts[0], flowVolts[1], flowVolts[2],
      solenoidOn[0] ? "true" : "false",
      solenoidOn[1] ? "true" : "false",
      solenoidOn[2] ? "true" : "false",
      solenoidOn[3] ? "true" : "false");

  if (jsonLength > 0 && jsonLength < (int)sizeof(json)) {
    events.send(json, "reading", sampleMs);
  } else {
    Serial.println("ERROR: reading telemetry buffer too small");
  }
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  Serial.begin(115200);
  delay(500);

  for (int i = 0; i < SOLENOID_COUNT; i++) {
    pinMode(SOLENOID_PINS[i], OUTPUT);
    setSolenoid(i, false);
  }

  Wire.begin(I2C_SDA, I2C_SCL);

  bool hardwareReady = true;
  if (!adsP.begin(0x48)) {
    Serial.println("ERROR: ADS1115 pressure ADC (0x48) not found.");
    hardwareReady = false;
  }
  if (!adsF.begin(0x49)) {
    Serial.println("ERROR: ADS1115 flow ADC (0x49) not found.");
    hardwareReady = false;
  }
  if (!hardwareReady) {
    // Match the existing system's fail-closed bring-up: relays remain OFF and
    // no network command surface starts with an incomplete sensor bus.
    Serial.println("FATAL: sensor hardware unavailable; restart after repair.");
    while (true) {
      digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
      delay(250);
    }
  }

  adsP.setGain(GAIN_TWOTHIRDS);
  adsF.setGain(GAIN_TWOTHIRDS);
  adsP.setDataRate(RATE_ADS1115_860SPS);
  adsF.setDataRate(RATE_ADS1115_860SPS);
  Serial.println("Both ADS1115 chips ready.");

  Serial.print("Connecting to WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  unsigned long wifiStartMs = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (millis() - wifiStartMs > 30000) {
      Serial.println("\nWiFi timeout, rebooting.");
      delay(1000);
      ESP.restart();
    }
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
  }

  Serial.println("\nConnected!");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());

  if (MDNS.begin(MDNS_NAME)) {
    Serial.printf("mDNS started: http://%s.local\n", MDNS_NAME);
    MDNS.addService("http", "tcp", 80);
  }

  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(
        200, "application/json",
        "{\"service\":\"flow-management-esp32\",\"api_version\":2,"
        "\"telemetry\":\"/events\",\"dashboard_host\":\"laptop\"}");
  });

  server.on("/solenoid/toggle", HTTP_POST, [](AsyncWebServerRequest *request) {
    if (!request->hasParam("n")) {
      request->send(400, "text/plain", "missing n");
      return;
    }
    int index = request->getParam("n")->value().toInt();
    if (index < 0 || index >= SOLENOID_COUNT) {
      request->send(400, "text/plain", "bad n");
      return;
    }
    setSolenoid(index, !solenoidOn[index]);
    broadcastSolenoidState();
    request->send(200, "text/plain", solenoidOn[index] ? "ON" : "OFF");
  });

  events.onConnect([](AsyncEventSourceClient *client) {
    Serial.println("Supervisor connected.");
    broadcastSolenoidState();
  });
  server.addHandler(&events);
  server.begin();

  digitalWrite(LED_BUILTIN, HIGH);
  Serial.println("Headless ESP32 API ready; dashboard is hosted by the laptop.");
}

void loop() {
  unsigned long now = millis();
  if (now - lastSampleMs < SAMPLE_PERIOD_MS) return;
  lastSampleMs = now;
  broadcastReading(now);
}
