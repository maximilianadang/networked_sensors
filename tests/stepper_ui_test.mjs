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
  stepper_direction_calibration_safe: true, stepper_local_enabled: false,
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
