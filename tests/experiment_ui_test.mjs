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
const ids = ["experimentStatus", "experimentFile", "experimentCsv", "experimentFailSafeFile", "experimentFailSafeCsv", "experimentExample",
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
const base = {program_id: 7, name: "record_motor_solenoids.csv", csv_input_fields: ["csv", "fail_safe_csv"], columns: ["action", "source", "device"],
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

const twoSections = createExperimentComponent();
const mainFailure = {row: 1, sequence: "main", status: "failed", reason: "reading_check_failed",
  checks: {pressure: {ok: false, observed: 11, minimum: 0, maximum: 10, unit: "mbar", terminal: "AI10"}}};
const dual = {program_id: 8, revision: 1, name: "two-sections.csv", state: "running", sequence: "fail_safe",
  csv_input_fields: ["csv", "fail_safe_csv"],
  current_row: 3, columns: ["action", "source", "device", "set_point", "min_mbar", "max_mbar"],
  program: [{action: "reading_state", source: "controllino", device: "AI10", min_mbar: 0, max_mbar: 10}],
  fail_safe_program: [
    {action: "requested_state", source: "controllino", device: "solenoid1", set_point: "closed"},
    {action: "requested_state", source: "controllino", device: "stepper", set_point: "halt"},
    {action: "requested_state", source: "controllino", device: "can_motor", set_point: "speed"}],
  failure: mainFailure, monitors: {}, results: [mainFailure,
    {row: 1, sequence: "fail_safe", status: "failed", reason: "request_failed", error: "connection reset", checks: {}},
    {row: 2, sequence: "fail_safe", status: "passed", checks: {}},
    {row: 3, sequence: "fail_safe", status: "waiting", checks: {}}]};
twoSections.render(dual);
const table = elements.experimentPreview.sections;
check(table.thead.children[0].children.map(cell => cell.textContent).join("|") === "Main sequence|Fail safe",
  "Both sections must be labeled side by side");
check(table.tbody.children.length === 3, "Sections with different lengths must retain every action");
check(!elements.experimentCsv.value.includes("fail_safe_") && !elements.experimentFailSafeCsv.value.includes("fail_safe_"),
  "Each CSV editor must use ordinary column names");
check(elements.experimentCsv.value.split("\n").filter(Boolean).length === 2,
  "Main CSV must contain only main actions without padding");
check(elements.experimentFailSafeCsv.value.split("\n").filter(Boolean).length === 4,
  "Fail-safe CSV must contain all of its own rows, independently of main length");
check(elements.experimentStatus.textContent.includes("Fail safe") && elements.experimentStatus.textContent.includes("3"),
  "Status must identify the current row in the active section");
check(elements.experimentStart.disabled, "Start must remain disabled throughout fail safe");
check(elements.experimentCsv.disabled && elements.experimentFailSafeCsv.disabled
  && elements.experimentFile.disabled && elements.experimentFailSafeFile.disabled,
  "Both editors and file selectors must be locked during execution");
twoSections.render({...dual, revision: 2, state: "failed", current_row: null,
  results: dual.results.map(result => result.status === "waiting" ? {...result, status: "passed"} : result)});
check(!elements.experimentStart.disabled, "A finished fail-safe sequence may be explicitly restarted");
check(elements.experimentFeedback.textContent.includes("Main row 1: reading_check_failed"),
  "Final status must retain the original failure even when the final shutdown action passed");
check(!elements.experimentFeedback.textContent.includes("undefined"), "Failure details must remain meaningful");
check(elements.experimentChecks.children.some(item => item.textContent.includes("Fail safe row 1: request_failed")),
  "Each fail-safe failure must remain visible after later actions pass");
check(elements.experimentChecks.children.some(item => item.textContent.includes("Main failure / pressure: Fail")
  && item.textContent.includes("11 mbar") && item.textContent.includes("0–10 mbar")),
  "The triggering measurement and its bounds must remain visible after fail safe finishes");
const offset = table.thead.children[0].children[0].colSpan;
check(table.tbody.children[2].children[offset].dataset.status === "passed",
  "Fail-safe cell status must be rendered independently of the main section");

const flush = async () => { for (let index = 0; index < 30; index++) await Promise.resolve(); };
const mainDraft = elements.experimentCsv.value;
const failSafeDraft = elements.experimentFailSafeCsv.value + "\n";
elements.experimentFailSafeCsv.value = failSafeDraft;
elements.experimentFailSafeCsv.listeners.input();
check(elements.experimentStart.disabled, "Editing fail safe must require validation again before Start");
twoSections.render({...dual, revision: 3, state: "failed", current_row: null});
check(elements.experimentFailSafeCsv.value === failSafeDraft && elements.experimentCsv.value === mainDraft,
  "Telemetry updates must preserve both unsaved drafts");
globalThis.fetch = async (url, options) => {
  check(url === API.experimentLoad, "Loading both fields must use the existing load endpoint");
  const payload = JSON.parse(options.body);
  check(payload.csv === mainDraft && payload.fail_safe_csv === failSafeDraft,
    "Load must send both independent CSV fields for atomic validation");
  return {ok: true, text: async () => JSON.stringify({experiment: {...dual, program_id: 9, revision: 4, state: "ready",
    sequence: "main", failure: null, current_row: null, results: []}})};
};
elements.experimentLoad.click();
await flush();
check(!elements.experimentStart.disabled, "Successful validation of both fields re-enables Start");

const importedFailSafe = "action,source,max_age_ms,health,timeout_s,duration_s\nwait,,,,,1\n";
const unchangedMain = elements.experimentCsv.value;
elements.experimentFailSafeFile.files = [{name: "shutdown.csv", size: importedFailSafe.length,
  text: async () => importedFailSafe}];
elements.experimentFailSafeFile.listeners.change();
await flush();
check(elements.experimentFailSafeCsv.value === importedFailSafe && elements.experimentCsv.value === unchangedMain,
  "Importing fail safe must leave the main draft untouched");
const importedMain = importedFailSafe.replace(",1\n", ",2\n");
elements.experimentFile.files = [{name: "main.csv", size: importedMain.length, text: async () => importedMain}];
elements.experimentFile.listeners.change();
await flush();
check(elements.experimentCsv.value === importedMain && elements.experimentFailSafeCsv.value === importedFailSafe,
  "Importing main must leave the fail-safe draft untouched");
check(elements.experimentStart.disabled, "Imported CSVs must be validated before Start");

const exampleRequests = [];
globalThis.fetch = async url => {
  exampleRequests.push(url);
  return {ok: true, text: async () => url === API.experimentExample ? mainDraft : failSafeDraft};
};
elements.experimentExample.click();
await flush();
check(exampleRequests.length === 2 && exampleRequests.includes(API.experimentFailSafeExample),
  "Use example must retrieve both CSVs");
check(elements.experimentCsv.value === mainDraft && elements.experimentFailSafeCsv.value === failSafeDraft,
  "Use example must populate both editors independently");

const compatibility = createExperimentComponent();
const outdated = {...dual, revision: 1, state: "ready", sequence: "main", current_row: null, results: []};
delete outdated.csv_input_fields;
compatibility.render(outdated);
const preservedFailSafe = elements.experimentFailSafeCsv.value;
const preservedMain = elements.experimentCsv.value;
check(elements.experimentLoad.disabled && elements.experimentStart.disabled,
  "An older backend must block Load and Start before it can ignore fail_safe_csv");
check(elements.experimentFeedback.textContent.includes("Restart dashboard.py"),
  "Backend mismatch must explain the required restart");
globalThis.fetch = async () => { throw new Error("An incompatible backend must never receive a load"); };
elements.experimentLoad.click();
await flush();
check(elements.experimentFailSafeCsv.value === preservedFailSafe && elements.experimentCsv.value === preservedMain,
  "Blocked loading must preserve both drafts");

compatibility.render({...dual, revision: 2, state: "ready", sequence: "main", current_row: null, results: []});
check(!elements.experimentLoad.disabled, "A compatible backend must allow loading");
globalThis.fetch = async () => ({ok: true, text: async () => JSON.stringify({experiment: {
  ...outdated, revision: 3, program_id: 9, fail_safe_program: []
}})});
elements.experimentLoad.click();
await flush();
check(elements.experimentFailSafeCsv.value === preservedFailSafe && elements.experimentCsv.value === preservedMain,
  "An incompatible load response must never overwrite the drafted fail safe");
check(elements.experimentStart.disabled && elements.experimentFeedback.textContent.includes("Restart dashboard.py"),
  "An incompatible response must require restart and leave Start blocked");
print("PASS: motor confirmation, wait progress, completion, and explicit restart UI checks");
