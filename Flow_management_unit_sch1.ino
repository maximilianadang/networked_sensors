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
 *   - GET  /events    SSE: 10 Hz version-3 "reading" + "sol" state events
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
const char* WIFI_SSID = "GL-MT3000-b3a";
const char* WIFI_PASS = "4ACZ53S976";
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
const unsigned long ADC_HEALTH_PERIOD_MS = 1000;
const unsigned long ADC_RETRY_PERIOD_MS = 5000;
const uint8_t PRESSURE_ADC_ADDRESS = 0x48;
const uint8_t FLOW_ADC_ADDRESS = 0x49;
// ────────────────────────────────────────────────────────────────────

Adafruit_ADS1115 adsP;
Adafruit_ADS1115 adsF;
AsyncWebServer server(80);
AsyncEventSource events("/events");

bool solenoidOn[SOLENOID_COUNT] = {false, false, false, false};
bool pressureAdcReady = false;
bool flowAdcReady = false;
unsigned long lastSampleMs = 0;
unsigned long lastAdcHealthMs = 0;
unsigned long lastAdcRetryMs = 0;

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

bool i2cDevicePresent(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

bool initializePressureAdc() {
  if (!adsP.begin(PRESSURE_ADC_ADDRESS)) return false;
  adsP.setGain(GAIN_TWOTHIRDS);
  adsP.setDataRate(RATE_ADS1115_860SPS);
  Serial.println("ADS1115 pressure ADC (0x48) ready.");
  return true;
}

bool initializeFlowAdc() {
  if (!adsF.begin(FLOW_ADC_ADDRESS)) return false;
  adsF.setGain(GAIN_TWOTHIRDS);
  adsF.setDataRate(RATE_ADS1115_860SPS);
  Serial.println("ADS1115 flow ADC (0x49) ready.");
  return true;
}

void refreshAdcState(unsigned long now) {
  if (now - lastAdcHealthMs < ADC_HEALTH_PERIOD_MS) return;
  lastAdcHealthMs = now;

  if (pressureAdcReady && !i2cDevicePresent(PRESSURE_ADC_ADDRESS)) {
    pressureAdcReady = false;
    Serial.println("WARNING: pressure ADC disconnected; publishing null values.");
  }
  if (flowAdcReady && !i2cDevicePresent(FLOW_ADC_ADDRESS)) {
    flowAdcReady = false;
    Serial.println("WARNING: flow ADC disconnected; publishing null values.");
  }

  if ((pressureAdcReady && flowAdcReady) ||
      now - lastAdcRetryMs < ADC_RETRY_PERIOD_MS) return;
  lastAdcRetryMs = now;
  if (!pressureAdcReady) pressureAdcReady = initializePressureAdc();
  if (!flowAdcReady) flowAdcReady = initializeFlowAdc();
}

void readSensors(float pressure[3], float flow[3],
                 float pressureVolts[3], float flowVolts[3]) {
  for (int i = 0; i < 3; i++) {
    pressure[i] = NAN;
    flow[i] = NAN;
    pressureVolts[i] = NAN;
    flowVolts[i] = NAN;

    if (pressureAdcReady) {
      int16_t rawPressure = adsP.readADC_SingleEnded(i);
      pressureVolts[i] = constrain(
          rawPressure * VOLTS_PER_BIT, P_V_MIN, P_V_MAX);
      pressure[i] = mapFloat(
          pressureVolts[i], P_V_MIN, P_V_MAX, P_MIN, P_MAX);
    }
    if (flowAdcReady) {
      int16_t rawFlow = adsF.readADC_SingleEnded(i);
      flowVolts[i] = constrain(rawFlow * VOLTS_PER_BIT, F_V_MIN, F_V_MAX);
      flow[i] = mapFloat(flowVolts[i], F_V_MIN, F_V_MAX, F_MIN, F_MAX);
    }
  }
}

void formatJsonNumber(char *buffer, size_t size, float value, int decimals) {
  if (isfinite(value)) {
    snprintf(buffer, size, "%.*f", decimals, value);
  } else {
    snprintf(buffer, size, "null");
  }
}

void broadcastReading(unsigned long sampleMs) {
  float pressure[3], flow[3], pressureVolts[3], flowVolts[3];
  readSensors(pressure, flow, pressureVolts, flowVolts);

  char p[3][16], f[3][16], pVolts[3][16], fVolts[3][16];
  for (int i = 0; i < 3; i++) {
    formatJsonNumber(p[i], sizeof(p[i]), pressure[i], 3);
    formatJsonNumber(f[i], sizeof(f[i]), flow[i], 2);
    formatJsonNumber(pVolts[i], sizeof(pVolts[i]), pressureVolts[i], 4);
    formatJsonNumber(fVolts[i], sizeof(fVolts[i]), flowVolts[i], 4);
  }

  // Version 3 remains atomic but makes sensor availability explicit. Missing
  // ADC families publish null triplets while sample time and relay state stay
  // live, so an unavailable sensor cannot hide the controller from the laptop.
  char json[448];
  int jsonLength = snprintf(
      json, sizeof(json),
      "{\"v\":3,\"sample_ms\":%lu,"
      "\"p_adc_ok\":%s,\"f_adc_ok\":%s,"
      "\"p\":[%s,%s,%s],\"f\":[%s,%s,%s],"
      "\"p_v\":[%s,%s,%s],\"f_v\":[%s,%s,%s],"
      "\"sol\":[%s,%s,%s,%s]}",
      sampleMs,
      pressureAdcReady ? "true" : "false",
      flowAdcReady ? "true" : "false",
      p[0], p[1], p[2], f[0], f[1], f[2],
      pVolts[0], pVolts[1], pVolts[2],
      fVolts[0], fVolts[1], fVolts[2],
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

  pressureAdcReady = initializePressureAdc();
  flowAdcReady = initializeFlowAdc();
  if (!pressureAdcReady) {
    Serial.println("WARNING: pressure ADC absent; network/control will continue.");
  }
  if (!flowAdcReady) {
    Serial.println("WARNING: flow ADC absent; network/control will continue.");
  }
  lastAdcHealthMs = millis();
  lastAdcRetryMs = millis();

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
    char descriptor[192];
    snprintf(
        descriptor, sizeof(descriptor),
        "{\"service\":\"flow-management-esp32\",\"api_version\":3,"
        "\"pressure_adc_ok\":%s,\"flow_adc_ok\":%s,"
        "\"telemetry\":\"/events\",\"dashboard_host\":\"laptop\"}",
        pressureAdcReady ? "true" : "false",
        flowAdcReady ? "true" : "false");
    request->send(200, "application/json", descriptor);
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
  refreshAdcState(now);
  if (now - lastSampleMs < SAMPLE_PERIOD_MS) return;
  lastSampleMs = now;
  broadcastReading(now);
}
