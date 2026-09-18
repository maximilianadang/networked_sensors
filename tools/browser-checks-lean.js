// Browser-only regression checks. Served by check_dashboard-lean.py, never production.
export async function checkPanels(config, initial, history) {
  const {createStepperComponent} = await import("/assets/components/stepper-lean.js");
  const {createMetadataComponent} = await import("/assets/components/metadata-lean.js");
  const {createToolbarComponent} = await import("/assets/components/toolbar-lean.js");
  const {createActions} = await import("/assets/api-lean.js");
  const {renderMetrics} = await import("/assets/components/metrics.js");
  const {renderSources} = await import("/assets/components/sources.js");
  const {drawAllCharts} = await import("/assets/charts.js");
  const results = [];
  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
    results.push(message);
  };
  const el = id => document.getElementById(id);
  const tick = () => new Promise(resolve => setTimeout(resolve, 30));
  const state = {latest: {...initial, stepper_mode: "controllino",
    stepper_connected: true, stepper_control_mode: "local_velocity",
    stepper_direction_calibration_safe: true, stepper_mode_command_capable: true,
    stepper_command_capable: true, stepper_speed_command_capable: true,
    stepper_estop_capable: true, stepper_estop_latched: false,
    stepper_moving: false, stepper_d4_raw: "HIGH", stepper_local_enabled: false,
    stepper_brushless_motor_capable: true, stepper_brushless_motor_variable_capable: true,
    stepper_home_capable: false, stepper_dro_capable: false},
    run: {esp32_source: "sim", recording: false}};
  let stepper;
  const applySample = sample => { state.latest = sample; stepper.render(sample); };
  const limits = {...config.stepper, min_distance_mm: .01};
  stepper = createStepperComponent({getLatest: () => state.latest, applySample, limits});
  window.fixture = {state, stepper, applySample};
  stepper.render(state.latest);
  let calls = [];
  let fail = false;
  let release;
  let delayed = false;
  window.confirm = () => true;
  window.fetch = async (url, options = {}) => {
    const body = JSON.parse(options.body || "{}");
    calls.push({url, body});
    if (delayed) await new Promise(resolve => { release = resolve; });
    if (fail) return new Response(JSON.stringify({error: "test rejection"}), {status: 409});
    const sample = {...state.latest};
    if (url.endsWith("/control-mode")) {
      sample.stepper_control_mode = body.web_position ? "web_position" : "local_velocity";
      sample.stepper_local_enabled = body.web_position;
    }
    if (url.endsWith("/local-run")) sample.stepper_moving = body.direction !== 0;
    if (url.endsWith("/move")) sample.stepper_moving = true;
    if (url.endsWith("/stop")) sample.stepper_moving = false;
    if (url.endsWith("/speed")) sample.stepper_command_speed_mm_s = body.speed_mm_s;
    if (url.endsWith("/estop")) sample.stepper_estop_latched = true;
    if (url.endsWith("/reset")) sample.stepper_estop_latched = false;
    return new Response(JSON.stringify({
      confirmed: true, sample, stepper: sample, on: true, setpoint_us: body.pulse_us,
      pulse_us: body.pulse_us || 1200, metadata: body,
      zero: {set: true, motion_commanded: false, raw_position_mm: 42},
      run: {esp32_source: "sim", recording: url.endsWith("/start")}
    }));
  };
  const click = async id => { el(id).click(); await tick(); };
  await click("stepperRunReverse");
  assert(calls.at(-1).body.direction === -1 && state.latest.stepper_moving, "Reverse sends signed local run");
  await click("stepperRunStop");
  assert(!state.latest.stepper_moving, "Local Stop updates confirmed state");
  fail = true;
  await click("stepperRunForward");
  assert(!el("stepperRunForward").disabled && !el("stepperCommandFeedback").hidden,
    "Rejected run exposes error and releases pending state");
  fail = false;
  await click("stepperModeWeb");
  assert(state.latest.stepper_control_mode === "web_position" && !el("stepperMove").disabled,
    "Web Position mode confirmed and Move enabled");
  el("stepperDirectionReverse").click();
  await click("stepperMove");
  assert(calls.at(-1).body.direction === "reverse" && state.latest.stepper_moving,
    "Web move preserves software direction");
  assert(stepper.handleSpaceShortcut(), "Space chooses Stop during motion");
  await tick();
  assert(!state.latest.stepper_moving, "Space Stop confirmed");
  fail = true;
  await click("stepperMove");
  assert(!el("stepperMove").disabled, "Rejected move can retry without editing position");
  fail = false;
  assert(el("stepperHome").disabled, "Unavailable homing stays disabled");
  await click("brushlessMotorToggle");
  assert(calls.at(-1).url.endsWith("/motor/toggle"), "Brushless toggle retained");
  el("brushlessPulseWidth").value = "1200";
  el("brushlessPulseWidth").dispatchEvent(new Event("input", {bubbles:true}));
  await click("brushlessApplyPulse");
  assert(calls.at(-1).body.pulse_us === 1200, "Brushless pulse setpoint retained");
  await click("emergencyStop");
  assert(state.latest.stepper_estop_latched, "E-STOP confirms latch");
  await click("emergencyReset");
  assert(!state.latest.stepper_estop_latched, "Reset confirms stopped latch release");
  applySample({...state.latest, stepper_dro_capable: true, stepper_dro_fresh: true,
    stepper_dro_position_mm: 42, stepper_moving: false});
  await click("stepperSetDroZero");
  assert(el("stepperDroZeroStatus").textContent.includes("42.00"), "Display-only zero retained");

  // Delayed command does not monopolize STOP or E-STOP; duplicate same-key sends are suppressed.
  const actions = createActions();
  let unblock;
  const pending = actions.run("move", () => new Promise(resolve => { unblock = resolve; }));
  assert(await actions.run("move", () => { throw Error("duplicate"); }) === false,
    "Duplicate in-flight command suppressed");
  let stopped = false;
  await actions.run("stop", async () => { stopped = true; });
  assert(stopped && actions.has("move"), "Stop independent of pending Move");
  unblock();
  await pending;
  assert(!actions.has("move"), "Pending state cleaned after success");

  const metadata = createMetadataComponent({geometry: config.geometry, stepperLimits: limits,
    applyMotionPlan: stepper.applyMotionPlan, onPowderFlowChange: () => {}});
  const form = el("metadataForm");
  function input(name, value) {
    const field = form.elements.namedItem(name);
    field.value = value;
    field.dispatchEvent(new Event("input", {bubbles: true}));
  }
  input("powder_flow_rate_g_per_s", String(config.geometry.powder_mass_per_stepper_travel_g_per_mm));
  input("test_duration_s", "10");
  assert(Number(el("stepperSpeed").value) === 1 && Number(el("stepperDistance").value) === 10,
    "Powder flow and duration derive speed and travel");
  delayed = true;
  form.dispatchEvent(new Event("submit", {cancelable: true}));
  await tick();
  input("notes", "edited during save");
  delayed = false;
  release();
  await tick();
  assert(form.elements.namedItem("notes").value === "edited during save" &&
    el("metadataStatus").textContent === "Unsaved", "Saving does not overwrite newer metadata edits");
  fail = true;
  form.dispatchEvent(new Event("submit", {cancelable: true}));
  await tick();
  assert(el("metadataStatus").textContent.includes("Save failed"), "Metadata failure visible");
  fail = false;

  const toolbar = createToolbarComponent({getState: () => state, applySample,
    setRunState: run => { state.run = run; toolbar.renderRun(run); },
    setEspTransportMessage: text => { window.toolbarError = text; }, solenoidCount: 4});
  toolbar.renderRun(state.run);
  await click("startRun");
  assert(state.run.recording && el("startRun").disabled, "Recording start retained");
  await click("stopRun");
  assert(!state.run.recording, "Recording stop retained");
  fail = true;
  await click("startRun");
  assert(window.toolbarError.includes("test rejection") && !el("startRun").disabled,
    "Recording failure reported and retry available");
  fail = false;
  await click("sol0");
  assert(calls.at(-1).url.endsWith("toggle?n=0"), "Solenoid routing retained");
  window.panelResults = results;
  window.fixture.paint = () => {
    toolbar.renderSample(state.latest);
    renderMetrics(state.latest, metadata.powderFlowRateGPerS());
    renderSources(state.latest, history.length);
    drawAllCharts(history);
    el("streamStatus").textContent = "Browser test fixture";
  };
  return results;
}
