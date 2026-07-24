import {API, postJson} from "../api.js";
import {UI_CONFIG} from "../config.js";
import {elements, numberValue, setText} from "../dom.js";

export function createStepperComponent({getLatest, applySample, limits}) {
  const els = elements([
    "emergencyStop", "emergencyReset", "emergencyState", "stepperForm",
    "stepperDistanceField", "stepperDistance", "stepperSpeed", "stepperSpeedLabel",
    "stepperModeLocal", "stepperModeWeb", "stepperCommandField",
    "stepperCommandInput", "stepperMove", "stepperHome", "stepperStop",
    "stepperApplySpeed", "stepperMessage", "stepperState", "stepperConfiguredSpeed",
    "stepperEffectiveSpeed", "stepperMeasuredSpeed", "stepperPulseEngine",
    "stepperCommand", "stepperOwner", "stepperModeStatus", "stepperLocal",
    "stepperPistonVisual", "stepperDroState", "stepperDroPosition",
    "stepperDroDisplacement", "stepperDroDirection", "stepperDroRange",
    "stepperDroFrames",
    "stepperD5Label", "stepperManualDirection", "stepperDirectionStatus",
    "stepperDriverOutput", "stepperPositiveLimit", "stepperNegativeLimit",
    "stepperLimitFilter", "stepperBlocked", "stepperSequence", "stepperTransport"
  ]);

  let controlModeDirty = false;
  let controlModeRequestPending = false;
  let messageSticky = false;
  let speedRequestPending = false;
  let motionRequestPending = false;
  let lastFreshDroFrameCount = null;
  let lastFreshDroPositionMm = null;
  let observedDroDirection = "Waiting";

  const droVisualMinMm = UI_CONFIG.droVisualRange.minMm;
  const droVisualMaxMm = UI_CONFIG.droVisualRange.maxMm;
  const droVisualSpanMm = droVisualMaxMm - droVisualMinMm;

  els.stepperDistance.min = String(limits.min_distance_mm);
  els.stepperDistance.max = String(limits.max_distance_mm);
  els.stepperDistance.step = String(UI_CONFIG.stepperInputs.distanceStepMm);
  els.stepperDistance.value = String(UI_CONFIG.stepperInputs.defaultDistanceMm);
  els.stepperSpeed.min = String(limits.min_speed_mm_s);
  els.stepperSpeed.max = String(limits.max_speed_mm_s);
  els.stepperSpeed.step = String(UI_CONFIG.stepperInputs.speedStepMmS);
  els.stepperSpeed.value = String(limits.default_speed_mm_s);
  const modeInputs = [els.stepperModeLocal, els.stepperModeWeb];

  function updateControls() {
    const latest = getLatest();
    const distance = Number(els.stepperDistance.value);
    const speed = Number(els.stepperSpeed.value);
    const connected = latest && latest.stepper_connected === true;
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
    const selectedDirection = authorizedDirection === "both" ? "forward" : authorizedDirection;
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

    els.emergencyStop.disabled = !connected || !estopCapable || estopLatched;
    els.emergencyReset.disabled = !connected || !estopCapable || !estopLatched ||
      moving || (!simulated && !d4Off);
    els.emergencyReset.title = !estopLatched
      ? "E-STOP is not latched"
      : !simulated && !d4Off
        ? "Turn physical D4 OFF before resetting"
        : "Reset the software latch; this does not start motion";

    els.stepperDistanceField.hidden = !webPositionMode;
    els.stepperCommandField.hidden = !webPositionMode;
    els.stepperMove.hidden = !webPositionMode;
    els.stepperHome.hidden = !webPositionMode;
    els.stepperStop.hidden = !webPositionMode;
    els.stepperApplySpeed.hidden = webPositionMode;
    els.stepperDistance.disabled = !webPositionMode;
    els.stepperDistance.title = webPositionMode
      ? "Positive travel magnitude; physical D5 selects Forward or Reverse"
      : "Available only in Web Position mode";
    els.stepperCommandInput.disabled = !webPositionMode;
    els.stepperCommandInput.title = webPositionMode
      ? "Optional identifier for this position command"
      : "Available only in Web Position mode";
    setText(els.stepperSpeedLabel, "Speed (mm/s)");
    els.stepperMove.disabled = motionRequestPending !== false || !commandCapable ||
      !directionCalibrationSafe || !connected || !webPositionMode || estopLatched ||
      !enabled || moving || !valid || !directionSelected || positiveBlocked || negativeBlocked;
    // A confirmed moving state exposes Stop even if Move has not returned yet.
    els.stepperStop.disabled = motionRequestPending === "stop" || estopLatched ||
      !commandCapable || !webPositionMode || !moving;
    els.stepperHome.disabled = motionRequestPending !== false || !homeCapable ||
      !directionCalibrationSafe || !connected || !webPositionMode || estopLatched ||
      !enabled || moving || (authorizedDirection !== "reverse" && authorizedDirection !== "both");
    const modeDisabled = motionRequestPending !== false ||
      controlModeRequestPending || !directionCalibrationSafe || !modeCommandCapable ||
      estopLatched || !connected || !d4Off || moving;
    for (const input of modeInputs) input.disabled = modeDisabled;
    els.stepperMove.title = motionRequestPending !== false
      ? "Waiting for the current motion command"
      : "Start the Web Position move (Space while the page has focus)";
    els.stepperStop.title = motionRequestPending === "stop"
      ? "Waiting for Stop confirmation"
      : "Stop Web Position motion (Space while the page has focus)";

    if (controlModeRequestPending) {
      for (const input of modeInputs) input.title = "Waiting for the Yún to confirm the control mode";
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
    els.stepperApplySpeed.disabled = speedRequestPending || webPositionMode || estopLatched;
    if (speedRequestPending) {
      els.stepperApplySpeed.title = "Waiting for the Yún to confirm the new speed";
    } else if (webPositionMode) {
      els.stepperApplySpeed.title = "Move uses the speed field directly in Web Position mode";
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
    const positionMm = Number(latest.stepper_dro_position_mm);
    const frameCount = Number(latest.stepper_dro_valid_frame_count);
    const hasPosition = droHasSample && Number.isFinite(positionMm);
    const fresh = hasPosition && latest.stepper_dro_fresh === true;
    const outOfRange = hasPosition &&
      (positionMm < droVisualMinMm || positionMm > droVisualMaxMm);
    const hasTrustworthyVisualPosition = fresh || lastFreshDroPositionMm !== null;

    els.stepperPistonVisual.classList.toggle(
      "is-unavailable",
      !hasPosition || !hasTrustworthyVisualPosition,
    );
    els.stepperPistonVisual.classList.toggle("is-stale", hasPosition && !fresh);
    els.stepperPistonVisual.classList.toggle("is-out-of-range", outOfRange);

    if (!hasPosition) {
      lastFreshDroFrameCount = null;
      lastFreshDroPositionMm = null;
      observedDroDirection = droCapable ? "Waiting for sample" : "Unavailable";
      setText(els.stepperDroDirection, observedDroDirection);
      setText(
        els.stepperDroRange,
        `DRO range: ${droVisualMinMm}–${droVisualMaxMm} mm · read-only display.`,
      );
      els.stepperPistonVisual.setAttribute(
        "aria-label",
        `Read-only piston position unavailable. ${observedDroDirection}.`,
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
      const positionPercent = 4 + ratio * 92;
      els.stepperPistonVisual.style.setProperty(
        "--piston-position",
        `${positionPercent.toFixed(3)}%`,
      );

      // Infer direction only when a new validated DRO frame arrives. Dashboard
      // polls between sensor frames should not make the direction flicker.
      if (frameCount !== lastFreshDroFrameCount) {
        if (lastFreshDroPositionMm === null) {
          observedDroDirection = "Position acquired";
        } else {
          const deltaMm = positionMm - lastFreshDroPositionMm;
          observedDroDirection = deltaMm > 0.005
            ? "Increasing →"
            : deltaMm < -0.005 ? "← Decreasing" : "Holding";
        }
        lastFreshDroFrameCount = frameCount;
        lastFreshDroPositionMm = positionMm;
      }
    }

    setText(
      els.stepperDroDirection,
      fresh ? observedDroDirection : "Frozen — stale",
    );
    setText(
      els.stepperDroRange,
      outOfRange
        ? `The ${positionMm.toFixed(2)} mm DRO coordinate is outside the ${droVisualMinMm}–${droVisualMaxMm} mm display range; the head is clamped at the edge.`
        : `DRO range: ${droVisualMinMm}–${droVisualMaxMm} mm · read-only display.`,
    );
    els.stepperPistonVisual.setAttribute(
      "aria-label",
      `Read-only piston head position ${positionMm.toFixed(2)} millimeters. ${
        fresh ? observedDroDirection : "Sensor data stale; graphic frozen"
      }.`,
    );
  }

  function render(latest) {
    const moving = latest.stepper_moving === true;
    const estopCapable = latest.stepper_estop_capable === true;
    const estopLatched = latest.stepper_estop_latched === true;
    const localEnabled = latest.stepper_local_enabled === true;
    const commandCapable = latest.stepper_command_capable === true;
    const connected = latest.stepper_connected === true;
    const controlMode = latest.stepper_control_mode || "unknown";
    const webPositionMode = controlMode === "web_position";
    document.body.classList.toggle("estop-latched", estopLatched);
    setText(els.emergencyState, estopLatched
      ? "LATCHED — step pulses inhibited"
      : !connected
        ? "Unavailable — stepper offline"
        : estopCapable
          ? "Ready"
          : "Unavailable — firmware update required");
    setText(els.stepperState, latest.stepper_state || "Unknown");
    setText(els.stepperOwner, `${latest.stepper_mode || "--"} / ${latest.stepper_control_owner || "--"}`);
    setText(els.stepperModeStatus, webPositionMode
      ? "Web Position"
      : controlMode === "local_velocity" ? "Local Velocity" : "--");
    setText(els.stepperConfiguredSpeed, `${numberValue(latest, "stepper_command_speed_mm_s", 3)} mm/s`);
    setText(els.stepperEffectiveSpeed, `${numberValue(latest, "stepper_speed_mm_s", 3)} mm/s`);
    const pulseMeasurementCapable = latest.stepper_pulse_measurement_capable === true;
    setText(els.stepperMeasuredSpeed, pulseMeasurementCapable
      ? `${numberValue(latest, "stepper_measured_speed_mm_s", 3)} mm/s (${numberValue(latest, "stepper_measured_pulse_rate_sps", 0)} pulses/s)`
      : "Unavailable — firmware update required");
    setText(els.stepperPulseEngine, latest.stepper_unified_timer_capable === true
      ? "Unified Timer1 (Local / Web / Home)"
      : "Legacy split scheduler — firmware update required");
    setText(els.stepperCommand, latest.stepper_command_id || "--");
    const droCapable = latest.stepper_dro_capable === true;
    const droHasSample = droCapable &&
      Number.isFinite(Number(latest.stepper_dro_valid_frame_count)) &&
      Number(latest.stepper_dro_valid_frame_count) > 0;
    setText(els.stepperDroState, !droCapable
      ? "DRO unavailable"
      : !droHasSample
        ? "Waiting for data"
        : latest.stepper_dro_fresh === true
          ? `Fresh · ${numberValue(latest, "stepper_dro_sample_age_ms", 0)} ms`
          : `STALE · ${numberValue(latest, "stepper_dro_sample_age_ms", 0)} ms`);
    setText(els.stepperDroPosition, droHasSample
      ? `${numberValue(latest, "stepper_dro_position_mm", 2)} mm`
      : "--");
    setText(els.stepperDroDisplacement, droHasSample
      ? `${Number(latest.stepper_dro_displacement_mm) > 0 ? "+" : ""}${numberValue(latest, "stepper_dro_displacement_mm", 2)} mm`
      : "--");
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
    els.stepperManualDirection.title = `${manualDirection} / ${d5Raw}`;
    if (latest.stepper_control_mode) {
      if (!controlModeDirty) {
        els.stepperModeWeb.checked = webPositionMode;
        els.stepperModeLocal.checked = !webPositionMode;
      } else if (els.stepperModeWeb.checked === webPositionMode) {
        controlModeDirty = false;
      }
    }
    const directionCalibrationSafe = latest.stepper_direction_calibration_safe === true;
    setText(els.stepperDirectionStatus, directionCalibrationSafe
      ? "Normal (Forward → D6; Reverse → D8)"
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
    setText(els.stepperLimitFilter, latest.stepper_limit_filter_capable === true
      ? `${numberValue(latest, "stepper_limit_qualification_ms", 0)} ms qualification; rejected D6=${latest.stepper_positive_limit_glitch_count ?? "--"}, D8=${latest.stepper_negative_limit_glitch_count ?? "--"}`
      : "Unavailable — firmware update required");
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
    if (!messageSticky && ["usb", "network"].includes(latest.stepper_mode)) {
      const transportLabel = latest.stepper_mode === "network" ? "LAN" : "USB";
      setText(els.stepperMessage, !directionCalibrationSafe
        ? `${transportLabel} unsafe legacy direction mapping; upload fixed-direction firmware before motion`
        : commandCapable
          ? webPositionMode
            ? "Web Position ready; D4 arms, D5 selects direction, and D6/D8 stop travel"
            : "Local Velocity: D4 runs/stops and D5 selects direction"
          : latest.stepper_speed_command_capable
            ? `${transportLabel} speed tuning ready; upload position-capable firmware for Home and Move`
            : `${transportLabel} diagnostics only; upload T4B firmware for speed tuning`);
    }
    updateControls();
  }

  async function requestMove() {
    if (els.stepperMove.hidden || els.stepperMove.disabled || motionRequestPending !== false) return false;
    const latest = getLatest();
    const distance = Number(els.stepperDistance.value);
    const speed = Number(els.stepperSpeed.value);
    const selectedDirection = latest?.stepper_authorized_direction === "both"
      ? "forward"
      : latest?.stepper_authorized_direction;
    if (selectedDirection !== "forward" && selectedDirection !== "reverse") {
      setText(els.stepperMessage, "Rejected: D5 direction is unavailable");
      return false;
    }
    const confirmAt = UI_CONFIG.moveConfirmation;
    if ((Math.abs(distance) > confirmAt.distanceMm || speed > confirmAt.speedMmS) &&
        !window.confirm(`Confirm ${selectedDirection} move: ${distance} mm at ${speed} mm/s?`)) return false;
    const body = {distance_mm: distance, speed_mm_s: speed};
    const commandId = els.stepperCommandInput.value.trim();
    if (commandId) body.command_id = commandId;
    motionRequestPending = "move";
    updateControls();
    try {
      const payload = await postJson(API.stepperMove, body);
      setText(els.stepperMessage, `${payload.resolved_direction || selectedDirection} move accepted`);
      if (payload.sample) applySample(payload.sample);
    } catch (error) {
      setText(els.stepperMessage, `Rejected: ${error.message}`);
    } finally {
      if (motionRequestPending === "move") motionRequestPending = false;
      updateControls();
    }
    return true;
  }

  async function requestStop() {
    if (els.stepperStop.hidden || els.stepperStop.disabled || motionRequestPending === "stop") return false;
    motionRequestPending = "stop";
    updateControls();
    try {
      const payload = await postJson(API.stepperStop);
      setText(els.stepperMessage, "Motion stopped");
      if (payload.sample) applySample(payload.sample);
    } catch (error) {
      setText(els.stepperMessage, `Stop failed: ${error.message}`);
    } finally {
      if (motionRequestPending === "stop") motionRequestPending = false;
      updateControls();
    }
    return true;
  }

  function handleSpaceShortcut() {
    const latest = getLatest();
    if (latest?.stepper_control_mode !== "web_position") return false;
    const moving = latest?.stepper_moving === true;
    if ((moving && motionRequestPending === "stop") ||
        (!moving && motionRequestPending !== false)) return false;
    const actionButton = moving ? els.stepperStop : els.stepperMove;
    if (!actionButton || actionButton.hidden || actionButton.disabled) return false;
    if (moving) void requestStop();
    else void requestMove();
    return true;
  }

  els.emergencyStop.addEventListener("click", async () => {
    // E-STOP remains a single action; confirmation is only for reset.
    try {
      const payload = await postJson(API.stepperEstop);
      if (payload.sample) applySample(payload.sample);
      messageSticky = true;
      setText(els.stepperMessage, "E-STOP latched; step pulses inhibited");
    } catch (error) {
      messageSticky = true;
      setText(els.stepperMessage, `E-STOP failed: ${error.message}`);
    }
  });
  els.emergencyReset.addEventListener("click", async () => {
    if (!window.confirm("Reset the E-STOP latch? Physical D4 must be OFF. This re-enables motion commands but does not start motion.")) return;
    try {
      const payload = await postJson(API.stepperEstopReset);
      if (payload.sample) applySample(payload.sample);
      messageSticky = true;
      setText(els.stepperMessage, "E-STOP reset; motion remains stopped");
    } catch (error) {
      messageSticky = true;
      setText(els.stepperMessage, `E-STOP reset rejected: ${error.message}`);
    }
  });
  els.stepperForm.addEventListener("input", updateControls);
  els.stepperSpeed.addEventListener("input", () => { messageSticky = false; });
  async function requestControlMode() {
    const latest = getLatest();
    const webPosition = els.stepperModeWeb.checked;
    const previousWebPosition = latest?.stepper_control_mode === "web_position";
    controlModeDirty = true;
    controlModeRequestPending = true;
    messageSticky = true;
    setText(els.stepperMessage, `Selecting ${webPosition ? "Web Position" : "Local Velocity"}…`);
    updateControls();
    try {
      const payload = await postJson(API.stepperControlMode, {web_position: webPosition});
      if (payload.sample) applySample(payload.sample);
      const confirmedMode = payload.stepper?.stepper_control_mode || payload.sample?.stepper_control_mode;
      if (payload.confirmed !== true || confirmedMode !== (webPosition ? "web_position" : "local_velocity")) {
        throw new Error("the Yún did not return the requested control mode");
      }
      setText(els.stepperMessage, webPosition
        ? "Web Position selected; D5 chooses travel direction; D6/D8 stop travel"
        : "Local Velocity selected; D4 runs/stops and D5 selects direction");
    } catch (error) {
      controlModeDirty = false;
      els.stepperModeWeb.checked = previousWebPosition;
      els.stepperModeLocal.checked = !previousWebPosition;
      setText(els.stepperMessage, `Mode rejected: ${error.message}`);
    } finally {
      controlModeRequestPending = false;
      updateControls();
    }
  }
  for (const input of modeInputs) {
    input.addEventListener("change", () => {
      if (input.checked) void requestControlMode();
    });
  }
  els.stepperForm.addEventListener("submit", event => {
    event.preventDefault();
    void requestMove();
  });
  els.stepperStop.addEventListener("click", () => void requestStop());
  els.stepperHome.addEventListener("click", async () => {
    if (!window.confirm(`Move toward D8 until its limit switch activates? Speed is fixed at ${limits.home_speed_mm_s} mm/s; D4 must be armed and D5 set to Reverse.`)) return;
    messageSticky = true;
    setText(els.stepperMessage, "Moving toward the D8 limit…");
    try {
      const payload = await postJson(API.stepperHome);
      if (payload.sample) applySample(payload.sample);
      setText(els.stepperMessage, payload.stepper?.stepper_negative_limit_active
        ? "D8 limit reached"
        : `D8-limit move accepted at ${limits.home_speed_mm_s} mm/s`);
    } catch (error) {
      setText(els.stepperMessage, `D8-limit move rejected: ${error.message}`);
    }
  });
  els.stepperApplySpeed.addEventListener("click", async () => {
    const speed = Number(els.stepperSpeed.value);
    if (!Number.isFinite(speed) ||
        speed < limits.min_speed_mm_s || speed > limits.max_speed_mm_s) {
      messageSticky = true;
      setText(els.stepperMessage, `Speed rejected: enter ${limits.min_speed_mm_s} through ${limits.max_speed_mm_s} mm/s`);
      return;
    }
    speedRequestPending = true;
    messageSticky = true;
    els.stepperApplySpeed.textContent = "Applying…";
    setText(els.stepperMessage, `Sending ${speed.toFixed(1)} mm/s; waiting for Yún confirmation…`);
    updateControls();
    try {
      const payload = await postJson(API.stepperSpeed, {speed_mm_s: speed});
      if (payload.sample) applySample(payload.sample);
      const confirmedSpeed = Number(payload.sample?.stepper_command_speed_mm_s);
      if (payload.confirmed !== true || !Number.isFinite(confirmedSpeed)) {
        throw new Error("the Yún did not return a confirmed configured speed");
      }
      setText(els.stepperMessage, `Confirmed by Yún: ${confirmedSpeed.toFixed(1)} mm/s`);
    } catch (error) {
      setText(els.stepperMessage, `Speed rejected: ${error.message}`);
    } finally {
      speedRequestPending = false;
      els.stepperApplySpeed.textContent = "Apply Local Velocity Speed";
      updateControls();
    }
  });

  return {render, handleSpaceShortcut};
}
