import {elements, maxNumberValue, numberValue, setText} from "../dom.js";
import {UI_CONFIG} from "../config.js";

const els = elements([
  "mEspP1", "mEspP2", "mEspP3", "mSickPressure",
  "mOpenFlow", "mSickFlow", "mHeartbeat"
]);

export function renderMetrics(sample) {
  const digits = UI_CONFIG.metricPrecision;
  setText(els.mEspP1, numberValue(sample, "esp32_p1_bar", digits.esp32Pressure));
  setText(els.mEspP2, numberValue(sample, "esp32_p2_bar", digits.esp32Pressure));
  setText(els.mEspP3, numberValue(sample, "esp32_p3_bar", digits.esp32Pressure));
  setText(els.mSickPressure, maxNumberValue(sample, [
    "dxmr90_port1_pressure_bar",
    "dxmr90_port2_pressure_bar"
  ], digits.sickPressure));
  setText(els.mOpenFlow, numberValue(sample, "esp32_open_flow_gmin", digits.massFlow));
  setText(els.mSickFlow, numberValue(sample, "dxmr90_open_total_mass_flow_g_min", digits.massFlow));
  setText(els.mHeartbeat, numberValue(sample, "dxmr90_heartbeat", digits.heartbeat));
}
