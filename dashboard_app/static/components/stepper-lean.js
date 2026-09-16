// PANEL: Motor Control — controls, DRO piston and Brushless motor.
// Also owns Interlocks, E-STOP, and motion telemetry inside Source details.
// Markup: index-lean.html; appearance: dashboard-lean.css (search the same panel name).
import {API, postJson, createActions} from "../api-lean.js";
import {createServoComponent} from "./servo.js";
import {UI_CONFIG} from "../config.js";
import {elements, numberValue, setText} from "../dom.js";

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
    const manualDirection = latest.stepper_manual_direction || "--";
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
