const check = (value, message) => { if (!value) throw new Error(message); };
class Element {
  constructor() {
    this.value = ""; this.dataset = {}; this.children = []; this.nodes = new Map();
    this.style = {setProperty() {}};
    this.classList = {toggle() {}, add() {}, remove() {}};
  }
  addEventListener(name, handler) {
    this.listeners ??= {};
    (this.listeners[name] ??= []).push(handler);
  }
  blur() { this.blurred = true; }
  setAttribute() {}
  removeAttribute() {}
  closest() { return this; }
  insertAdjacentElement() {}
  querySelector(selector) {
    if (!this.nodes.has(selector)) this.nodes.set(selector, new Element());
    return this.nodes.get(selector);
  }
  querySelectorAll(selector) {
    const kind = selector.includes("servo") ? "servo" : "can";
    return [...new Set([...this.innerHTML.matchAll(new RegExp(`data-${kind}="([^"]+)"`, "g"))].map(m => m[1]))]
      .map(name => { const element = this.querySelector(name); element.dataset[kind] = name; return element; });
  }
}
const nodes = new Map();
globalThis.document = {
  body: new Element(),
  querySelector() { return null; },
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
  createElement() { return new Element(); },
};
globalThis.UI_CONFIG = {stepperInputs: {distanceStepMm: .1, speedStepMmS: .1, defaultDistanceMm: 1}};
globalThis.addEventListener = () => {};
const {createStepperComponent, shortcutTargetIsGuarded} = await import("../dashboard_app/static/app-lean.js");
let sample = {stepper_mode: "controllino", stepper_connected: true,
  stepper_control_mode: "web_position", stepper_command_capable: true,
  stepper_direction_calibration_safe: true, stepper_mode_command_capable: true, stepper_local_enabled: false,
  stepper_d4_raw: "HIGH", stepper_moving: false,
  stepper_home_capable: true, stepper_authorized_direction: "both",
  stepper_positive_limit_active: false, stepper_negative_limit_active: true};
const component = createStepperComponent({getLatest: () => sample, applySample() {},
  limits: {min_distance_mm: .01, max_distance_mm: 1000, max_travel_mm: 277,
    min_speed_mm_s: .1, max_speed_mm_s: 10, default_speed_mm_s: 1.5}});
nodes.get("stepperDirectionForward").checked = true;
nodes.get("stepperDirectionReverse").checked = false;
component.render(sample);
check(!nodes.get("stepperHome").disabled, "Home allows an already-active bottom switch without D4 arming");
check(!nodes.get("stepperMove").disabled, "Controllino may move up from bottom without D4 arming");
nodes.get("stepperDirectionReverse").checked = true;
component.render(sample);
check(nodes.get("stepperMove").disabled, "Bottom limit still blocks down");
nodes.get("stepperDirectionReverse").checked = false;
const baseline = sample;
for (const changes of [{stepper_estop_latched: true}, {stepper_moving: true},
                        {stepper_connected: false}, {stepper_positive_limit_active: true}]) {
  sample = {...baseline, ...changes};
  component.render(sample);
  check(nodes.get("stepperMove").disabled, "Safety and busy checks remain active");
  if (!changes.stepper_positive_limit_active) check(nodes.get("stepperHome").disabled, "Home retains safety and busy checks");
}
sample = {...baseline, stepper_mode: "usb", stepper_authorized_direction: "forward"};
component.render(sample);
check(nodes.get("stepperMove").disabled, "Legacy Yún still requires D4 arming");
print("PASS: positional Move eligibility and retained safety checks");

for (const id of ["stepperModeWeb", "stepperModeLocal", "stepperDirectionForward",
                  "stepperDirectionReverse", "stepperMove", "stepperStop", "stepperHome"]) {
  const element = nodes.get(id);
  const releaseFocus = element.listeners.click.find(handler => handler.toString().includes("input.blur"));
  releaseFocus({detail: 0});
  check(!element.blurred, "Keyboard activation retains native focus");
  releaseFocus({detail: 1});
  check(element.blurred, "Pointer activation releases focus for Space start/stop");
}
check(!shortcutTargetIsGuarded({target: {tagName: "BODY"}}), "Page focus permits shortcuts");
for (const tagName of ["INPUT", "BUTTON", "TEXTAREA", "SELECT", "A"]) {
  check(shortcutTargetIsGuarded({target: {tagName}}), "Focused controls retain native keyboard behavior");
}
check(shortcutTargetIsGuarded({repeat: true, target: {tagName: "BODY"}}), "Held Space must not repeat motion commands");

// Mode selection follows confirmed telemetry, including a lost HTTP response.
const flush = async () => { for (let i=0; i<30; i++) await Promise.resolve(); };
const reply = (ok, data) => ({ok, text: async () => JSON.stringify(data)});
const local = {...baseline, stepper_control_mode: "local_velocity", stepper_status_sequence: 20};
sample = local;
component.render(sample);
let resolveRequest;
globalThis.fetch = async () => new Promise(resolve => { resolveRequest = resolve; });
nodes.get("stepperModeWeb").checked = true;
nodes.get("stepperModeLocal").checked = false;
nodes.get("stepperModeWeb").listeners.change[0]();
check(nodes.get("stepperModeLocal").checked && !nodes.get("stepperModeWeb").checked,
      "Clicking Positional must leave Directional selected pending confirmation");
resolveRequest(reply(false, {error: "acknowledgement timed out", outcome: "unconfirmed"}));
await flush();
check(nodes.get("stepperCommandFeedback").hidden, "An uncertain mode response must not show a red failure");
check(nodes.get("stepperModeLocal").checked, "A timeout cannot select the requested mode");
sample = {...local, stepper_connected: false, stepper_control_mode: "web_position", stepper_status_sequence: 21};
component.render(sample);
check(nodes.get("stepperModeLocal").checked, "Disconnected telemetry cannot confirm a mode change");
sample = {...local, stepper_control_mode: "web_position", stepper_status_sequence: 22};
component.render(sample);
check(nodes.get("stepperModeWeb").checked && !nodes.get("stepperModeLocal").checked,
      "Fresh actual-state telemetry selects Positional");
check(nodes.get("stepperCommandFeedback").hidden,
      "Late confirmation changes the selector without a failure banner");

// A confirmation arriving during the request wins over its eventual timeout.
sample = {...local, stepper_status_sequence: 30}; component.render(sample);
globalThis.fetch = async () => new Promise(resolve => { resolveRequest = resolve; });
nodes.get("stepperModeWeb").checked = true; nodes.get("stepperModeLocal").checked = false;
nodes.get("stepperModeWeb").listeners.change[0]();
sample = {...local, stepper_control_mode: "web_position", stepper_status_sequence: 31}; component.render(sample);
resolveRequest(reply(false, {error: "response lost", outcome: "unconfirmed"})); await flush();
check(nodes.get("stepperModeWeb").checked && nodes.get("stepperCommandFeedback").hidden,
      "A late HTTP timeout cannot undo confirmed state");

// Explicit controller rejection remains visible and preserves actual selection.
sample = {...local, stepper_status_sequence: 40}; component.render(sample);
globalThis.fetch = async () => reply(false, {error: "controller rejected: busy"});
nodes.get("stepperModeWeb").checked = true; nodes.get("stepperModeLocal").checked = false;
nodes.get("stepperModeWeb").listeners.change[0](); await flush();
check(nodes.get("stepperModeLocal").checked && !nodes.get("stepperModeWeb").checked,
      "An explicit rejection must preserve confirmed mode");
check(!nodes.get("stepperCommandFeedback").hidden &&
      nodes.get("stepperCommandFeedback").textContent.includes("rejected"), "Explicit rejection is reported");
print("PASS: mode selection, uncertain acknowledgement, late confirmation, and rejection");

// A verified HTTP response can confirm the mode before the next stream update.
sample = {...local, stepper_status_sequence: 50}; component.render(sample);
globalThis.fetch = async () => reply(true, {confirmed: true, stepper:
  {...sample, stepper_control_mode: "web_position", stepper_status_sequence: 51}});
nodes.get("stepperModeWeb").checked = true; nodes.get("stepperModeLocal").checked = false;
nodes.get("stepperModeWeb").listeners.change[0](); await flush();
check(nodes.get("stepperModeWeb").checked && nodes.get("stepperCommandFeedback").hidden,
      "A confirmed response selects the actual mode quietly");
