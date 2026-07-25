// Presentation-only settings are safe to tune in the field. Hardware and
// command limits come from /api/config and remain authoritative in Python.
export const UI_CONFIG = Object.freeze({
  historyLimit: 240,
  pollingIntervalMs: 100,
  moveConfirmation: Object.freeze({distanceMm: 50, speedMmS: 5}),
  stepperInputs: Object.freeze({
    minDistanceMm: 0.01,
    distanceStepMm: 0.01,
    defaultDistanceMm: 1.0,
    speedStepMmS: 0.1
  }),
  metricPrecision: Object.freeze({
    esp32Pressure: 3,
    sickPressure: 3,
    massFlow: 2,
    heartbeat: 0
  }),
  charts: Object.freeze({
    pressureChart: Object.freeze([
      {key: "esp32_p1_bar", color: "--chart-blue"},
      {key: "esp32_p2_bar", color: "--chart-green"},
      {key: "esp32_p3_bar", color: "--chart-amber"},
      {key: "dxmr90_port1_pressure_bar", color: "--chart-red"},
      {key: "dxmr90_port2_pressure_bar", color: "--chart-cyan"}
    ]),
    espFlowChart: Object.freeze([
      {key: "esp32_f1_gmin", color: "--chart-blue"},
      {key: "esp32_f2_gmin", color: "--chart-green"},
      {key: "esp32_f3_gmin", color: "--chart-amber"},
      {key: "esp32_open_flow_gmin", color: "--chart-violet"}
    ]),
    sickFlowChart: Object.freeze([
      {key: "dxmr90_port1_mass_flow_g_min", color: "--chart-red"},
      {key: "dxmr90_port2_mass_flow_g_min", color: "--chart-cyan"},
      {key: "dxmr90_open_total_mass_flow_g_min", color: "--chart-violet"}
    ])
  })
});
