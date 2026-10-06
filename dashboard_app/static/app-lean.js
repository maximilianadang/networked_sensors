// LEAN DASHBOARD — one module, ordered like the page (top to bottom, left to right).
// PAGE MAP: 1 Status | 2 Toolbar | 3 Measurements | 4 Charts | 5 Motor Control
//           6 Test Metadata | 7 Source details | 8 Shared helpers/API | 9 Startup
// Panel state lives inside factory functions; exports also support the original
// dashboard and isolated tests. Importing this module alone never starts the app.

// ============================================================================
// 1. STATUS PILLS — Dashboard connection (device status: section 7)
// ============================================================================
function setStreamStatus(label, state) {
  const streamEls = elements(["streamDot", "streamStatus"]);
  setText(streamEls.streamStatus, "Dashboard");
  streamEls.streamStatus.closest(".pill").setAttribute("aria-label", `Dashboard ${label}`);
  streamEls.streamStatus.closest(".pill").title = `Dashboard ${label}`;
  setDot(streamEls.streamDot, state);
}

// ============================================================================
// 2. TOOLBAR — Start, Stop, Export, Solenoid 1–4; recording/UTC status
// ============================================================================
// TOOLBAR: Start, Stop, Export, Solenoid 1–4 and recording/UTC indicators.
// E-STOP belongs to Motor Control in app-lean.js; shared appearance: dashboard-lean.css.

export function createToolbarComponent({
  getState,
  applySample,
  setRunState,
  setEspTransportMessage,
  solenoidCount
}) {
  const ids = [
    "runDot", "runStatus", "clockText", "startRun", "stopRun", "exportRun"
  ];
  for (let index = 0; index < solenoidCount; index += 1) ids.push(`sol${index}`);
  const els = elements(ids);
  const actions = createActions({
    refresh: () => renderRun(getState().run),
    onError: error => setEspTransportMessage(`Command failed: ${error.message}`)
  });

  function updateSolenoidControls() {
    const {latest, run} = getState();
    for (let index = 0; index < solenoidCount; index += 1) {
      const enabled = latest?.[`solenoid${index + 1}_connected`] === true;
      const owner = latest?.[`solenoid${index + 1}_source`] === "stepper" ? "Controllino" : "ESP32";
      const pending = actions.has(`solenoid-${index}`);
      els[`sol${index}`].disabled = !enabled || pending;
      els[`sol${index}`].title = pending
        ? `Sending Solenoid ${index + 1} command`
        : enabled
          ? `Toggle Solenoid ${index + 1} (keyboard ${index + 1})`
          : `${owner} control stream is not live`;
    }
  }

  function renderRun(run) {
    const recording = run.recording === true;
    const latestRecording = run.latest_recording || null;
    setText(els.runStatus, recording ? "Recording" : "Not recording");
    setDot(els.runDot, recording ? "ok" : "warn");
    els.startRun.disabled = recording || actions.has("recording");
    els.stopRun.disabled = !recording || actions.has("recording");
    els.exportRun.disabled = recording || !latestRecording;
    updateSolenoidControls();
  }

  function renderSample(sample) {
    const timestamp = sample.timestamp_iso || "";
    const date = new Date(timestamp);
    const compactTimestamp = Number.isNaN(date.getTime())
      ? (timestamp || "No timestamp")
      : `${date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "")} UTC`;
    setText(els.clockText, compactTimestamp);
    els.clockText.title = timestamp;
    for (let index = 0; index < solenoidCount; index += 1) {
      const on = sample[`solenoid${index + 1}_on`] === true;
      els[`sol${index}`].classList.toggle("on", on);
    }
    updateSolenoidControls();
  }

  async function toggleSolenoid(index) {
    const button = els[`sol${index}`];
    if (!button || button.disabled || actions.has(`solenoid-${index}`)) return false;
    setEspTransportMessage(`Sending Solenoid ${index + 1} command`);
    return actions.run(`solenoid-${index}`, () => postJson(API.solenoidToggle(index)), {
      success: payload => { if (payload.sample) applySample(payload.sample); }
    });
  }

  for (const [id, endpoint] of [["startRun", API.startRun], ["stopRun", API.stopRun]]) {
    els[id].addEventListener("click", () => {
      if (!els[id].disabled) void actions.run("recording", () => postJson(endpoint), {
        success: payload => setRunState(payload.run)
      });
    });
  }
  els.exportRun.addEventListener("click", downloadLatestExport);
  for (let index = 0; index < solenoidCount; index += 1) {
    els[`sol${index}`].addEventListener("click", () => void toggleSolenoid(index));
  }

  return {renderRun, renderSample, toggleSolenoid};
}

// ============================================================================
// 3. MEASUREMENTS — ESP32 Pressure, SICK Pressure, Open-Line Air Flow,
//    Air:Powder Ratio, SICK Flow · Solenoid 4, Heartbeat
// ============================================================================
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
  const els = elements([
    "mEspP1", "mEspP2", "mEspP3", "mSickPressure",
    "mOpenFlow", "mAirPowderRatio", "mSickFlow", "mHeartbeat"
  ]);
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

// ============================================================================
// 4. CHARTS — Pressure (bar), ESP32 Mass Flow (g/min), SICK Mass Flow (g/min)
//    Includes display configuration and named chart series.
// ============================================================================
// SHARED PANELS: Pressure (bar), ESP32 Mass Flow (g/min), SICK Mass Flow (g/min).
// Shared drawing engine and display configuration. Edit series below, headings
// and legend in index.html, and its appearance in dashboard.css.
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
    speedStepMmS: 0.001
  }),
  metricPrecision: Object.freeze({
    esp32Pressure: 3,
    sickPressure: 3,
    massFlow: 2,
    airPowderRatio: 2,
    heartbeat: 0
  }),
  charts: Object.freeze({
    // PANEL: Pressure (bar) — plotted series and colors.
    pressureChart: Object.freeze([
      {key: "esp32_p1_bar", color: "--chart-blue"},
      {key: "esp32_p2_bar", color: "--chart-green"},
      {key: "esp32_p3_bar", color: "--chart-amber"},
      {key: "dxmr90_port1_pressure_bar", color: "--chart-red"},
      {key: "dxmr90_port2_pressure_bar", color: "--chart-cyan"}
    ]),
    // PANEL: ESP32 Mass Flow (g/min) — plotted series and colors.
    espFlowChart: Object.freeze([
      {key: "esp32_f1_gmin", color: "--chart-blue"},
      {key: "esp32_f2_gmin", color: "--chart-green"},
      {key: "esp32_f3_gmin", color: "--chart-amber"},
      {key: "esp32_open_flow_gmin", color: "--chart-violet"}
    ]),
    // PANEL: SICK Mass Flow (g/min) — plotted series and colors.
    sickFlowChart: Object.freeze([
      {key: "dxmr90_port1_mass_flow_g_min", color: "--chart-red"},
      {key: "dxmr90_port2_mass_flow_g_min", color: "--chart-cyan"},
      {key: "dxmr90_open_total_mass_flow_g_min", color: "--chart-violet"}
    ])
  })
});

function themeColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartBounds(history, series) {
  const values = [];
  for (const sample of history) {
    for (const item of series) {
      const value = Number(sample[item.key]);
      if (Number.isFinite(value)) values.push(value);
    }
  }
  if (!values.length) return [0, 1];
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.12;
  return [min - pad, max + pad];
}

function drawChart(canvasId, series, history) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) throw new Error(`Dashboard chart #${canvasId} is missing`);
  const ctx = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(rect.width * ratio));
  const height = Math.max(220, Math.floor(rect.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = themeColor("--chart-bg");
  ctx.fillRect(0, 0, width, height);
  const padL = 68 * ratio;
  const padR = 18 * ratio;
  const padT = 18 * ratio;
  const padB = 34 * ratio;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  ctx.strokeStyle = themeColor("--chart-grid");
  ctx.lineWidth = 1 * ratio;
  ctx.fillStyle = themeColor("--chart-label");
  ctx.font = `${16 * ratio}px system-ui, sans-serif`;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";

  const [min, max] = chartBounds(history, series);
  for (let i = 0; i <= 4; i += 1) {
    const y = padT + (plotH * i / 4);
    const value = max - ((max - min) * i / 4);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(width - padR, y);
    ctx.stroke();
    ctx.fillText(value.toFixed(1), padL - 8 * ratio, y);
  }

  if (history.length < 2) return;
  const xFor = index => padL + plotW * (index / Math.max(1, history.length - 1));
  const yFor = value => padT + plotH * (1 - (value - min) / (max - min));

  for (const item of series) {
    ctx.strokeStyle = themeColor(item.color);
    ctx.lineWidth = 2.2 * ratio;
    ctx.beginPath();
    let drawing = false;
    history.forEach((sample, index) => {
      const value = Number(sample[item.key]);
      if (!Number.isFinite(value)) {
        drawing = false;
        return;
      }
      const x = xFor(index);
      const y = yFor(value);
      if (!drawing) {
        ctx.moveTo(x, y);
        drawing = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  }
}

export function drawAllCharts(history) {
  for (const [canvasId, series] of Object.entries(UI_CONFIG.charts)) {
    drawChart(canvasId, series, history);
  }
}

// ============================================================================
// 5. MOTOR CONTROL — DRO/piston (left), stepper and servo controls (right)
// ============================================================================
export function createStepperComponent({getLatest, applySample, limits}) {
  const els = elements([
    "emergencyStop", "emergencyReset", "emergencyState", "stepperForm",
    "stepperDistanceField", "stepperDistance", "stepperSpeed", "stepperSpeedLabel",
    "stepperModeLocal", "stepperModeWeb", "stepperCommandField",
    "stepperSoftwareDirection", "stepperDirectionForward", "stepperDirectionReverse",
    "stepperCommandInput", "stepperMove", "stepperHome", "stepperStop",
    "stepperApplySpeed", "stepperMessage", "stepperCommandFeedback",
    "stepperSoftwareRun", "stepperRunReverse", "stepperRunStop", "stepperRunForward",
    "stepperState", "stepperConfiguredSpeed",
    "stepperEffectiveSpeed", "stepperMeasuredSpeed", "stepperPulseEngine",
    "stepperCommand", "stepperOwner", "stepperModeStatus", "stepperLocal",
    "stepperPistonVisual", "stepperDroPosition", "stepperSetDroZero",
    "stepperMoveToDroZero", "stepperDroZeroStatus",
    "stepperDroMinLabel", "stepperDroMaxLabel",
    "stepperDirectionIndicator", "stepperDirectionArrow",
    "stepperDirectionLabel", "stepperDirectionBlocked",
    "stepperDirectionBlockLabel",
    "stepperDroRawPosition", "stepperDroZeroDiagnostic", "stepperDroFrames",
    "stepperD5Label", "stepperManualDirection", "stepperDirectionStatus",
    "stepperDriverOutput", "stepperPositiveLimit", "stepperNegativeLimit",
    "stepperLimitFilter", "stepperBlocked", "stepperInterlocks", "stepperSequence", "stepperTransport",
    "brushlessMotorState", "brushlessMotorDetail", "brushlessMotorToggle",
    "brushlessMotorAction", "brushlessPulseWidth", "brushlessApplyPulse"
  ]);
  // Keep a pre-restart page functional while its cached HTML lacks this field.
  const stepperPrimaryPulseOutput =
    document.getElementById("stepperPrimaryPulseOutput");
  const stepperDroVelocity = document.getElementById("stepperDroVelocity");

  const servo = createServoComponent({getLatest, applySample, postJson});

  let controlModeDirty = false;
  let messageSticky = false;
  let brushlessPulseDirty = false;
  let lastFreshDroPositionMm = null;
  const actions = createActions({refresh: updateControls});

  function setCommandFeedback(message = "") {
    setText(els.stepperCommandFeedback, message);
    els.stepperCommandFeedback.hidden = message === "";
  }

  // The saved display zero is the physical D8/negative limit at the top.
  // Preserve raw-minus-zero sign: positions below that top zero are negative.
  const droVisualMinMm = -(limits.max_travel_mm ?? limits.max_distance_mm);
  const droVisualMaxMm = 0;
  const droVisualSpanMm = droVisualMaxMm - droVisualMinMm;
  const droVisualEndpointToleranceMm = 0.1;

  els.stepperDistance.min = String(limits.min_distance_mm);
  els.stepperDistance.max = String(limits.max_distance_mm);
  els.stepperDistance.step = String(UI_CONFIG.stepperInputs.distanceStepMm);
  els.stepperDistance.value = String(UI_CONFIG.stepperInputs.defaultDistanceMm);
  els.stepperSpeed.min = String(limits.min_speed_mm_s);
  els.stepperSpeed.max = String(limits.max_speed_mm_s);
  els.stepperSpeed.step = String(UI_CONFIG.stepperInputs.speedStepMmS);
  els.stepperSpeed.value = String(limits.default_speed_mm_s);
  els.brushlessPulseWidth.min = "1000";
  els.brushlessPulseWidth.max = "2000";
  els.brushlessPulseWidth.step = "1";
  setText(els.stepperDroMinLabel, `Bottom ${droVisualMinMm.toFixed(2)} mm`);
  setText(els.stepperDroMaxLabel, "Top 0 mm");
  const modeInputs = [els.stepperModeLocal, els.stepperModeWeb];

  function formatSetpoint(value, digits) {
    return value.toFixed(digits).replace(/\.?0+$/, "");
  }

  function applyMotionPlan({speedMmS, distanceMm}) {
    if (Number.isFinite(speedMmS)) {
      els.stepperSpeed.value = formatSetpoint(speedMmS, 3);
    }
    if (Number.isFinite(distanceMm)) {
      els.stepperDistance.value = formatSetpoint(distanceMm, 2);
    }
    setCommandFeedback();
    updateControls();
  }

  function updateControls() {
    const latest = getLatest();
    const distance = Number(els.stepperDistance.value);
    const speed = Number(els.stepperSpeed.value);
    const connected = latest && latest.stepper_connected === true;
    const controllino = latest && latest.stepper_mode === "controllino";
    const enabled = latest && latest.stepper_local_enabled === true;
    const d4Off = latest && latest.stepper_d4_raw === "HIGH";
    const commandCapable = latest && latest.stepper_command_capable === true;
    const speedCommandCapable = latest && latest.stepper_speed_command_capable === true;
    const directionCalibrationSafe = latest && latest.stepper_direction_calibration_safe === true;
    const modeCommandCapable = latest && latest.stepper_mode_command_capable === true;
    const homeCapable = latest && latest.stepper_home_capable === true;
    const estopCapable = latest && latest.stepper_estop_capable === true;
    const estopLatched = latest && latest.stepper_estop_latched === true;
    const webPositionMode = latest && latest.stepper_control_mode === "web_position";
    const authorizedDirection = latest && latest.stepper_authorized_direction;
    // The operator enters a positive magnitude. Physical D5 supplies direction;
    // "both" is the simulator's forward default because it has no D5 input.
    const selectedDirection = controllino
      ? (els.stepperDirectionReverse.checked ? "reverse" : "forward")
      : authorizedDirection === "both" ? "forward" : authorizedDirection;
    const signedDistance = selectedDirection === "reverse"
      ? -distance
      : selectedDirection === "forward" ? distance : Number.NaN;
    const moving = latest && latest.stepper_moving === true;
    const positiveBlocked = latest && signedDistance > 0 &&
      (latest.stepper_positive_limit_active || latest.stepper_positive_limit_latched);
    const negativeBlocked = latest && signedDistance < 0 &&
      (latest.stepper_negative_limit_active || latest.stepper_negative_limit_latched);
    const directionSelected = selectedDirection === "forward" || selectedDirection === "reverse";
    const valid = Number.isFinite(distance) &&
      distance >= limits.min_distance_mm && distance <= limits.max_distance_mm &&
      Number.isFinite(speed) &&
      speed >= limits.min_speed_mm_s && speed <= limits.max_speed_mm_s;
    const simulated = latest && latest.stepper_mode === "sim";
    const rawDroPositionMm = Number(latest?.stepper_dro_position_mm);
    const canSetDroZero = connected &&
      latest?.stepper_dro_capable === true &&
      latest?.stepper_dro_fresh === true &&
      Number.isFinite(rawDroPositionMm) &&
      !moving;
    const brushlessCapable =
      latest && latest.stepper_brushless_motor_capable === true;
    const brushlessVariableCapable =
      latest && latest.stepper_brushless_motor_variable_capable === true;
    const brushlessOn =
      latest && latest.stepper_brushless_motor_on === true;
    const brushlessPulseUs = Number(els.brushlessPulseWidth.value);
    const validBrushlessPulse = Number.isInteger(brushlessPulseUs) &&
      brushlessPulseUs >= 1000 && brushlessPulseUs <= 2000;

    els.brushlessMotorToggle.disabled = actions.has("brushless") ||
      actions.has("pulse") ||
      !connected || !brushlessCapable || estopLatched;
    els.brushlessMotorToggle.title = actions.has("brushless")
      ? "Waiting for the controller to confirm the brushless motor state"
      : !connected
        ? "Controller status is not connected"
        : !brushlessCapable
          ? "The connected firmware does not support brushless motor control"
          : estopLatched
            ? "Reset E-STOP before starting the brushless motor"
            : `${brushlessOn ? "Turn off" : "Turn on"} the brushless motor (M)`;
    els.brushlessPulseWidth.disabled = actions.has("pulse") ||
      !connected || !brushlessVariableCapable;
    els.brushlessApplyPulse.disabled = actions.has("pulse") ||
      !connected || !brushlessVariableCapable || !validBrushlessPulse;
    els.brushlessApplyPulse.title = actions.has("pulse")
      ? "Waiting for the controller to confirm the pulse width"
      : !connected
        ? "Controller status is not connected"
        : !brushlessVariableCapable
          ? "Upload variable-pulse firmware before changing the pulse width"
          : !validBrushlessPulse
            ? "Enter an integer pulse width from 1000 through 2000 µs"
            : brushlessOn
              ? `Apply ${brushlessPulseUs} µs immediately while the motor is ON`
              : `Use ${brushlessPulseUs} µs the next time the motor is turned ON`;

    els.stepperSetDroZero.disabled = actions.has("zero") || !canSetDroZero;
    els.stepperSetDroZero.title = actions.has("zero")
      ? "Saving the system zero"
      : moving
        ? "Stop motion before setting zero"
        : !canSetDroZero
          ? "A fresh connected DRO sample is required"
          : `Use the current raw reading ${rawDroPositionMm.toFixed(2)} mm as the saved system zero`;
    // T4H.2 deliberately remains inert until a local closed-loop controller
    // and its physical fault tests exist.
    els.stepperMoveToDroZero.disabled = true;
    els.stepperMoveToDroZero.title =
      "Deferred: requires the T4H.2 closed-loop controller and physical stepper tests";

    els.emergencyStop.disabled = !connected || !estopCapable || estopLatched;
    els.emergencyReset.disabled = !connected || !estopCapable || !estopLatched ||
      moving || (!simulated && !d4Off);
    els.emergencyReset.title = !estopLatched
      ? "E-STOP is not latched"
      : !simulated && !d4Off
        ? "Turn physical D4 OFF before resetting"
        : "Reset the software latch; this does not start motion";

    els.stepperDistanceField.hidden = !webPositionMode;
    els.stepperSoftwareDirection.hidden = !webPositionMode || !controllino;
    els.stepperCommandField.hidden = !webPositionMode;
    els.stepperMove.hidden = !webPositionMode;
    els.stepperHome.hidden = !webPositionMode;
    els.stepperStop.hidden = !webPositionMode;
    els.stepperApplySpeed.hidden = webPositionMode;
    // D4/D5/D6/D8 do not exist on this Controllino installation; keeping an
    // empty interlock block visible only wastes the height needed by controls.
    els.stepperInterlocks.hidden = controllino;
    els.stepperSoftwareRun.hidden = webPositionMode || !controllino;
    els.stepperRunReverse.disabled = !connected || estopLatched || moving || actions.has("local-run");
    els.stepperRunForward.disabled = els.stepperRunReverse.disabled;
    els.stepperRunStop.disabled = !connected || (!moving && !actions.has("local-run"));
    els.stepperDistance.disabled = !webPositionMode;
    els.stepperDistance.title = webPositionMode
      ? controllino
        ? "Positive travel magnitude; choose Forward or Reverse above"
        : "Positive travel magnitude; physical D5 selects Forward or Reverse"
      : "Available only in Positional mode";
    els.stepperCommandInput.disabled = !webPositionMode;
    els.stepperCommandInput.title = webPositionMode
      ? "Optional identifier for this position command"
      : "Available only in Positional mode";
    setText(els.stepperSpeedLabel, "Speed (mm/s)");
    els.stepperMove.disabled = (actions.has("move") || actions.has("stop")) || !commandCapable ||
      !directionCalibrationSafe || !connected || !webPositionMode || estopLatched ||
      !enabled || moving || !valid || !directionSelected || positiveBlocked || negativeBlocked;
    // A confirmed moving state exposes Stop even if Move has not returned yet.
    els.stepperStop.disabled = actions.has("stop") || estopLatched ||
      !commandCapable || !webPositionMode || !moving;
    els.stepperHome.disabled = (actions.has("move") || actions.has("stop") || actions.has("home")) || !homeCapable ||
      !directionCalibrationSafe || !connected || !webPositionMode || estopLatched ||
      !enabled || moving || (authorizedDirection !== "reverse" && authorizedDirection !== "both");
    const modeDisabled = (actions.has("move") || actions.has("stop")) ||
      actions.has("mode") || !directionCalibrationSafe || !modeCommandCapable ||
      estopLatched || !connected || !d4Off || moving;
    for (const input of modeInputs) input.disabled = modeDisabled;
    els.stepperMove.title = (actions.has("move") || actions.has("stop"))
      ? "Waiting for the current motion command"
      : "Start the Positional move (Space while the page has focus)";
    els.stepperStop.title = actions.has("stop")
      ? "Waiting for Stop confirmation"
      : "Stop Positional motion (Space while the page has focus)";

    if (actions.has("mode")) {
      for (const input of modeInputs) input.title = "Waiting for the controller to confirm the control mode";
    } else if (!connected) {
      for (const input of modeInputs) input.title = "Yún USB status is not connected";
    } else if (!modeCommandCapable) {
      for (const input of modeInputs) input.title = "The connected firmware does not support control modes";
    } else if (!d4Off || moving) {
      for (const input of modeInputs) input.title = "Turn D4 OFF and stop motion before changing mode";
    } else {
      for (const input of modeInputs) input.title = "Select a control mode to apply it immediately";
    }

    // Keep Apply clickable when possible so a rejected request explains why.
    els.stepperApplySpeed.disabled = actions.has("speed") || webPositionMode || estopLatched;
    if (actions.has("speed")) {
      els.stepperApplySpeed.title = "Waiting for the controller to confirm the new speed";
    } else if (webPositionMode) {
      els.stepperApplySpeed.title = "Move uses the speed field directly in Positional mode";
    } else if (!Number.isFinite(speed) ||
      speed < limits.min_speed_mm_s || speed > limits.max_speed_mm_s) {
      els.stepperApplySpeed.title = `Enter a speed from ${limits.min_speed_mm_s} through ${limits.max_speed_mm_s} mm/s`;
    } else if (!connected) {
      els.stepperApplySpeed.title = "Yún USB status is not connected";
    } else if (!speedCommandCapable) {
      els.stepperApplySpeed.title = "The connected firmware does not support speed tuning";
    } else if (!d4Off || moving) {
      els.stepperApplySpeed.title = "Turn D4 OFF before applying a speed";
    } else {
      els.stepperApplySpeed.title = `Apply ${speed.toFixed(1)} mm/s and wait for Yún confirmation`;
    }
  }

  function renderPiston(latest, droCapable, droHasSample) {
    const rawPositionMm = Number(latest.stepper_dro_position_mm);
    const positionMm = Number(latest.stepper_dro_zeroed_position_mm);
    const zeroSet = latest.stepper_dro_zero_set === true;
    const hasPosition = zeroSet && droHasSample && Number.isFinite(positionMm);
    const fresh = hasPosition && latest.stepper_dro_fresh === true;
    const outOfRange = hasPosition &&
      (
        positionMm < droVisualMinMm - droVisualEndpointToleranceMm ||
        positionMm > droVisualMaxMm + droVisualEndpointToleranceMm
      );
    const hasTrustworthyVisualPosition = fresh || lastFreshDroPositionMm !== null;

    els.stepperPistonVisual.classList.toggle(
      "is-unavailable",
      !hasPosition || !hasTrustworthyVisualPosition,
    );
    els.stepperPistonVisual.classList.toggle("is-stale", hasPosition && !fresh);
    els.stepperPistonVisual.classList.toggle("is-out-of-range", outOfRange);

    if (!hasPosition) {
      lastFreshDroPositionMm = null;
      els.stepperPistonVisual.setAttribute(
        "aria-label",
        zeroSet
          ? "DRO piston position unavailable."
          : `DRO position ${
            Number.isFinite(rawPositionMm) ? rawPositionMm.toFixed(2) : "unavailable"
          } millimeters. Piston position unavailable.`,
      );
      return;
    }

    // Never move the graphic from a stale sample. The last trustworthy
    // position remains visible while the stale treatment signals the freeze.
    if (fresh) {
      const clampedMm = Math.min(droVisualMaxMm, Math.max(droVisualMinMm, positionMm));
      const ratio = droVisualSpanMm > 0
        ? (clampedMm - droVisualMinMm) / droVisualSpanMm
        : 0;
      const positionPercent = 96 - ratio * 92;
      els.stepperPistonVisual.style.setProperty(
        "--piston-position",
        `${positionPercent.toFixed(3)}%`,
      );
      lastFreshDroPositionMm = positionMm;
    }

    els.stepperPistonVisual.setAttribute(
      "aria-label",
      `DRO piston position ${positionMm.toFixed(2)} millimeters relative to the D8 top zero; positive is upward and negative is downward${
        fresh ? "" : "; sensor data stale and graphic frozen"
      }${outOfRange ? "; outside the displayed stroke" : ""}.`,
    );
  }

  function render(latest) {
    servo.render(latest);
    const moving = latest.stepper_moving === true;
    const estopCapable = latest.stepper_estop_capable === true;
    const estopLatched = latest.stepper_estop_latched === true;
    const localEnabled = latest.stepper_local_enabled === true;
    const commandCapable = latest.stepper_command_capable === true;
    const connected = latest.stepper_connected === true;
    const directionCalibrationSafe =
      latest.stepper_direction_calibration_safe === true;
    const controlMode = latest.stepper_control_mode || "unknown";
    const webPositionMode = controlMode === "web_position";
    const brushlessCapable =
      latest.stepper_brushless_motor_capable === true;
    const brushlessOn = latest.stepper_brushless_motor_on === true;
    const brushlessPulseUs = Number(latest.stepper_brushless_motor_pulse_us);
    const brushlessSetpointUs = Number(
      latest.stepper_brushless_motor_setpoint_us,
    );
    document.body.classList.toggle("estop-latched", estopLatched);
    setText(els.emergencyState, estopLatched
      ? "LATCHED — step pulses inhibited"
      : !connected
        ? "Unavailable — stepper offline"
        : estopCapable
          ? "Ready"
          : "Unavailable — firmware update required");
    setText(els.stepperState, latest.stepper_state || "Unknown");
    setText(
      els.brushlessMotorState,
      !connected
        ? "Offline"
        : brushlessCapable
          ? brushlessOn ? "ON" : "OFF"
          : "Unavailable",
    );
    els.brushlessMotorState.classList.toggle(
      "on",
      connected && brushlessCapable && brushlessOn,
    );
    setText(
      els.brushlessMotorDetail,
      brushlessCapable && Number.isFinite(brushlessPulseUs)
        ? `D12 Pulse Timer: ${brushlessPulseUs.toFixed(0)} µs`
        : "D12 Pulse Timer: unavailable",
    );
    setText(els.brushlessMotorAction, brushlessOn ? "Turn off" : "Turn on");
    if (
      Number.isInteger(brushlessSetpointUs) &&
      brushlessSetpointUs >= 1000 &&
      brushlessSetpointUs <= 2000
    ) {
      if (!brushlessPulseDirty) {
        els.brushlessPulseWidth.value = String(brushlessSetpointUs);
      } else if (Number(els.brushlessPulseWidth.value) === brushlessSetpointUs) {
        brushlessPulseDirty = false;
      }
    }
    setText(els.stepperOwner, `${latest.stepper_mode || "--"} / ${latest.stepper_control_owner || "--"}`);
    setText(els.stepperModeStatus, webPositionMode
      ? "Positional"
      : controlMode === "local_velocity" ? "Directional" : "--");
    setText(els.stepperConfiguredSpeed, `${numberValue(latest, "stepper_command_speed_mm_s", 3)} mm/s`);
    setText(els.stepperEffectiveSpeed, `${numberValue(latest, "stepper_speed_mm_s", 3)} mm/s`);
    const pulseMeasurementCapable = latest.stepper_pulse_measurement_capable === true;
    const measuredPulseRate = Number(latest.stepper_measured_pulse_rate_sps);
    const measuredPulseSpeed = Number(latest.stepper_measured_speed_mm_s);
    const hasMeasuredPulseOutput = pulseMeasurementCapable &&
      Number.isFinite(measuredPulseRate) &&
      Number.isFinite(measuredPulseSpeed);
    const measuredPulseOutput = hasMeasuredPulseOutput
      ? `${measuredPulseRate.toFixed(0)} pulses/s · ${measuredPulseSpeed.toFixed(3)} mm/s`
      : "Unavailable — firmware update required";
    setText(els.stepperMeasuredSpeed, measuredPulseOutput);
    if (stepperPrimaryPulseOutput) {
      setText(
        stepperPrimaryPulseOutput,
        hasMeasuredPulseOutput ? `${measuredPulseRate.toFixed(0)} pulses/s` : "--",
      );
    }
    setText(els.stepperPulseEngine, latest.stepper_unified_timer_capable === true
      ? "Unified Timer1 (Local / Web / Home)"
      : "Legacy split scheduler — firmware update required");
    setText(els.stepperCommand, latest.stepper_command_id || "--");
    const droCapable = latest.stepper_dro_capable === true;
    const droHasSample = droCapable &&
      Number.isFinite(Number(latest.stepper_dro_valid_frame_count)) &&
      Number(latest.stepper_dro_valid_frame_count) > 0;
    const droZeroSet = latest.stepper_dro_zero_set === true;
    const droZeroedPosition = Number(latest.stepper_dro_zeroed_position_mm);
    const droVelocityValue = latest.stepper_dro_velocity_mm_s;
    const droVelocityMmS = Number(droVelocityValue);
    const droVelocityWindowMs = Number(latest.stepper_dro_velocity_window_ms);
    const hasDroVelocity = connected &&
      latest.stepper_dro_fresh === true &&
      droVelocityValue !== null &&
      droVelocityValue !== undefined &&
      Number.isFinite(droVelocityMmS);
    setText(els.stepperDroPosition, droHasSample
      ? droZeroSet && Number.isFinite(droZeroedPosition)
        ? `${droZeroedPosition.toFixed(2)} mm`
        : `${numberValue(latest, "stepper_dro_position_mm", 2)} mm`
      : "--");
    if (stepperDroVelocity) {
      setText(
        stepperDroVelocity,
        hasDroVelocity
          ? `${droVelocityMmS > 0 ? "+" : ""}${droVelocityMmS.toFixed(2)} mm/s`
          : "--",
      );
      stepperDroVelocity.title = hasDroVelocity
        ? `Signed slope of fresh DRO positions (positive upward, negative downward) over ${
            Number.isFinite(droVelocityWindowMs)
              ? droVelocityWindowMs.toFixed(0)
              : "--"
          } ms`
        : "Waiting for enough fresh DRO position samples";
    }
    setText(els.stepperDroRawPosition, droHasSample
      ? `${numberValue(latest, "stepper_dro_position_mm", 2)} mm`
      : "--");
    const zeroRawMm = Number(latest.stepper_dro_zero_raw_mm);
    const hasFiniteZero = droZeroSet && Number.isFinite(zeroRawMm);
    const zeroText = hasFiniteZero
      ? `Raw ${zeroRawMm.toFixed(2)} mm · saved system reference`
      : "Not set";
    setText(els.stepperDroZeroStatus, hasFiniteZero
      ? `Zero: ${zeroRawMm.toFixed(2)} mm raw · saved`
      : "System zero not set");
    setText(els.stepperDroZeroDiagnostic, zeroText);
    renderPiston(latest, droCapable, droHasSample);
    setText(els.stepperDroFrames, droCapable
      ? `valid=${latest.stepper_dro_valid_frame_count ?? "--"}, rejected=${latest.stepper_dro_rejected_frame_count ?? "--"}, dropped=${latest.stepper_dro_dropped_frame_count ?? "--"}`
      : "--");
    const localState = localEnabled
      ? (webPositionMode ? "Armed" : "Running")
      : (webPositionMode ? "Disarmed" : "Stopped");
    const d4Raw = latest.stepper_d4_raw || "--";
    setText(els.stepperLocal, `${localState} · ${d4Raw}`);
    els.stepperLocal.title = `${localState} / ${d4Raw}`;
    setText(els.stepperD5Label, "Direction (D5)");
    const rawDirection = latest.stepper_manual_direction || "--";
    const manualDirection = latest.stepper_mode === "controllino"
      ? ({forward: "reverse", reverse: "forward"}[rawDirection] || rawDirection)
      : rawDirection;
    const compactDirection = {
      forward: "FWD",
      reverse: "REV",
      not_applicable: "N/A",
    }[manualDirection] || manualDirection;
    const d5Raw = latest.stepper_d5_raw || "--";
    setText(els.stepperManualDirection, `${compactDirection} · ${d5Raw}`);
    const d5Qualified = latest.stepper_d5_qualified || d5Raw;
    els.stepperManualDirection.title =
      `${manualDirection} / raw ${d5Raw} / qualified ${d5Qualified}`;
    const hasD5Direction = connected &&
      ["HIGH", "LOW"].includes(d5Raw) &&
      ["forward", "reverse"].includes(manualDirection);
    const arrowDirection = hasD5Direction && manualDirection === "forward"
      ? "down"
      : hasD5Direction && manualDirection === "reverse" ? "up" : "unavailable";
    const blockedLimit = arrowDirection === "down" &&
      latest.stepper_positive_limit_active === true
      ? "D6 BOTTOM LIMIT"
      : arrowDirection === "up" &&
          latest.stepper_negative_limit_active === true
        ? "D8 TOP LIMIT"
        : null;
    els.stepperDirectionIndicator.classList.toggle(
      "is-down",
      arrowDirection === "down",
    );
    els.stepperDirectionIndicator.classList.toggle(
      "is-up",
      arrowDirection === "up",
    );
    els.stepperDirectionIndicator.classList.toggle(
      "is-unavailable",
      arrowDirection === "unavailable",
    );
    els.stepperDirectionIndicator.classList.toggle(
      "is-blocked",
      blockedLimit !== null,
    );
    setText(
      els.stepperDirectionArrow,
      arrowDirection === "down" ? "↓" : arrowDirection === "up" ? "↑" : "",
    );
    setText(
      els.stepperDirectionLabel,
      arrowDirection === "down"
        ? "D5 FWD · D6 BOTTOM"
        : arrowDirection === "up"
          ? "D5 REV · D8 TOP"
          : connected ? "D5 DIRECTION UNAVAILABLE" : "YÚN DISCONNECTED",
    );
    setText(
      els.stepperDirectionBlockLabel,
      blockedLimit
        ? `${blockedLimit} BLOCKS ${arrowDirection === "down" ? "↓" : "↑"}`
        : "",
    );
    if (latest.stepper_control_mode) {
      if (!controlModeDirty) {
        els.stepperModeWeb.checked = webPositionMode;
        els.stepperModeLocal.checked = !webPositionMode;
      } else if (els.stepperModeWeb.checked === webPositionMode) {
        controlModeDirty = false;
      }
    }
    setText(els.stepperDirectionStatus, directionCalibrationSafe
      ? "Normal (Forward → D6 bottom; Reverse → D8 top)"
      : latest.stepper_direction_mapping === "inverted"
        ? "UNSAFE LEGACY INVERSION — upload required"
        : "Unavailable — firmware update required");
    const driverDetail = latest.stepper_driver_enable_capable === true
      ? latest.stepper_driver_enabled ? "ENERGIZED / D9 HIGH" : "DISABLED / D9 LOW"
      : "Unavailable — firmware update required";
    setText(els.stepperDriverOutput, latest.stepper_driver_enable_capable === true
      ? latest.stepper_driver_enabled ? "Energized · HIGH" : "Disabled · LOW"
      : "Unavailable");
    els.stepperDriverOutput.title = driverDetail;
    const positiveLatch = latest.stepper_positive_limit_latched ? " / LATCHED" : "";
    const negativeLatch = latest.stepper_negative_limit_latched ? " / LATCHED" : "";
    const positiveState = latest.stepper_positive_limit_active ? "ACTIVE" : "Clear";
    const negativeState = latest.stepper_negative_limit_active ? "ACTIVE" : "Clear";
    const d6Raw = latest.stepper_d6_raw || "--";
    const d8Raw = latest.stepper_d8_raw || "--";
    setText(els.stepperPositiveLimit, `${positiveState} · ${d6Raw}${positiveLatch ? " · LATCHED" : ""}`);
    setText(els.stepperNegativeLimit, `${negativeState} · ${d8Raw}${negativeLatch ? " · LATCHED" : ""}`);
    els.stepperPositiveLimit.title = `${positiveState} / raw ${latest.stepper_d6_raw || "--"}${positiveLatch}`;
    els.stepperNegativeLimit.title = `${negativeState} / raw ${latest.stepper_d8_raw || "--"}${negativeLatch}`;
    const directionFilterDetail =
      latest.stepper_direction_filter_capable === true
        ? `D5 ${numberValue(latest, "stepper_direction_qualification_ms", 0)} ms, rejected=${latest.stepper_direction_glitch_count ?? "--"}`
        : "D5 unqualified";
    const limitFilterDetail = latest.stepper_limit_filter_capable === true
      ? `D6/D8 ${numberValue(latest, "stepper_limit_qualification_ms", 0)} ms, rejected=${latest.stepper_positive_limit_glitch_count ?? "--"}/${latest.stepper_negative_limit_glitch_count ?? "--"}`
      : "D6/D8 unqualified";
    setText(
      els.stepperLimitFilter,
      `${directionFilterDetail}; ${limitFilterDetail}`,
    );
    const decisionReason = latest.stepper_blocked_reason || "none";
    const decisionText = estopLatched
      ? "E-STOP LATCHED: motion inhibited"
      : latest.stepper_blocked
        ? `BLOCKED: ${decisionReason}`
        : decisionReason === "run_off"
          ? "Stopped: run_off"
          : decisionReason === "boot_disarmed"
            ? "Stopped: cycle D4 OFF after reset"
            : decisionReason === "driver_wakeup"
              ? "Waiting: 200 ms driver wake-up"
              : ["d4_abort", "direction_auth", "operator_stop"].includes(decisionReason)
                ? `ABORTED: ${decisionReason}`
                : `Allowed: ${decisionReason}`;
    const compactDecision = estopLatched
      ? "E-STOP LATCHED"
      : latest.stepper_blocked
        ? `BLOCKED · ${decisionReason.replaceAll("_", " ")}`
        : decisionReason === "none"
          ? "Allowed"
          : decisionReason === "driver_wakeup"
            ? "Waiting · wake-up"
            : `${decisionText.split(":")[0]} · ${decisionReason.replaceAll("_", " ")}`;
    setText(els.stepperBlocked, compactDecision);
    els.stepperBlocked.title = decisionText;
    setText(els.stepperSequence, latest.stepper_status_sequence ?? "--");
    setText(els.stepperTransport, latest.stepper_transport_error ||
      (connected ? "Connected" : "Waiting for status"));
    if (!messageSticky && ["usb", "network", "controllino"].includes(latest.stepper_mode)) {
      const transportLabel = latest.stepper_mode === "usb" ? "USB" : "LAN";
      const controllino = latest.stepper_mode === "controllino";
      setText(els.stepperMessage, !connected
        ? "Motion controller disconnected"
        : !directionCalibrationSafe
          ? `${transportLabel} unsafe legacy direction mapping; upload fixed-direction firmware before motion`
          : commandCapable
            ? webPositionMode
              ? controllino
                ? "Positional ready; signed commands select direction (no limit switches connected)"
                : "Positional ready; D4 arms, D5 selects direction, and D6/D8 stop travel"
              : controllino
                ? "Directional uses software run/direction commands"
                : "Directional: D4 runs/stops and D5 selects direction"
            : latest.stepper_speed_command_capable
              ? `${transportLabel} speed tuning ready; upload position-capable firmware for Home and Move`
              : `${transportLabel} diagnostics only; upload T4B firmware for speed tuning`);
    }
    updateControls();
  }


  // PANEL: Motor Control — one command lifecycle, explicit per-action validation.
  function command(key, endpoint, body = {}, {
    success = () => {}, failure, finish, target = els.stepperMessage
  } = {}) {
    messageSticky = true;
    return actions.run(key, () => postJson(endpoint, body), {
      success: payload => {
        if (payload.sample) applySample(payload.sample);
        success(payload);
      },
      failure: error => {
        const message = `${key} failed: ${error.message}`;
        setText(target, message);
        setCommandFeedback(message);
        failure?.(error);
      },
      finish
    });
  }

  function requireConfirmation(condition, description) {
    if (!condition) throw new Error(`Controller did not confirm ${description}`);
  }

  function requestMove() {
    if (els.stepperMove.hidden || els.stepperMove.disabled ||
        actions.has("move") || actions.has("stop")) return false;
    const latest = getLatest();
    const distance = Number(els.stepperDistance.value);
    const speed = Number(els.stepperSpeed.value);
    const direction = latest?.stepper_mode === "controllino"
      ? (els.stepperDirectionReverse.checked ? "reverse" : "forward")
      : latest?.stepper_authorized_direction === "both"
        ? "forward" : latest?.stepper_authorized_direction;
    if (!["forward", "reverse"].includes(direction)) {
      setCommandFeedback("Move rejected: direction is unavailable.");
      return false;
    }
    const confirmAt = UI_CONFIG.moveConfirmation;
    if ((Math.abs(distance) > confirmAt.distanceMm || speed > confirmAt.speedMmS) &&
        !window.confirm(`Confirm ${direction} move: ${distance} mm at ${speed} mm/s?`)) return false;
    const body = {distance_mm: distance, speed_mm_s: speed};
    if (latest?.stepper_mode === "controllino") body.direction = direction;
    const commandId = els.stepperCommandInput.value.trim();
    if (commandId) body.command_id = commandId;
    setCommandFeedback();
    return command("move", API.stepperMove, body, {
      success: p => setText(els.stepperMessage, `${p.resolved_direction || direction} move accepted`)
    });
  }

  function requestStop() {
    if (els.stepperStop.hidden || els.stepperStop.disabled) return false;
    return command("stop", API.stepperStop, {}, {
      success: () => setText(els.stepperMessage, "Motion stopped")
    });
  }

  function handleSpaceShortcut() {
    const latest = getLatest();
    if (latest?.stepper_control_mode !== "web_position") return false;
    const moving = latest.stepper_moving === true;
    const button = moving ? els.stepperStop : els.stepperMove;
    if (button.hidden || button.disabled ||
        actions.has("stop") || (!moving && actions.has("move"))) return false;
    void (moving ? requestStop() : requestMove());
    return true;
  }

  // SUBPANEL: Brushless motor — confirmed state and pulse-width setpoint.
  function requestBrushlessToggle() {
    if (els.brushlessMotorToggle.disabled) return false;
    return command("brushless", API.stepperMotorToggle, {}, {success: p => {
      requireConfirmation(p.confirmed === true && typeof p.on === "boolean", "the motor state");
      setText(els.stepperMessage, `Brushless motor ${p.on ? "ON" : "OFF"} at ${p.pulse_us} µs`);
    }});
  }

  function requestBrushlessPulse() {
    if (els.brushlessApplyPulse.disabled) return false;
    const pulseUs = Number(els.brushlessPulseWidth.value);
    if (!Number.isInteger(pulseUs) || pulseUs < 1000 || pulseUs > 2000) {
      setCommandFeedback("Brushless pulse rejected: enter an integer from 1000 through 2000 µs");
      return false;
    }
    return command("pulse", API.stepperMotorPulse, {pulse_us: pulseUs}, {success: p => {
      requireConfirmation(p.confirmed === true && Number(p.setpoint_us) === pulseUs, "the pulse width");
      brushlessPulseDirty = false;
      setText(els.stepperMessage, `Brushless ON pulse confirmed at ${pulseUs} µs${p.stepper?.stepper_brushless_motor_on ? " and applied live" : ""}`);
    }});
  }

  function handleMotorShortcut() {
    if (els.brushlessMotorToggle.disabled || actions.has("brushless")) return false;
    void requestBrushlessToggle();
    return true;
  }

  // E-STOP never waits behind an in-flight move; only reset asks confirmation.
  els.emergencyStop.addEventListener("click", () => void command("E-STOP", API.stepperEstop, {}, {
    success: () => setText(els.stepperMessage, "E-STOP latched; step pulses inhibited and brushless motor OFF")
  }));
  els.emergencyReset.addEventListener("click", () => {
    const prerequisite = getLatest()?.stepper_mode === "controllino"
      ? "Software run must be stopped." : "Physical D4 must be OFF.";
    if (window.confirm(`Reset the E-STOP latch? ${prerequisite} This permits commands but does not start motion.`)) {
      void command("reset", API.stepperEstopReset, {}, {
        success: () => setText(els.stepperMessage, "E-STOP reset; motion remains stopped")
      });
    }
  });
  els.stepperSetDroZero.addEventListener("click", () => {
    if (els.stepperSetDroZero.disabled) return;
    setText(els.stepperDroZeroStatus, "Saving system zero…");
    void command("zero", API.stepperDroZero, {}, {
      target: els.stepperDroZeroStatus,
      success: p => {
        requireConfirmation(p.zero?.set === true && p.zero?.motion_commanded === false, "a display-only zero");
        lastFreshDroPositionMm = null;
        setText(els.stepperDroZeroStatus, `Zero: ${Number(p.zero.raw_position_mm).toFixed(2)} mm raw · saved`);
      }
    });
  });

  function requestControlMode() {
    const webPosition = els.stepperModeWeb.checked;
    const previous = getLatest()?.stepper_control_mode === "web_position";
    controlModeDirty = true;
    return command("mode", API.stepperControlMode, {web_position: webPosition}, {
      success: p => {
        const mode = p.stepper?.stepper_control_mode || p.sample?.stepper_control_mode;
        requireConfirmation(p.confirmed === true && mode === (webPosition ? "web_position" : "local_velocity"), "the requested mode");
        controlModeDirty = false;
        setText(els.stepperMessage, `${webPosition ? "Positional" : "Directional"} selected`);
      },
      failure: () => {
        controlModeDirty = false;
        els.stepperModeWeb.checked = previous;
        els.stepperModeLocal.checked = !previous;
      }
    });
  }

  els.stepperHome.addEventListener("click", () => {
    if (els.stepperHome.disabled ||
        !window.confirm(`Move upward toward D8 until its top limit switch activates? Speed is fixed at ${limits.home_speed_mm_s} mm/s; D4 must be armed and D5 set to Reverse.`)) return;
    void command("home", API.stepperHome, {}, {
      success: p => setText(els.stepperMessage, p.stepper?.stepper_negative_limit_active
        ? "D8 top limit reached" : `D8 top-limit move accepted at ${limits.home_speed_mm_s} mm/s`)
    });
  });
  els.stepperApplySpeed.addEventListener("click", () => {
    if (els.stepperApplySpeed.disabled) return;
    const speed = Number(els.stepperSpeed.value);
    if (!Number.isFinite(speed) || speed < limits.min_speed_mm_s || speed > limits.max_speed_mm_s) {
      setCommandFeedback(`Speed rejected: enter ${limits.min_speed_mm_s} through ${limits.max_speed_mm_s} mm/s`);
      return;
    }
    void command("speed", API.stepperSpeed, {speed_mm_s: speed}, {success: p => {
      const confirmedSpeed = Number(p.sample?.stepper_command_speed_mm_s);
      requireConfirmation(p.confirmed === true && Number.isFinite(confirmedSpeed), "the configured speed");
      setText(els.stepperMessage, `Confirmed speed: ${confirmedSpeed.toFixed(1)} mm/s`);
    }});
  });
  function requestLocalRun(direction) {
    // Stop has its own key and is allowed while a run acknowledgement is pending.
    return command(direction === 0 ? "local-stop" : "local-run",
      API.stepperLocalRun, {direction}, {
        success: () => setText(els.stepperMessage, direction === 0
          ? "Local motion stopped" : `Local ${direction > 0 ? "Forward" : "Reverse"} running`)
      });
  }

  // Event bindings and keyboard hooks: no navigation or form POST reloads.
  els.stepperForm.addEventListener("input", updateControls);
  for (const input of [els.stepperDistance, els.stepperSpeed]) {
    input.addEventListener("input", () => { messageSticky = false; setCommandFeedback(); });
  }
  els.brushlessPulseWidth.addEventListener("input", () => {
    brushlessPulseDirty = true;
    messageSticky = false;
  });
  for (const input of modeInputs) {
    input.addEventListener("change", () => { if (input.checked) void requestControlMode(); });
  }
  els.stepperForm.addEventListener("submit", event => { event.preventDefault(); void requestMove(); });
  for (const [id, action] of Object.entries({
    brushlessMotorToggle: requestBrushlessToggle, brushlessApplyPulse: requestBrushlessPulse,
    stepperStop: requestStop, stepperRunReverse: () => requestLocalRun(-1),
    stepperRunStop: () => requestLocalRun(0), stepperRunForward: () => requestLocalRun(1)
  })) els[id].addEventListener("click", () => { if (!els[id].disabled) void action(); });

  return {render, handleSpaceShortcut, handleMotorShortcut, applyMotionPlan};
}

// PANEL: Position servo — MS62 calibration and On/Off; shared by both dashboards.
// Firmware owns return-and-release timing. Rendering never sends motion commands.
export function createServoComponent({getLatest, applySample, postJson}) {
  const brushless = document.getElementById("brushlessMotorToggle").closest("fieldset");
  const panel = document.createElement("fieldset");
  panel.className = "brushless-control servo-control";
  panel.style.display = "none";
  panel.innerHTML = `
    <legend>Position servo</legend>
    <span data-servo="state" class="pill" aria-live="polite">Unknown</span>
    <label class="brushless-pulse-field">Displacement from off (° clockwise)
      <input data-servo="angle" type="number" min="0" step="0.1" inputmode="decimal">
    </label>
    <div class="servo-actions">
      <button data-servo="toggle" class="servo-toggle" type="button" role="switch"
        aria-label="Servo" aria-checked="false">
        <span class="servo-toggle-track" aria-hidden="true"></span>
        <span data-servo="toggleLabel">Off</span>
      </button>
      <button data-servo="zero" type="button" title="Moves to the counterclockwise endpoint (0°), then disables pulses">Zero</button>
    </div>
    <small data-servo="feedback" role="status"></small>`;
  brushless.insertAdjacentElement("afterend", panel);
  const el = Object.fromEntries([...panel.querySelectorAll("[data-servo]")]
    .map(node => [node.dataset.servo, node]));
  let pending = false, dirty = false;

  function render(sample) {
    const servo = sample.stepper_aux_kind === "servo";
    brushless.hidden = servo;
    brushless.style.display = servo ? "none" : "";
    panel.style.display = servo ? "" : "none";
    if (!servo) return;
    const settings = sample.servo_settings;
    const live = sample.stepper_connected === true && !sample.stepper_transport_error;
    const releasing = sample.stepper_servo_releasing === true;
    const enabled = sample.stepper_servo_enabled === true;
    const ready = live && settings && sample.stepper_servo_release_capable === true;
    el.state.textContent = !live ? "Disconnected" : !ready ? "Firmware update required" :
      releasing ? "Returning to off" : enabled ? "Holding target" : "Pulses disabled";
    if (settings) {
      if (!dirty) el.angle.value = settings.displacement_deg;
      el.angle.max = Math.floor((settings.off_pulse_us - sample.stepper_servo_min_us) * 0.135 * 10) / 10;
    }
    const on = enabled && !releasing;
    el.toggle.setAttribute("aria-checked", String(on));
    el.toggleLabel.textContent = on ? "On" : "Off";
    const valid = el.angle.value !== "" && el.angle.checkValidity();
    el.angle.disabled = pending || !ready;
    el.toggle.disabled = pending || !ready || (!on && !valid) || sample.stepper_estop_latched;
    el.zero.disabled = pending || !ready || enabled || releasing || sample.stepper_estop_latched;
  }

  async function send(action) {
    pending = true;
    render(getLatest());
    el.feedback.textContent = "";
    try {
      const result = await postJson("/api/stepper/servo", {
        action, displacement_deg: Number(el.angle.value)
      });
      if (result.confirmed !== true) throw new Error("Servo command was not confirmed");
      dirty = false;
      if (result.sample) applySample(result.sample);
      if (action === "endpoint") el.feedback.textContent = "Off endpoint commanded and saved. Clockwise range: 0–270°.";
    } catch (error) {
      el.feedback.textContent = error.message;
    } finally {
      pending = false;
      render(getLatest());
    }
  }
  el.angle.addEventListener("input", () => { dirty = true; render(getLatest()); });
  el.toggle.addEventListener("click", () => {
    if (!el.toggle.disabled) void send(el.toggle.getAttribute("aria-checked") === "true" ? "off" : "on");
  });
  el.zero.addEventListener("click", () => { if (!el.zero.disabled) void send("endpoint"); });
  return {render};
}

// ============================================================================
// 6. TEST METADATA
// ============================================================================
// PANEL: Test Metadata — editing, saving and derived motion setpoints.
// Markup: index-lean.html; appearance: dashboard-lean.css (search Test Metadata).

export function createMetadataComponent({
  geometry,
  stepperLimits,
  applyMotionPlan,
  onPowderFlowChange
}) {
  const els = elements([
    "metadataForm", "metadataStatus", "metadataMotionPlan"
  ]);
  let revision = 0;
  const actions = createActions({
    onError: error => setText(els.metadataStatus, `Save failed: ${error.message}`)
  });
  const powderFlowField = els.metadataForm.elements.namedItem(
    "powder_flow_rate_g_per_s",
  );
  const durationField = els.metadataForm.elements.namedItem("test_duration_s");
  const powderMassPerTravel = Number(
    geometry.powder_mass_per_stepper_travel_g_per_mm,
  );

  function powderFlowRateGPerS() {
    const parsed = Number(powderFlowField.value);
    return powderFlowField.value.trim() !== "" &&
      Number.isFinite(parsed) &&
      parsed > 0
      ? parsed
      : null;
  }

  function updateMotionPlan() {
    powderFlowField.setCustomValidity("");
    durationField.setCustomValidity("");
    const powderFlowGPerS = powderFlowRateGPerS();
    onPowderFlowChange(powderFlowGPerS);

    if (powderFlowField.value.trim() === "") {
      setText(
        els.metadataMotionPlan,
        `Enter powder flow and duration to calculate Positional setpoints · geometry ${powderMassPerTravel} g/mm`,
      );
      return;
    }
    if (powderFlowGPerS === null) {
      powderFlowField.setCustomValidity(
        "Enter a powder flow rate greater than 0 g/s.",
      );
      setText(els.metadataMotionPlan, "Powder flow must be greater than 0 g/s.");
      return;
    }

    const speedMmS = powderFlowGPerS / powderMassPerTravel;
    const rawDuration = durationField.value.trim();
    const durationS = rawDuration === "" ? null : Number(rawDuration);
    const validDuration = durationS === null ||
      (Number.isFinite(durationS) && durationS > 0);
    const distanceMm = validDuration && durationS !== null
      ? speedMmS * durationS
      : null;
    applyMotionPlan({speedMmS, distanceMm});

    const errors = [];
    if (
      speedMmS < stepperLimits.min_speed_mm_s ||
      speedMmS > stepperLimits.max_speed_mm_s
    ) {
      const message = `Calculated speed ${speedMmS.toFixed(3)} mm/s is outside the ${stepperLimits.min_speed_mm_s}–${stepperLimits.max_speed_mm_s} mm/s range.`;
      powderFlowField.setCustomValidity(message);
      errors.push(message);
    }
    if (!validDuration) {
      const message = "Test duration must be greater than 0 seconds.";
      durationField.setCustomValidity(message);
      errors.push(message);
    } else if (
      distanceMm !== null &&
      (
        distanceMm < stepperLimits.min_distance_mm ||
        distanceMm > stepperLimits.max_distance_mm
      )
    ) {
      const message = `Calculated travel ${distanceMm.toFixed(2)} mm is outside the ${stepperLimits.min_distance_mm}–${stepperLimits.max_distance_mm} mm range.`;
      durationField.setCustomValidity(message);
      errors.push(message);
    }

    if (errors.length > 0) {
      setText(els.metadataMotionPlan, errors[0]);
    } else if (distanceMm === null) {
      setText(
        els.metadataMotionPlan,
        `Positional speed ${speedMmS.toFixed(3)} mm/s · enter duration to calculate travel`,
      );
    } else {
      setText(
        els.metadataMotionPlan,
        `Positional setpoints ${speedMmS.toFixed(3)} mm/s · ${distanceMm.toFixed(2)} mm travel · geometry ${powderMassPerTravel} g/mm`,
      );
    }
  }

  function fill(metadata) {
    for (const [key, value] of Object.entries(metadata)) {
      const field = els.metadataForm.elements.namedItem(key);
      if (field && field.value !== value) field.value = value || "";
    }
    updateMotionPlan();
    setText(els.metadataStatus, "Saved");
  }

  els.metadataForm.addEventListener("input", event => {
    revision += 1;
    setText(els.metadataStatus, "Unsaved");
    if (event.target === powderFlowField || event.target === durationField) {
      updateMotionPlan();
    }
  });
  els.metadataForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (actions.has("save")) return;
    const data = Object.fromEntries(new FormData(els.metadataForm).entries());
    const submittedRevision = revision;
    setText(els.metadataStatus, "Saving…");
    await actions.run("save", () => postJson(API.metadata, data), {
      success: payload => {
        if (revision === submittedRevision) fill(payload.metadata);
        else setText(els.metadataStatus, "Unsaved");
      }
    });
  });

  updateMotionPlan();
  return {fill, powderFlowRateGPerS};
}

// ============================================================================
// 7. SOURCE DETAILS — device diagnostics and connection status
// ============================================================================
// ---------- Source details and connection indicators ----------
// PANEL: Source details — ESP32 and DXMR90 diagnostics.
// Shared with the top connection indicators; motion diagnostics live in stepper.js.

function sourceElements() { return elements([
  "espDot", "espStatus", "dxDot", "dxStatus", "stepperDot", "stepperStatus",
  "edDot", "edStatus", "edMode", "edAge", "edTransport", "edChannels",
  "historyStatus", "espRowDot", "espRowStatus", "dxRowDot", "dxRowStatus",
  "espMode", "espAge", "espPressureAdc", "espFlowAdc", "espPressure",
  "espFlow", "espTransport", "dxMode", "dxAge", "dxPort1", "dxPort2"
]); }

function presentation(label, mode, connected) {
  if (mode === "off") return {label: `${label} off`, row: "Off", state: "warn"};
  if (connected === true) return {label, row: "Live", state: "ok"};
  return {label, row: "Stale", state: "bad"};
}

export function renderSources(sample, historyLength) {
  const sourceEls = sourceElements();
  const espConnected = sample.esp32_connected;
  const dxConnected = sample.dxmr90_connected;
  const stepperConnected = sample.stepper_connected;
  const espMode = sample.esp32_mode || "--";
  const dxMode = sample.dxmr90_mode || "--";
  const stepperMode = sample.stepper_mode || "--";
  const pressureAdcReady = sample.esp32_pressure_adc_ready === true;
  const flowAdcReady = sample.esp32_flow_adc_ready === true;
  const edMode = sample.ed593_mode || "off";
  const ed = presentation("ED-593", edMode, sample.ed593_connected);
  if (edMode !== "off" && sample.ed593_transport_error) {
    ed.state = "bad";
    ed.row = "Transport error";
  }
  setDot(sourceEls.edDot, ed.state);
  setText(sourceEls.edStatus, ed.label);
  setText(sourceEls.edMode, edMode);
  setText(sourceEls.edAge, ageText(sample, "ed593_age_ms"));
  setText(sourceEls.edTransport, edMode === "off" ? "Disabled" : sample.ed593_transport_error || "No reported transport error");
  sourceEls.edChannels.replaceChildren();
  for (let i = 0; i < 8; i++) {
    const row = document.createElement("tr");
    const enabled = sample[`ed593_tc${i}_enabled`];
    const fault = sample[`ed593_tc${i}_fault`];
    const live = sample.ed593_connected === true && !sample.ed593_transport_error;
    for (const value of [`TC ${i}`, live ? numberValue(sample, `ed593_tc${i}_temperature_c`, 2) : "--",
                         enabled === true ? "Yes" : enabled === false ? "No" : "Unknown",
                         fault === true ? "Yes" : fault === false ? "No" : "Unknown"]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    }
    sourceEls.edChannels.appendChild(row);
  }
  const esp = presentation("ESP32", espMode, espConnected);
  const dx = presentation("DXMR90", dxMode, dxConnected);
  const controllerLabel = stepperMode === "controllino" ? "Controllino MAXI" : "Arduino Yun";
  const stepper = presentation(controllerLabel, stepperMode, stepperConnected);
  if (espConnected && (!pressureAdcReady || !flowAdcReady)) {
    esp.label = "ESP32";
    esp.row = "Live / ADC partial";
    esp.state = "warn";
  }

  setDot(sourceEls.espDot, esp.state);
  setDot(sourceEls.dxDot, dx.state);
  setDot(sourceEls.stepperDot, stepper.state);
  setDot(sourceEls.espRowDot, esp.state);
  setDot(sourceEls.dxRowDot, dx.state);
  setText(sourceEls.espStatus, esp.label);
  setText(sourceEls.dxStatus, dx.label);
  setText(sourceEls.stepperStatus, stepper.label);
  for (const [element, status] of [[sourceEls.edStatus, ed], [sourceEls.espStatus, esp], [sourceEls.dxStatus, dx], [sourceEls.stepperStatus, stepper]]) {
    const pill = element.closest(".pill");
    pill.title = `${status.label}: ${status.row}`;
    pill.setAttribute("aria-label", pill.title);
  }
  setText(sourceEls.espRowStatus, esp.row);
  setText(sourceEls.dxRowStatus, dx.row);
  setText(sourceEls.espMode, espMode);
  setText(sourceEls.dxMode, dxMode);
  setText(sourceEls.espAge, ageText(sample, "esp32_age_ms"));
  setText(sourceEls.dxAge, ageText(sample, "dxmr90_age_ms"));
  setText(sourceEls.espPressureAdc, pressureAdcReady ? "Ready" : "Unavailable");
  setText(sourceEls.espFlowAdc, flowAdcReady ? "Ready" : "Unavailable");
  setText(sourceEls.espPressure, `${numberValue(sample, "esp32_p_combined_bar", 3)} bar`);
  setText(sourceEls.espFlow, `${numberValue(sample, "esp32_f_combined_gmin", 2)} g/min`);
  setText(sourceEls.espTransport, espMode === "off"
    ? "Disabled"
    : sample.esp32_transport_error || (espConnected ? "Connected" : "Waiting for stream"));
  setText(sourceEls.dxPort1, `${numberValue(sample, "dxmr90_port1_mass_flow_g_min", 2)} g/min`);
  setText(sourceEls.dxPort2, `${numberValue(sample, "dxmr90_port2_mass_flow_g_min", 2)} g/min`);
  setText(sourceEls.historyStatus, `${historyLength} samples`);
}

export function setEspTransportMessage(message) {
  const sourceEls = sourceElements();
  setText(sourceEls.espTransport, message);
}

// ============================================================================
// 8. SHARED — formatting, DOM helpers, API and command lifecycle
// ============================================================================
export function elements(ids) {
  return Object.fromEntries(ids.map(id => {
    const element = document.getElementById(id);
    if (!element) throw new Error(`Dashboard element #${id} is missing`);
    return [id, element];
  }));
}

export function setText(element, value) {
  element.textContent = value;
}

export function setDot(element, state) {
  element.classList.remove("ok", "warn", "bad");
  element.classList.add(state);
}

export function numberValue(sample, key, digits) {
  if (!sample || sample[key] === null || sample[key] === undefined) return "--";
  const value = Number(sample[key]);
  return Number.isFinite(value) ? value.toFixed(digits) : String(sample[key]);
}

export function maxNumberValue(sample, keys, digits) {
  if (!sample) return "--";
  const values = keys
    .map(key => sample[key])
    .filter(value => value !== null && value !== undefined)
    .map(Number)
    .filter(Number.isFinite);
  return values.length ? Math.max(...values).toFixed(digits) : "--";
}

export function ageText(sample, key) {
  if (!sample || sample[key] === null || sample[key] === undefined) return "--";
  const ms = Number(sample[key]);
  if (!Number.isFinite(ms)) return "--";
  return ms < 1000 ? `${ms.toFixed(0)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function shortcutTargetIsGuarded(event) {
  const target = event.target;
  const tagName = target && target.tagName ? target.tagName.toUpperCase() : "";
  const editing = target && (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(tagName)
  );
  // A focused button or link owns its Space-key behavior. In particular,
  // never turn Space on a focused E-STOP into a global motion command.
  const activatingControl = ["BUTTON", "A"].includes(tagName);
  return Boolean(
    editing || activatingControl || event.defaultPrevented || event.repeat ||
    event.ctrlKey || event.altKey || event.metaKey || event.shiftKey
  );
}

export const API = Object.freeze({
  config: "/api/config",
  state: "/api/state",
  latest: "/api/latest",
  history: "/api/history",
  events: "/api/events",
  startRun: "/api/run/start",
  stopRun: "/api/run/stop",
  exportLatest: "/api/export/latest",
  metadata: "/api/metadata",
  experimentLoad: "/api/experiment/load",
  experimentStart: "/api/experiment/start",
  experimentExample: "/api/experiment/example.csv",
  solenoidToggle: index => `/api/solenoid/toggle?n=${index}`,
  stepperMove: "/api/stepper/move",
  stepperStop: "/api/stepper/stop",
  stepperEstop: "/api/stepper/estop",
  stepperEstopReset: "/api/stepper/estop/reset",
  stepperMotorToggle: "/api/stepper/motor/toggle",
  stepperMotorPulse: "/api/stepper/motor/pulse",
  stepperHome: "/api/stepper/home",
  stepperControlMode: "/api/stepper/control-mode",
  stepperLocalRun: "/api/stepper/local-run",
  stepperSpeed: "/api/stepper/speed",
  stepperDroZero: "/api/stepper/dro-zero"
});

// One response parser for reads and commands; never retry a motor command.
async function request(url, payload) {
  const response = await fetch(url, payload === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); }
  catch { throw new Error(text || `${response.status} ${response.statusText}`); }
  if (!response.ok) throw new Error(data?.error || text || String(response.status));
  return data;
}
export const getJson = url => request(url);
export const postJson = (url, payload = {}) => request(url, payload);

// Keyed command lifecycle shared by panels. Separate stop/E-STOP keys remain
// available while other requests wait. No automatic POST retry or optimistic ACK.
export function createActions({refresh = () => {}, onError = () => {}} = {}) {
  const pending = new Set();
  return {
    has: key => pending.has(key),
    async run(key, execute, {success = () => {}, failure = onError, finish = () => {}} = {}) {
      if (pending.has(key)) return false;
      pending.add(key);
      try {
        refresh();
        await success(await execute());
      } catch (error) {
        failure(error);
      } finally {
        pending.delete(key);
        try { finish(); } finally { refresh(); }
      }
      return true;
    }
  };
}

export function downloadLatestExport() {
  // Download without navigating away from the live SSE dashboard.
  const link = document.createElement("a");
  link.href = API.exportLatest;
  link.download = "export.csv";
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

// Experiment Start is separate from the toolbar's recording Start.
export function createExperimentComponent() {
  const el = elements([
    "experimentStatus", "experimentFile", "experimentCsv", "experimentExample",
    "experimentLoad", "experimentStart", "experimentPreview", "experimentFeedback",
    "experimentChecks"
  ]);
  let snapshot = null;
  let name = "experiment.csv";
  let dirty = false;
  let pending = false;

  function updateControls() {
    const running = snapshot?.state === "running";
    el.experimentFile.disabled = pending || running;
    el.experimentCsv.disabled = pending || running;
    el.experimentExample.disabled = pending || running;
    el.experimentLoad.disabled = pending || running || !el.experimentCsv.value.trim();
    el.experimentStart.disabled = pending || running || dirty || !snapshot?.program?.length;
  }

  function render(incoming) {
    if (!incoming || (snapshot && incoming.revision < snapshot.revision)) return;
    const changedProgram = !snapshot || incoming.program_id !== snapshot.program_id;
    snapshot = incoming;
    if (changedProgram && !dirty) {
      name = snapshot.name || name;
      el.experimentCsv.value = snapshot.program.length
        ? [snapshot.columns.join(","), ...snapshot.program.map(row =>
            snapshot.columns.map(column => row[column]).join(","))].join("\n") + "\n"
        : "";
    }
    const labels = {
      empty: "No spreadsheet loaded", ready: "Ready", running: `Checking row ${snapshot.current_row}`,
      completed: "Completed", failed: "Failed"
    };
    setText(el.experimentStatus, labels[snapshot.state] || snapshot.state);
    const head = el.experimentPreview.querySelector("thead");
    const body = el.experimentPreview.querySelector("tbody");
    head.replaceChildren();
    body.replaceChildren();
    const header = document.createElement("tr");
    for (const column of ["Row", ...snapshot.columns, "Result"]) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = column;
      header.appendChild(cell);
    }
    head.appendChild(header);
    snapshot.program.forEach((action, index) => {
      const result = snapshot.results[index]?.status || "pending";
      const row = document.createElement("tr");
      row.dataset.status = result;
      for (const value of [index + 1, ...snapshot.columns.map(column => action[column]), result]) {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        row.appendChild(cell);
      }
      body.appendChild(row);
    });
    const latestResult = snapshot.results.at(-1);
    let message = snapshot.name ? `${snapshot.name}: ${labels[snapshot.state]}.` : "Load a spreadsheet to begin.";
    if (snapshot.state === "running") message += " Waiting for all requirements to pass.";
    if (snapshot.state === "running" && latestResult?.motion_started) message += " Motion started; waiting for firmware pulse completion.";
    if (snapshot.state === "running" && latestResult?.request_pending) message += " Command response pending; monitoring continues.";
    if (snapshot.state === "failed") {
      message += ` Row ${latestResult.row}: ${latestResult.reason}.`;
      if (latestResult.monitor_row) message += ` Persistent DRO check from row ${latestResult.monitor_row} failed.`;
      if (latestResult.error) message += ` ${latestResult.error}`;
      if (latestResult.stop_pending) message += " Stop request pending.";
      if (latestResult.request_error) message += ` Command outcome: ${latestResult.request_error}`;
      if (latestResult.stop_error) message += ` Stop request failed: ${latestResult.stop_error}`;
    }
    setText(el.experimentFeedback, message);
    el.experimentChecks.replaceChildren();
    const displayedChecks = Object.entries(latestResult?.checks || {}).map(([name, check]) => [name, name, check]);
    for (const monitor of Object.values(snapshot.monitors || {})) {
      if (monitor.active) {
        for (const [name, check] of Object.entries(monitor.checks || {})) {
          displayedChecks.push([`DRO monitor (row ${monitor.row}) / ${name}`, name, check]);
        }
      }
    }
    for (const [displayName, checkName, check] of displayedChecks) {
      const item = document.createElement("li");
      item.dataset.ok = String(check.ok);
      const outcome = check.ok ? "Pass" : snapshot.state === "failed" ? "Fail" : "Waiting";
      const evidence = checkName === "freshness"
        ? `${check.age_ms ?? "unknown"} ms age; maximum ${check.max_age_ms} ms`
        : checkName === "health"
          ? `${check.transport_error_available ? check.transport_error || "no reported transport error" : "transport-error telemetry unavailable"}; dashboard connected flag: ${check.connected ?? "unknown"} (diagnostic)`
          : checkName === "requested_state" || check.evidence === "electrical_output"
            ? `requested ${check.requested}; observed ${check.observed === true ? "on" : check.observed === false ? "off" : check.observed ?? "unknown"}${check.evidence === "electrical_output" ? ` (inferred from ${check.valve_type} valve output: ${check.output_on === true ? "energized" : check.output_on === false ? "de-energized" : "unknown"})` : ""}`
          : checkName === "temperature"
            ? `${check.observed ?? "unknown"} °C; allowed ${check.min_c}–${check.max_c} °C; enabled ${check.enabled ?? "unknown"}; fault ${check.fault ?? "unknown"}`
          : checkName === "position"
            ? `${check.observed ?? "unknown"} mm (raw DRO position); allowed ${check.min_mm}–${check.max_mm} mm`
          : checkName === "move"
            ? `command ${check.command_id ?? "unknown"}; expected ${check.expected_command_id}; state ${check.state ?? "unknown"}; remaining ${check.remaining_mm ?? "unknown"} mm`
          : Object.entries(check.observed).map(([field, value]) => `${field}: ${value ?? "unknown"}`).join(", ");
      item.textContent = `${displayName}: ${outcome} · ${evidence}`;
      el.experimentChecks.appendChild(item);
    }
    for (const [device, request] of Object.entries(latestResult?.request?.targets || {})) {
      const item = document.createElement("li");
      item.textContent = `${device}: ${request.status === "failed" ? `request failed · ${request.error}` : `requested ${request.set_point}`}`;
      el.experimentChecks.appendChild(item);
    }
    updateControls();
  }

  async function perform(execute) {
    if (pending || snapshot?.state === "running") return;
    pending = true;
    updateControls();
    try { await execute(); }
    catch (error) { setText(el.experimentFeedback, error.message); }
    finally { pending = false; updateControls(); }
  }

  el.experimentCsv.addEventListener("input", () => { dirty = true; updateControls(); });
  el.experimentFile.addEventListener("change", () => void perform(async () => {
    const file = el.experimentFile.files[0];
    if (!file) return;
    if (file.size > 256 * 1024) throw new Error("Spreadsheet exceeds 256 KiB");
    name = file.name;
    el.experimentCsv.value = await file.text();
    dirty = true;
    setText(el.experimentFeedback, "Review the CSV, then load the spreadsheet.");
  }));
  el.experimentExample.addEventListener("click", () => void perform(async () => {
    const response = await fetch(API.experimentExample);
    if (!response.ok) throw new Error("Could not read the ESP32 example");
    el.experimentCsv.value = await response.text();
    name = "esp32_health.csv";
    dirty = true;
    setText(el.experimentFeedback, "Review the example thresholds, then load the spreadsheet.");
  }));
  el.experimentLoad.addEventListener("click", () => void perform(async () => {
    const payload = await postJson(API.experimentLoad, {csv: el.experimentCsv.value, name});
    if (snapshot && payload.experiment.program_id < snapshot.program_id) {
      throw new Error("The spreadsheet was replaced; review and load your CSV again.");
    }
    dirty = false;
    render(payload.experiment);
  }));
  el.experimentStart.addEventListener("click", () => void perform(async () => {
    render((await postJson(API.experimentStart, {program_id: snapshot.program_id})).experiment);
  }));
  updateControls();
  return {render};
}

// ============================================================================
// 9. STARTUP — bind panels, stream/poll updates, charts and keyboard shortcuts
// ============================================================================
export async function startDashboard() {
  const operationalConfig = await getJson(API.config);
  const historyLimit = Math.min(
    UI_CONFIG.historyLimit,
    operationalConfig.history_limit
  );
  const state = {
    latest: null,
    run: {},
    history: []
  };
  let pollTimer = null;
  let pollRequestPending = false;
  let toolbar;
  let stepper;
  let metadata;
  const experiment = createExperimentComponent();

  let chartFrame = null;
  function scheduleCharts() {
    if (chartFrame !== null) return;
    chartFrame = requestAnimationFrame(() => {
      chartFrame = null;
      drawAllCharts(state.history);
    });
  }

  function renderAll() {
    toolbar.renderRun(state.run);
    if (!state.latest) return;
    toolbar.renderSample(state.latest);
    renderMetrics(state.latest, metadata?.powderFlowRateGPerS());
    renderSources(state.latest, state.history.length);
    stepper.render(state.latest);
    scheduleCharts();
  }

  function applySample(sample, append = true) {
    state.latest = sample;
    if (append && sample.timestamp_iso !== state.history.at(-1)?.timestamp_iso) {
      state.history.push(sample);
      if (state.history.length > historyLimit) state.history.shift();
    }
    renderAll();
  }

  function setRunState(run) {
    state.run = run || {};
    toolbar.renderRun(state.run);
  }

  toolbar = createToolbarComponent({
    getState: () => state,
    applySample,
    setRunState,
    setEspTransportMessage,
    solenoidCount: operationalConfig.solenoid_count
  });
  const stepperLimits = {
    ...operationalConfig.stepper,
    min_distance_mm: UI_CONFIG.stepperInputs.minDistanceMm
  };
  stepper = createStepperComponent({
    getLatest: () => state.latest,
    applySample,
    limits: stepperLimits
  });
  metadata = createMetadataComponent({
    geometry: operationalConfig.geometry,
    stepperLimits,
    applyMotionPlan: plan => stepper.applyMotionPlan(plan),
    onPowderFlowChange: powderFlowGPerS => {
      if (state.latest) renderMetrics(state.latest, powderFlowGPerS);
    }
  });

  function applyState(payload) {
    if (payload.run) state.run = payload.run;
    experiment.render(payload.experiment);
    if (payload.sample) applySample(payload.sample, false);
    if (payload.metadata) metadata.fill(payload.metadata);
    toolbar.renderRun(state.run);
  }

  async function hydrate() {
    const historyPayload = await getJson(`${API.history}?limit=${historyLimit}`);
    state.history = historyPayload.history || [];
    applyState(await getJson(API.state));
  }

  function startPollingFallback() {
    if (pollTimer) return;
    pollTimer = window.setInterval(async () => {
      if (pollRequestPending) return;
      pollRequestPending = true;
      try {
        const payload = await getJson(API.latest);
        if (payload.run) state.run = payload.run;
        experiment.render(payload.experiment);
        if (payload.sample) applySample(payload.sample);
        else toolbar.renderRun(state.run);
        setStreamStatus("Polling", "warn");
      } catch (error) {
        setStreamStatus("Offline", "bad");
      } finally {
        pollRequestPending = false;
      }
    }, UI_CONFIG.pollingIntervalMs);
  }

  function stopPollingFallback() {
    if (!pollTimer) return;
    window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function connectEvents() {
    if (!window.EventSource) {
      startPollingFallback();
      return;
    }
    const events = new EventSource(API.events);
    events.addEventListener("open", () => {
      stopPollingFallback();
      setStreamStatus("Live", "ok");
    });
    events.addEventListener("sample", event => {
      setStreamStatus("Live", "ok");
      applySample(JSON.parse(event.data));
    });
    events.addEventListener("state", event => {
      setRunState(JSON.parse(event.data));
    });
    events.addEventListener("experiment", event => {
      experiment.render(JSON.parse(event.data));
    });
    events.addEventListener("error", () => {
      setStreamStatus("Reconnecting", "warn");
      startPollingFallback();
    });
  }

  document.addEventListener("keydown", event => {
    if (shortcutTargetIsGuarded(event)) return;

    const spacePressed = event.code === "Space" || event.key === " ";
    if (spacePressed) {
      if (!stepper.handleSpaceShortcut()) return;
      event.preventDefault();
      return;
    }

    const motorPressed = event.code === "KeyM" || event.key.toLowerCase() === "m";
    if (motorPressed) {
      if (!stepper.handleMotorShortcut()) return;
      event.preventDefault();
      return;
    }

    const index = Number(event.key) - 1;
    if (!Number.isInteger(index) ||
        index < 0 || index >= operationalConfig.solenoid_count) return;
    event.preventDefault();
    void toolbar.toggleSolenoid(index);
  });

  const chartObserver = new ResizeObserver(scheduleCharts);
  document.querySelectorAll(".plot-panel").forEach(panel => chartObserver.observe(panel));
  await hydrate();
  connectEvents();
}

if (document.querySelector('script[data-dashboard="lean"]')) await startDashboard().catch(error => {
  console.error("Dashboard startup failed", error);
  setStreamStatus("Offline", "bad");
});
