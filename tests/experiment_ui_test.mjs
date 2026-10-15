// Run with `gjs -m tests/experiment_ui_test.mjs`. DOM and HTTP stay in memory.
const check = (condition, message) => {
  if (!condition) throw new Error(message);
};
class Element {
  constructor() {
    this.value = "";
    this.dataset = {};
    this.children = [];
    this.listeners = {};
    this.textContent = "";
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  querySelector(name) { return this.sections[name]; }
  click() { if (!this.disabled) this.listeners.click(); }
}
const ids = ["experimentStatus", "experimentFile", "experimentCsv", "experimentExample",
  "experimentLoad", "experimentStart", "experimentPreview", "experimentFeedback", "experimentChecks"];
const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
elements.experimentPreview.sections = {thead: new Element(), tbody: new Element()};
globalThis.document = {
  getElementById: id => elements[id], createElement: () => new Element(),
  querySelector: () => null, // Prevent dashboard startup and device I/O.
};
const {createExperimentComponent, stepperLimitBlocks, stepperInterlockPresentation, API} =
  await import("../dashboard_app/static/app-lean.js");
for (const [side, blocked, retreat] of [["negative", "reverse", "forward"], ["positive", "forward", "reverse"]]) {
  const latest = {stepper_mode: "controllino", [`stepper_${side}_limit_active`]: true};
  check(stepperLimitBlocks(latest, blocked), `${side} limit must block ${blocked}`);
  check(!stepperLimitBlocks(latest, retreat), `${side} limit must allow retreat ${retreat}`);
}
check(stepperLimitBlocks({stepper_mode: "usb", stepper_positive_limit_active: true}, "forward"),
  "Legacy Yún keeps its Forward/positive mapping");
check(!stepperLimitBlocks({stepper_mode: "controllino"}, "forward"), "Absent legacy inputs stay unavailable");

// A previous LOW must never block a currently clear Controllino input.
const photographed = {stepper_mode: "controllino", stepper_connected: true,
  stepper_positive_limit_input: "DI1 / A13", stepper_negative_limit_input: "DI0 / A12",
  stepper_d6_raw: "LOW", stepper_d8_raw: "HIGH",
  stepper_positive_limit_active: true, stepper_negative_limit_active: false,
  stepper_positive_limit_latched: true, stepper_negative_limit_latched: true,
  stepper_blocked: false, stepper_blocked_reason: "boot"};
const photographedDisplay = stepperInterlockPresentation(photographed);
check(photographedDisplay.forward.blocked && !photographedDisplay.reverse.blocked,
  "Only the currently LOW top input may block motion; down remains clear");
check(photographedDisplay.summary.includes("Reverse/down clear"),
  "Previously latched flags must never block retreat past a HIGH input");
check(photographedDisplay.reverse.text.includes("HIGH") && photographedDisplay.reverse.text.includes("clear"),
  "A HIGH input must be displayed as clear, without a remembered trip");
check(photographedDisplay.forward.label.includes("Top") && photographedDisplay.forward.label.includes("DI1"),
  "The top limit must identify its physical terminal");
const retreatDisplay = stepperInterlockPresentation({...photographed, stepper_negative_limit_latched: false});
check(retreatDisplay.summary.includes("Reverse/down clear") && !retreatDisplay.reverse.blocked,
  "One active endpoint must show that retreat is clear");
const clearDisplay = stepperInterlockPresentation({...photographed, stepper_positive_limit_active: false,
  stepper_positive_limit_latched: false, stepper_negative_limit_latched: false, stepper_d6_raw: "HIGH"});
check(clearDisplay.summary === "Limits clear in both directions", "Released limits must show no directional block");
check(stepperInterlockPresentation({...photographed, stepper_connected: false}).summary === "Controller offline",
  "Cached inputs must not claim permission while disconnected");
check(stepperInterlockPresentation({...photographed, stepper_estop_latched: true}).summary.includes("E-stop"),
  "E-stop must remain visible regardless of switch states");

const component = createExperimentComponent();
const base = {program_id: 7, name: "record_motor_solenoids.csv", columns: ["action", "source", "device"],
  program: [{action: "requested_state", source: "controllino", device: "can_motor"}], monitors: {}};
const state = (revision, phase, checks = {}) => ({...base, revision, state: phase,
  current_row: phase === "running" ? 1 : null,
  results: [{row: 1, status: phase === "completed" ? "passed" : "waiting", checks}]});
const accepted = {ok: true, before_sequence: 10, observed_sequence: 11,
  evidence: "firmware_command_counter"};
component.render(state(1, "running"));
check(elements.experimentStart.disabled, "Start must be disabled during execution");
component.render(state(2, "completed", {
  requested_state: {ok: true, requested: 0, observed: 0}, command_accepted: accepted,
}));
check(!elements.experimentStart.disabled, "Completed motor feedback must re-enable Start");
check(elements.experimentChecks.children.length === 2, "Completed motor checks must render");

const requests = [];
globalThis.fetch = async (url, options) => {
  check(url === API.experimentStart && options.method === "POST", "Only explicit Start may be sent");
  requests.push(JSON.parse(options.body));
  return {ok: true, text: async () => JSON.stringify({experiment: state(3, "running")})};
};
check(requests.length === 0, "Completion must never automatically restart");
elements.experimentStart.click();
for (let index = 0; index < 20; index++) await Promise.resolve();
check(requests.length === 1 && requests[0].program_id === base.program_id,
  "A second Start must use the same loaded spreadsheet without reloading");
check(elements.experimentStart.disabled, "Start must be disabled while the second run executes");
component.render(state(4, "running", {
  command_accepted: accepted,
  manual_state: {ok: true, expected_token: 42, observed_token: 42, active: true},
}));
component.render({...state(5, "running", {wait: {ok: false, elapsed_s: 2, duration_s: 5}}),
  program: [{action: "wait", source: "", device: ""}]});
check(elements.experimentChecks.children[0].textContent.includes("2"), "Wait progress must render");
component.render(state(6, "completed", {command_accepted: accepted}));
check(!elements.experimentStart.disabled, "Start must re-enable after a second completion");
elements.experimentCsv.listeners.input();
check(elements.experimentStart.disabled, "Unloaded CSV edits must still block Start");
print("PASS: motor confirmation, wait progress, completion, and explicit restart UI checks");
