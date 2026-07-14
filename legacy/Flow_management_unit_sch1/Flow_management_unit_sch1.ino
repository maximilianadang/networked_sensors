/*
 * LEGACY ARCHIVE: self-hosted ESP32 dashboard retained for reference/fallback.
 * The primary firmware is networked_sensors/Flow_management_unit_sch1.ino.
 *
 * Test Bench — Streaming CSV version (no JSON-escape bottleneck for long tests)
 * 3× pressure (ADS1115 #1: A0/A1/A2)  → bar (gauge)
 * 3× Festo flow (ADS1115 #2: A0/A1/A2)
 * 3× solenoids (GPIO 5/6/9)
 *
 * Libraries: Adafruit ADS1X15, ESPAsyncWebServer (ESP32Async), AsyncTCP (ESP32Async)
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
const int SOLENOID_PINS[3] = {5, 6, 9};
const bool RELAY_ACTIVE_LOW = true;
// ────────────────────────────────────────────────────────────────────

// ── Sensor scaling ───────────────────────────────────────────────────
const float P_V_MIN = 0.5, P_V_MAX = 4.5, P_MIN = 0.0, P_MAX = 10.0;
const float F_V_MIN = 1.0, F_V_MAX = 5.0, F_MIN = 0.0, F_MAX = 258.58;
const float VOLTS_PER_BIT = 0.0001875;
// ────────────────────────────────────────────────────────────────────

Adafruit_ADS1115 adsP;
Adafruit_ADS1115 adsF;
AsyncWebServer server(80);
AsyncEventSource events("/events");

bool solenoidOn[3] = {false, false, false};

bool   recording   = false;
unsigned long testStartMs = 0;
String csvDataBuffer;

float mapFloat(float x, float a1, float a2, float b1, float b2) {
  return (x - a1) * (b2 - b1) / (a2 - a1) + b1;
}

void setSolenoid(int idx, bool on) {
  if (idx < 0 || idx > 2) return;
  solenoidOn[idx] = on;
  bool pinHigh = RELAY_ACTIVE_LOW ? !on : on;
  digitalWrite(SOLENOID_PINS[idx], pinHigh ? HIGH : LOW);
  Serial.printf("Solenoid %d: %s\n", idx + 1, on ? "ON" : "OFF");
}

void readSensors(float p[3], float f[3], float vP[3], float vF[3]) {
  for (int i = 0; i < 3; i++) {
    int16_t rawP = adsP.readADC_SingleEnded(i);
    vP[i] = constrain(rawP * VOLTS_PER_BIT, P_V_MIN, P_V_MAX);
    p[i]  = mapFloat(vP[i], P_V_MIN, P_V_MAX, P_MIN, P_MAX);

    int16_t rawF = adsF.readADC_SingleEnded(i);
    vF[i] = constrain(rawF * VOLTS_PER_BIT, F_V_MIN, F_V_MAX);
    f[i]  = mapFloat(vF[i], F_V_MIN, F_V_MAX, F_MIN, F_MAX);
  }
}

// ── HTML page ────────────────────────────────────────────────────────
const char HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Test Bench</title>
<script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0f0f1a;color:#e8e8ec;font-family:-apple-system,sans-serif;padding:24px}
  h1{font-size:1.3em;color:#aaa;margin-bottom:20px}
  h2{font-size:0.9em;color:#888;margin-bottom:10px;text-transform:uppercase;letter-spacing:0.6px}
  .row{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px}
  .card{background:#1a1a2e;border-radius:12px;padding:14px 20px;min-width:130px}
  .card .label{font-size:0.7em;color:#888;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.5px}
  .card .value{font-size:1.5em;font-weight:600;display:inline-block}
  .card .unit{font-size:0.78em;color:#888;margin-left:5px}
  .p1{color:#00d4ff}.p2{color:#5ad7ff}.p3{color:#3affc1}
  .f1{color:#ff6b6b}.f2{color:#ff9a5a}.f3{color:#ffd166}
  .pc{color:#aaffec}.fc{color:#ffe6b0}
  .panel{background:#1a1a2e;border-radius:12px;padding:20px;margin-bottom:18px}
  .ctrlrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
  .ctrlrow:last-child{margin-bottom:0}
  .metagrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-bottom:12px}
  .field{display:flex;flex-direction:column}
  .field label{font-size:0.72em;color:#888;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.5px}
  input[type=text],input[type=number],textarea{background:#0f0f1a;border:1px solid #333;color:#e8e8ec;padding:8px 12px;border-radius:6px;font-size:0.95em;font-family:inherit;width:100%}
  textarea{min-height:60px;resize:vertical}
  button{background:#2a2a44;border:1px solid #444;color:#e8e8ec;padding:10px 16px;border-radius:8px;cursor:pointer;font-size:0.95em}
  button:hover{background:#383858}
  button:disabled{opacity:0.4;cursor:not-allowed}
  button.primary{background:#00d4ff;color:#000;border-color:#00d4ff;font-weight:600}
  button.danger{background:#ff4757;color:#fff;border-color:#ff4757;font-weight:600}
  button.sol{min-width:155px}
  button.sol.on{background:#00ff88;color:#000;border-color:#00ff88;font-weight:600}
  .status{font-size:0.85em;color:#888;margin-left:auto}
  .status.rec{color:#ff4757;font-weight:600}
  .timer{font-family:monospace;font-size:0.95em;color:#ff4757;margin-left:10px}
  .charts{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .chart-wrap{background:#1a1a2e;border-radius:12px;padding:18px}
  .chart-title{font-size:0.85em;color:#888;margin-bottom:8px}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;background:#00ff88;animation:p 1s infinite}
  @keyframes p{0%,100%{opacity:1}50%{opacity:0.3}}
  kbd{background:#333;padding:2px 7px;border-radius:4px;font-size:0.85em;font-family:monospace;color:#fff;margin-left:6px}
  .legend{display:inline-flex;gap:12px;font-size:0.78em;color:#aaa;margin-left:14px}
  .legend span::before{content:'';display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle}
  .l1::before{background:#00d4ff}.l2::before{background:#5ad7ff}.l3::before{background:#3affc1}
  .l4::before{background:#ff6b6b}.l5::before{background:#ff9a5a}.l6::before{background:#ffd166}
  @media (max-width:800px){.charts{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>Test Bench Controller</h1>

<div class='panel'>
  <h2>Test metadata</h2>
  <div class='metagrid'>
    <div class='field'><label>Sample number</label><input type='text' id='m_sample' placeholder='e.g. A1'></div>
    <div class='field'><label>Sub number</label><input type='text' id='m_sub' placeholder='e.g. 03'></div>
    <div class='field'><label>Dispenser</label><input type='text' id='m_disp' placeholder='e.g. nozzle_2mm'></div>
    <div class='field'><label>Material</label><input type='text' id='m_mat' placeholder='e.g. PA12'></div>
    <div class='field'><label>Powder flow rate (g/min)</label><input type='number' id='m_pflow' step='0.1' placeholder='e.g. 50.0'></div>
    <div class='field' style='grid-column:1/-1'><label>Description</label><input type='text' id='m_desc' placeholder='e.g. burst test 3bar'></div>
    <div class='field' style='grid-column:1/-1'><label>Notes</label><textarea id='m_notes' placeholder='free-text notes'></textarea></div>
  </div>
</div>

<div class='panel'>
  <h2>Recording</h2>
  <div class='ctrlrow'>
    <button class='primary' id='startBtn'>Start Test</button>
    <button class='danger' id='stopBtn' disabled>Stop &amp; Save</button>
    <span class='status' id='recStatus'>Not recording</span>
    <span class='timer' id='timer'></span>
  </div>
  <div class='ctrlrow'>
    <button class='sol' id='sol0'>Solenoid 1 <kbd>1</kbd></button>
    <button class='sol' id='sol1'>Solenoid 2 <kbd>2</kbd></button>
    <button class='sol' id='sol2'>Solenoid 3 <kbd>3</kbd></button>
  </div>
</div>

<div class='row'>
  <div class='card'><div class='label'>Pressure 1</div><span class='value p1' id='p0'>--</span><span class='unit'>bar</span></div>
  <div class='card'><div class='label'>Pressure 2</div><span class='value p2' id='p1'>--</span><span class='unit'>bar</span></div>
  <div class='card'><div class='label'>Pressure 3</div><span class='value p3' id='p2'>--</span><span class='unit'>bar</span></div>
  <div class='card'><div class='label'>P combined</div><span class='value pc' id='pc'>--</span><span class='unit'>bar</span></div>
  <div class='card'><div class='label'>Flow 1</div><span class='value f1' id='f0'>--</span><span class='unit'>g/min</span></div>
  <div class='card'><div class='label'>Flow 2</div><span class='value f2' id='f1'>--</span><span class='unit'>g/min</span></div>
  <div class='card'><div class='label'>Flow 3</div><span class='value f3' id='f2'>--</span><span class='unit'>g/min</span></div>
  <div class='card'><div class='label'>F combined</div><span class='value fc' id='fc'>--</span><span class='unit'>g/min</span></div>
</div>

<div class='charts'>
  <div class='chart-wrap'>
    <div class='chart-title'><span class='dot'></span>Pressure (bar)
      <span class='legend'><span class='l1'>P1</span><span class='l2'>P2</span><span class='l3'>P3</span></span>
    </div>
    <canvas id='chartP'></canvas>
  </div>
  <div class='chart-wrap'>
    <div class='chart-title'><span class='dot'></span>Flow rate (g/min)
      <span class='legend'><span class='l4'>F1</span><span class='l5'>F2</span><span class='l6'>F3</span></span>
    </div>
    <canvas id='chartF'></canvas>
  </div>
</div>

<script>
const MAX_POINTS = 120;
const labels = [];
const pData = [[], [], []];
const fData = [[], [], []];

const pColors = ['#00d4ff', '#5ad7ff', '#3affc1'];
const fColors = ['#ff6b6b', '#ff9a5a', '#ffd166'];

function makeChart(id, colors, data){
  return new Chart(document.getElementById(id).getContext('2d'),{
    type:'line',
    data:{
      labels:labels,
      datasets: [0,1,2].map(i => ({
        label: 'Ch ' + (i+1),
        data: data[i],
        borderColor: colors[i],
        backgroundColor: colors[i] + '10',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3,
        fill: false
      }))
    },
    options:{animation:false,responsive:true,
      scales:{x:{ticks:{color:'#555',maxTicksLimit:6},grid:{color:'#222'}},
              y:{ticks:{color:'#aaa'},grid:{color:'#333'}}},
      plugins:{legend:{display:false}}}});
}
const chartP = makeChart('chartP', pColors, pData);
const chartF = makeChart('chartF', fColors, fData);

const META_FIELDS = ['m_sample','m_sub','m_disp','m_mat','m_desc','m_pflow','m_notes'];
function loadMeta(){
  META_FIELDS.forEach(k => {
    const v = localStorage.getItem(k);
    if (v !== null) document.getElementById(k).value = v;
  });
}
function saveMetaToStorage(){
  META_FIELDS.forEach(k => localStorage.setItem(k, document.getElementById(k).value));
}
META_FIELDS.forEach(k => {
  document.getElementById(k).addEventListener('input', saveMetaToStorage);
});
loadMeta();

let testStart = 0;
let timerInterval = null;
function fmtTime(ms){
  const s = Math.floor(ms/1000);
  const m = Math.floor(s/60);
  const ss = (s%60).toString().padStart(2,'0');
  return m + ':' + ss;
}
function startTimer(){
  testStart = Date.now();
  stopTimer();
  timerInterval = setInterval(() => {
    document.getElementById('timer').textContent = fmtTime(Date.now() - testStart);
  }, 100);
}
function stopTimer(){
  if (timerInterval){clearInterval(timerInterval); timerInterval = null;}
}

const src = new EventSource('/events');
src.addEventListener('reading', e => {
  const d = JSON.parse(e.data);
  for (let i = 0; i < 3; i++){
    document.getElementById('p' + i).textContent = d.p[i].toFixed(2);
    document.getElementById('f' + i).textContent = d.f[i].toFixed(1);
  }
  const pCombined = Math.min(d.p[0], d.p[1], d.p[2]);
  const fCombined = d.f[0] + d.f[1] + d.f[2];
  document.getElementById('pc').textContent = pCombined.toFixed(2);
  document.getElementById('fc').textContent = fCombined.toFixed(1);

  labels.push(new Date().toLocaleTimeString());
  for (let i = 0; i < 3; i++){
    pData[i].push(d.p[i]);
    fData[i].push(d.f[i]);
  }
  if (labels.length > MAX_POINTS){
    labels.shift();
    for (let i = 0; i < 3; i++){pData[i].shift(); fData[i].shift();}
  }
  chartP.update();
  chartF.update();
});

src.addEventListener('sol', e => {
  const states = JSON.parse(e.data);
  for (let i = 0; i < 3; i++){
    const btn = document.getElementById('sol' + i);
    btn.classList.toggle('on', states[i]);
    btn.innerHTML = 'Solenoid ' + (i+1) + (states[i] ? ' — ON' : '') + ' <kbd>' + (i+1) + '</kbd>';
  }
});

src.addEventListener('rec', e => {
  const d = JSON.parse(e.data);
  document.getElementById('startBtn').disabled = d.recording;
  document.getElementById('stopBtn').disabled  = !d.recording;
  const s = document.getElementById('recStatus');
  s.classList.toggle('rec', d.recording);
  s.textContent = d.recording ? 'Recording' : 'Not recording';
  if (d.recording){startTimer();}
  else {stopTimer(); document.getElementById('timer').textContent = '';}
});

for (let i = 0; i < 3; i++){
  document.getElementById('sol' + i).onclick = () =>
    fetch('/solenoid/toggle?n=' + i, {method:'POST'});
}

document.getElementById('startBtn').onclick = () => {
  fetch('/test/start', {method:'POST'});
};

function sanitize(s){
  return (s || '').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '');
}
function isoDate(){
  const d = new Date();
  const pad = n => n.toString().padStart(2,'0');
  return d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate())
       + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}
function dateOnly(){
  const d = new Date();
  const pad = n => n.toString().padStart(2,'0');
  return d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate());
}

function csvVal(v){
  const s = (v === null || v === undefined) ? '' : String(v);
  if (s.indexOf(',') !== -1 || s.indexOf('"') !== -1 || s.indexOf('\n') !== -1) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}
function fmt(n, d){
  return (n === null || n === undefined) ? '' : Number(n).toFixed(d);
}

document.getElementById('stopBtn').onclick = async () => {
  await fetch('/test/stop', {method:'POST'});

  // 1) Fetch only the small metrics JSON (computed averages)
  const mres = await fetch('/test/metrics');
  const metrics = await mres.json();

  // 2) Fetch the raw CSV data as a Blob (streaming, no JSON wrapping)
  const cres = await fetch('/test/csv');
  const csvBlob = await cres.blob();

  // 3) Build metadata header text from form + metrics
  const sampleNum = document.getElementById('m_sample').value;
  const subNum    = document.getElementById('m_sub').value;
  const disp      = document.getElementById('m_disp').value;
  const mat       = document.getElementById('m_mat').value;
  const desc      = document.getElementById('m_desc').value;
  const pflow     = document.getElementById('m_pflow').value;
  const notes     = document.getElementById('m_notes').value;

  const p1 = fmt(metrics.pressure_bar_first1s[0], 3);
  const p2 = fmt(metrics.pressure_bar_first1s[1], 3);
  const p3 = fmt(metrics.pressure_bar_first1s[2], 3);
  const pc = fmt(metrics.pressure_combined_bar_first1s, 3);
  const f1 = fmt(metrics.air_flow_g_per_min_active[0], 2);
  const f2 = fmt(metrics.air_flow_g_per_min_active[1], 2);
  const f3 = fmt(metrics.air_flow_g_per_min_active[2], 2);
  const fc = fmt(metrics.air_flow_combined_g_per_min_active, 2);

  const meta = [
    '# schema_version:,1.0',
    '# date:,' + csvVal(isoDate()),
    '# sample_number:,' + csvVal(sampleNum),
    '# sub_number:,' + csvVal(subNum),
    '# dispenser:,' + csvVal(disp),
    '# material:,' + csvVal(mat),
    '#',
    '# pressure_1_bar:,' + p1,
    '# pressure_2_bar:,' + p2,
    '# pressure_3_bar:,' + p3,
    '# pressure_combined_bar:,' + pc,
    '# air_flow_1_g_per_min:,' + f1,
    '# air_flow_2_g_per_min:,' + f2,
    '# air_flow_3_g_per_min:,' + f3,
    '# air_flow_combined_g_per_min:,' + fc,
    '# powder_flow_rate_g_per_min:,' + csvVal(pflow),
    '# description:,' + csvVal(desc),
    '# notes:,' + csvVal(notes),
    '# ==='
  ].join('\n') + '\n';

  // 4) Combine metadata + CSV as a single Blob (memory-efficient)
  const fullBlob = new Blob([meta, csvBlob], {type:'text/csv'});

  const descShort = sanitize(desc).slice(0, 12);
  const fname = dateOnly() + '_S' + sanitize(sampleNum) + '_R' + sanitize(subNum) +
                '_' + sanitize(disp) + '_' + sanitize(mat) +
                '_' + descShort + '.csv';

  const url = URL.createObjectURL(fullBlob);
  const a = document.createElement('a');
  a.href = url; a.download = fname;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '1') fetch('/solenoid/toggle?n=0', {method:'POST'});
  if (e.key === '2') fetch('/solenoid/toggle?n=1', {method:'POST'});
  if (e.key === '3') fetch('/solenoid/toggle?n=2', {method:'POST'});
});
</script>
</body>
</html>
)rawliteral";

void broadcastSolState() {
  String json = "[";
  for (int i = 0; i < 3; i++) {
    json += (solenoidOn[i] ? "true" : "false");
    if (i < 2) json += ",";
  }
  json += "]";
  events.send(json.c_str(), "sol", millis());
}

void broadcastRecState() {
  String json = "{\"recording\":" + String(recording ? "true" : "false") + "}";
  events.send(json.c_str(), "rec", millis());
}

void computeAutoFields(float pBarFirst1s[3], float &pCombFirst1s,
                       float fActive[3], float &fCombActive) {
  for (int i = 0; i < 3; i++) {
    pBarFirst1s[i] = NAN;
    fActive[i] = NAN;
  }
  pCombFirst1s = NAN;
  fCombActive  = NAN;

  if (csvDataBuffer.isEmpty()) return;

  int start = csvDataBuffer.indexOf('\n');
  if (start < 0) return;
  start++;

  float pSum[3] = {0, 0, 0};
  int   pCnt[3] = {0, 0, 0};
  float fSum[3] = {0, 0, 0};
  int   fCnt[3] = {0, 0, 0};
  bool  inActive[3] = {false, false, false};
  bool  fDone[3]    = {false, false, false};

  float pcSum = 0; int pcCnt = 0;
  float fcSum = 0; int fcCnt = 0;
  bool  fcInActive = false;
  bool  fcDone     = false;

  String row;
  int idx = start;
  while (idx < (int)csvDataBuffer.length()) {
    int nl = csvDataBuffer.indexOf('\n', idx);
    if (nl < 0) break;
    row = csvDataBuffer.substring(idx, nl);
    idx = nl + 1;
    if (row.length() == 0) continue;

    int positions[8];
    positions[0] = -1;
    int found = 0;
    int searchFrom = 0;
    for (int k = 0; k < 7; k++) {
      int p = row.indexOf(',', searchFrom);
      if (p < 0) { found = -1; break; }
      positions[k + 1] = p;
      searchFrom = p + 1;
      found++;
    }
    if (found < 7) continue;

    float t  = row.substring(positions[0] + 1, positions[1]).toFloat();
    float pv[3], fv[3];
    pv[0] = row.substring(positions[1] + 1, positions[2]).toFloat();
    pv[1] = row.substring(positions[2] + 1, positions[3]).toFloat();
    pv[2] = row.substring(positions[3] + 1, positions[4]).toFloat();
    fv[0] = row.substring(positions[4] + 1, positions[5]).toFloat();
    fv[1] = row.substring(positions[5] + 1, positions[6]).toFloat();
    fv[2] = row.substring(positions[6] + 1, positions[7]).toFloat();

    float pComb = min(pv[0], min(pv[1], pv[2]));
    float fComb = fv[0] + fv[1] + fv[2];

    if (t <= 1.0) {
      for (int i = 0; i < 3; i++) {
        pSum[i] += pv[i];
        pCnt[i]++;
      }
      pcSum += pComb;
      pcCnt++;
    }

    for (int i = 0; i < 3; i++) {
      if (fDone[i]) continue;
      if (!inActive[i] && fv[i] > 10.0) inActive[i] = true;
      if (inActive[i]) {
        if (fv[i] >= 10.0) {
          fSum[i] += fv[i];
          fCnt[i]++;
        } else {
          inActive[i] = false;
          fDone[i] = true;
        }
      }
    }

    if (!fcDone) {
      if (!fcInActive && fComb > 10.0) fcInActive = true;
      if (fcInActive) {
        if (fComb >= 10.0) {
          fcSum += fComb;
          fcCnt++;
        } else {
          fcInActive = false;
          fcDone = true;
        }
      }
    }
  }

  for (int i = 0; i < 3; i++) {
    if (pCnt[i] > 0) pBarFirst1s[i] = pSum[i] / pCnt[i];
    if (fCnt[i] > 0) fActive[i] = fSum[i] / fCnt[i];
  }
  if (pcCnt > 0) pCombFirst1s = pcSum / pcCnt;
  if (fcCnt > 0) fCombActive  = fcSum / fcCnt;
}

String numOrNull(float v, int decimals) {
  if (isnan(v)) return "null";
  return String(v, decimals);
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  Serial.begin(115200);
  delay(500);

  for (int i = 0; i < 3; i++) {
    pinMode(SOLENOID_PINS[i], OUTPUT);
    setSolenoid(i, false);
  }

  Wire.begin(I2C_SDA, I2C_SCL);

  bool hwOk = true;
  if (!adsP.begin(0x48)) { Serial.println("ERROR: ADS1115 #1 (0x48) not found."); hwOk = false; }
  if (!adsF.begin(0x49)) { Serial.println("ERROR: ADS1115 #2 (0x49) not found."); hwOk = false; }
  if (hwOk) {
    adsP.setGain(GAIN_TWOTHIRDS);
    adsF.setGain(GAIN_TWOTHIRDS);
    adsP.setDataRate(RATE_ADS1115_860SPS);
    adsF.setDataRate(RATE_ADS1115_860SPS);
    Serial.println("Both ADS1115 chips ready.");
  }

  Serial.print("Connecting to WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  unsigned long wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
    if (millis() - wifiStart > 30000) {
      Serial.println("\nWiFi timeout, rebooting."); delay(1000); ESP.restart();
    }
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
  }
  Serial.println("\nConnected!");
  Serial.print("IP: "); Serial.println(WiFi.localIP());

  if (MDNS.begin(MDNS_NAME)) {
    Serial.printf("mDNS started: http://%s.local\n", MDNS_NAME);
    MDNS.addService("http", "tcp", 80);
  }

  digitalWrite(LED_BUILTIN, HIGH);

  server.on("/", HTTP_GET, [](AsyncWebServerRequest *req) {
    req->send_P(200, "text/html", HTML);
  });

  server.on("/solenoid/toggle", HTTP_POST, [](AsyncWebServerRequest *req) {
    if (!req->hasParam("n")) { req->send(400, "text/plain", "missing n"); return; }
    int n = req->getParam("n")->value().toInt();
    if (n < 0 || n > 2) { req->send(400, "text/plain", "bad n"); return; }
    setSolenoid(n, !solenoidOn[n]);
    broadcastSolState();
    req->send(200, "text/plain", solenoidOn[n] ? "ON" : "OFF");
  });

  server.on("/test/start", HTTP_POST, [](AsyncWebServerRequest *req) {
    csvDataBuffer = "time_s,p1_bar,p2_bar,p3_bar,f1_gmin,f2_gmin,f3_gmin,"
                    "p1_volt,p2_volt,p3_volt,f1_volt,f2_volt,f3_volt,"
                    "sol1,sol2,sol3,"
                    "p_combined_bar,f_combined_gmin\n";
    testStartMs = millis();
    recording = true;
    Serial.println("Recording started");
    broadcastRecState();
    req->send(200, "text/plain", "started");
  });

  server.on("/test/stop", HTTP_POST, [](AsyncWebServerRequest *req) {
    recording = false;
    Serial.printf("Recording stopped (%d bytes)\n", csvDataBuffer.length());
    broadcastRecState();
    req->send(200, "text/plain", "stopped");
  });

  // ── NEW: small JSON with just the computed metrics ───────────────
  server.on("/test/metrics", HTTP_GET, [](AsyncWebServerRequest *req) {
    float pBar[3], fAct[3], pComb, fComb;
    computeAutoFields(pBar, pComb, fAct, fComb);

    String json = "{";
    json += "\"pressure_bar_first1s\":["
         + numOrNull(pBar[0], 3) + ","
         + numOrNull(pBar[1], 3) + ","
         + numOrNull(pBar[2], 3) + "],";
    json += "\"pressure_combined_bar_first1s\":" + numOrNull(pComb, 3) + ",";
    json += "\"air_flow_g_per_min_active\":["
         + numOrNull(fAct[0], 3) + ","
         + numOrNull(fAct[1], 3) + ","
         + numOrNull(fAct[2], 3) + "],";
    json += "\"air_flow_combined_g_per_min_active\":" + numOrNull(fComb, 3);
    json += "}";
    req->send(200, "application/json", json);
  });

  // ── NEW: stream raw CSV data directly, no JSON wrapping ──────────
  server.on("/test/csv", HTTP_GET, [](AsyncWebServerRequest *req) {
    // Stream the csvDataBuffer in chunks. Captures buffer pointer + offset.
    // ESPAsyncWebServer calls the callback repeatedly until it returns 0.
    size_t totalLen = csvDataBuffer.length();
    AsyncWebServerResponse *res = req->beginChunkedResponse("text/csv",
      [totalLen](uint8_t *buffer, size_t maxLen, size_t index) -> size_t {
        if (index >= totalLen) return 0; // done
        size_t remaining = totalLen - index;
        size_t toSend = (remaining < maxLen) ? remaining : maxLen;
        memcpy(buffer, csvDataBuffer.c_str() + index, toSend);
        return toSend;
      });
    res->addHeader("Content-Disposition", "attachment; filename=\"data.csv\"");
    req->send(res);
  });

  events.onConnect([](AsyncEventSourceClient *c) {
    Serial.println("Browser connected.");
    broadcastSolState();
    broadcastRecState();
  });
  server.addHandler(&events);
  server.begin();
}

unsigned long lastSample = 0;
const unsigned long SAMPLE_PERIOD_MS = 100;

void loop() {
  unsigned long now = millis();
  if (now - lastSample < SAMPLE_PERIOD_MS) return;
  lastSample = now;

  float p[3], f[3], vP[3], vF[3];
  readSensors(p, f, vP, vF);

  float pComb = min(p[0], min(p[1], p[2]));
  float fComb = f[0] + f[1] + f[2];

  String json = "{\"p\":[" + String(p[0], 3) + "," + String(p[1], 3) + "," + String(p[2], 3) +
                "],\"f\":[" + String(f[0], 2) + "," + String(f[1], 2) + "," + String(f[2], 2) + "]}";
  events.send(json.c_str(), "reading", now);

  if (recording) {
    char row[260];
    float t = (now - testStartMs) / 1000.0;
    snprintf(row, sizeof(row),
             "%.3f,%.3f,%.3f,%.3f,%.2f,%.2f,%.2f,"
             "%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,"
             "%d,%d,%d,"
             "%.3f,%.2f\n",
             t,
             p[0], p[1], p[2],
             f[0], f[1], f[2],
             vP[0], vP[1], vP[2],
             vF[0], vF[1], vF[2],
             solenoidOn[0] ? 1 : 0,
             solenoidOn[1] ? 1 : 0,
             solenoidOn[2] ? 1 : 0,
             pComb, fComb);
    csvDataBuffer += row;
  }
}
