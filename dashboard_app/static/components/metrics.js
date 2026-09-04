import {elements, maxNumberValue, numberValue, setText} from "../dom.js";
import {UI_CONFIG} from "../config.js";

const els = elements([
  "mEspP1", "mEspP2", "mEspP3", "mSickPressure",
  "mOpenFlow", "mAirPowderRatio", "mSickFlow", "mHeartbeat"
]);

// PANEL: Air:Powder Ratio — mass-flow calculation.
export function calculateAirPowderRatio(sample, powderFlowRateGPerS) {
  const airFlowGPerMin = Number(sample?.esp32_open_flow_gmin);
  const powderFlowGPerS = Number(powderFlowRateGPerS);
  if (
    !Number.isFinite(airFlowGPerMin) ||
    !Number.isFinite(powderFlowGPerS) ||
    powderFlowGPerS <= 0
  ) {
    return null;
  }
  return airFlowGPerMin / (powderFlowGPerS * 60);
}

export function renderMetrics(sample, powderFlowRateGPerS = null) {
  const digits = UI_CONFIG.metricPrecision;
  // PANEL: ESP32 Pressure
  setText(els.mEspP1, numberValue(sample, "esp32_p1_bar", digits.esp32Pressure));
  setText(els.mEspP2, numberValue(sample, "esp32_p2_bar", digits.esp32Pressure));
  setText(els.mEspP3, numberValue(sample, "esp32_p3_bar", digits.esp32Pressure));
  // PANEL: SICK Pressure (max)
  setText(els.mSickPressure, maxNumberValue(sample, [
    "dxmr90_port1_pressure_bar",
    "dxmr90_port2_pressure_bar"
  ], digits.sickPressure));
  // PANEL: Open-Line Air Flow
  setText(els.mOpenFlow, numberValue(sample, "esp32_open_flow_gmin", digits.massFlow));
  // PANEL: Air:Powder Ratio — display.
  const airPowderRatio = calculateAirPowderRatio(
    sample,
    powderFlowRateGPerS,
  );
  setText(
    els.mAirPowderRatio,
    airPowderRatio === null
      ? "--"
      : airPowderRatio.toFixed(digits.airPowderRatio),
  );
  // PANEL: SICK Flow · Solenoid 4
  setText(els.mSickFlow, numberValue(sample, "dxmr90_open_total_mass_flow_g_min", digits.massFlow));
  // PANEL: Heartbeat
  setText(els.mHeartbeat, numberValue(sample, "dxmr90_heartbeat", digits.heartbeat));
}
