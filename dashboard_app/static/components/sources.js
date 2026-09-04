// PANEL: Source details — ESP32 and DXMR90 diagnostics.
// Shared with the top connection indicators; motion diagnostics live in stepper.js.
import {ageText, elements, numberValue, setDot, setText} from "../dom.js";

const els = elements([
  "espDot", "espStatus", "dxDot", "dxStatus", "stepperDot", "stepperStatus",
  "historyStatus", "espRowDot", "espRowStatus", "dxRowDot", "dxRowStatus",
  "espMode", "espAge", "espPressureAdc", "espFlowAdc", "espPressure",
  "espFlow", "espTransport", "dxMode", "dxAge", "dxPort1", "dxPort2"
]);

function presentation(label, mode, connected) {
  if (mode === "off") return {label: `${label} off`, row: "Off", state: "warn"};
  if (connected === true) return {label: `${label} live`, row: "Live", state: "ok"};
  return {label: `${label} stale`, row: "Stale", state: "bad"};
}

export function renderSources(sample, historyLength) {
  const espConnected = sample.esp32_connected;
  const dxConnected = sample.dxmr90_connected;
  const stepperConnected = sample.stepper_connected;
  const espMode = sample.esp32_mode || "--";
  const dxMode = sample.dxmr90_mode || "--";
  const stepperMode = sample.stepper_mode || "--";
  const pressureAdcReady = sample.esp32_pressure_adc_ready === true;
  const flowAdcReady = sample.esp32_flow_adc_ready === true;
  const esp = presentation("ESP32", espMode, espConnected);
  const dx = presentation("DXMR90", dxMode, dxConnected);
  const controllerLabel = stepperMode === "controllino" ? "Controllino MAXI" : "Arduino Yun";
  const stepper = presentation(controllerLabel, stepperMode, stepperConnected);
  if (espConnected && (!pressureAdcReady || !flowAdcReady)) {
    esp.label = "ESP32 live / ADC unavailable";
    esp.row = "Live / ADC partial";
    esp.state = "warn";
  }

  setDot(els.espDot, esp.state);
  setDot(els.dxDot, dx.state);
  setDot(els.stepperDot, stepper.state);
  setDot(els.espRowDot, esp.state);
  setDot(els.dxRowDot, dx.state);
  setText(els.espStatus, esp.label);
  setText(els.dxStatus, dx.label);
  setText(els.stepperStatus, stepper.label);
  setText(els.espRowStatus, esp.row);
  setText(els.dxRowStatus, dx.row);
  setText(els.espMode, espMode);
  setText(els.dxMode, dxMode);
  setText(els.espAge, ageText(sample, "esp32_age_ms"));
  setText(els.dxAge, ageText(sample, "dxmr90_age_ms"));
  setText(els.espPressureAdc, pressureAdcReady ? "Ready" : "Unavailable");
  setText(els.espFlowAdc, flowAdcReady ? "Ready" : "Unavailable");
  setText(els.espPressure, `${numberValue(sample, "esp32_p_combined_bar", 3)} bar`);
  setText(els.espFlow, `${numberValue(sample, "esp32_f_combined_gmin", 2)} g/min`);
  setText(els.espTransport, espMode === "off"
    ? "Disabled"
    : sample.esp32_transport_error || (espConnected ? "Connected" : "Waiting for stream"));
  setText(els.dxPort1, `${numberValue(sample, "dxmr90_port1_mass_flow_g_min", 2)} g/min`);
  setText(els.dxPort2, `${numberValue(sample, "dxmr90_port2_mass_flow_g_min", 2)} g/min`);
  setText(els.historyStatus, `${historyLength} samples`);
}

export function setEspTransportMessage(message) {
  setText(els.espTransport, message);
}
