// Run with gjs -m tests/thermocouple_ui_test.mjs; no device/network I/O.
const check = (condition, message) => { if (!condition) throw new Error(message); };
class Element {
  constructor() { this.children = []; this.listeners = {}; this.textContent = ""; this.value = ""; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  setAttribute(name, value) { this[name] = value; }
}
const nodes = Object.fromEntries(["thermocoupleForm", "thermocoupleReadings", "thermocoupleStatus", "thermocoupleEmpty", "thermocoupleSave"]
  .map(id => [id, new Element()]));
globalThis.document = {getElementById: id => nodes[id], createElement: () => new Element(), querySelector: () => null};
const {createThermocoupleComponent, API} = await import("../dashboard_app/static/app-lean.js");
const descriptors = Array.from({length: 8}, (_, i) => ({device: `tc${i}`, label: `TC-${i}`, component: i === 7 ? "Spare" : ""}));
const component = createThermocoupleComponent({descriptors});
const rows = nodes.thermocoupleReadings.children;
const input = i => rows[i].children[2];
const value = i => rows[i].children[1].children[0].textContent;
const status = i => rows[i].children[1].children[1].textContent;
const visible = () => rows.filter(row => !row.hidden).map(row => row.children[2].name);
const healthy = {ed593_mode: "real", ed593_connected: true, ed593_transport_error: null,
  ed593_discovered_mask: 0x0F};
for (let i = 0; i < 8; i++) {
  Object.assign(healthy, {[`ed593_tc${i}_enabled`]: i < 4, [`ed593_tc${i}_fault`]: false,
    [`ed593_tc${i}_ready`]: i < 4, [`ed593_tc${i}_temperature_c`]: i >= 4 ? null : i === 0 ? 0 : 20+i});
  check(rows[i].children[0].textContent === `TC-${i}` && input(i).name === `tc${i}`,
    "Stable display IDs must preserve physical channel numbering");
}
check(visible().length === 0, "Saved names cannot invent channel presence before telemetry arrives");
component.render({ed593_mode: "off"});
check(nodes.thermocoupleEmpty.textContent === "ED-593 is off." && nodes.thermocoupleSave.disabled,
  "An offline source with no discovery must show its empty state");
component.render(healthy);
check(value(0) === "0.00" && value(3) === "23.00", "Zero and all four healthy values must display");
check(visible().join() === "tc0,tc1,tc2,tc3", "Only reported enabled inputs are initially displayed");
check(!visible().includes("tc7"), "A named disabled channel must stay hidden");
input(0).value = "Draft <component>";
component.render({...healthy, ed593_tc0_temperature_c: 25});
check(input(0).value === "Draft <component>", "Telemetry must never overwrite a component edit");
const originalInput = input(0);
const expanded = {...healthy, ed593_tc6_enabled: true, ed593_tc6_fault: false,
  ed593_tc6_ready: true, ed593_tc6_temperature_c: 55.5};
component.render(expanded);
check(visible().join() === "tc0,tc1,tc2,tc3",
  "A newly enabled input must wait until dashboard restart to appear");
check(rows[6].children[0].textContent === "TC-6" && input(0) === originalInput && input(0).value === "Draft <component>",
  "Discovery must never renumber probes or rebuild a field being edited");
component.render({...expanded, ed593_tc0_fault: true, ed593_tc0_ready: false, ed593_tc0_temperature_c: null});
check(!rows[0].hidden && value(0) === "--" && status(0) === "Sensor fault",
  "A faulted enabled probe must remain visible with its original ID");
component.render({ed593_connected: false});
check(visible().join() === "tc0,tc1,tc2,tc3" && value(0) === "--",
  "Lost telemetry must retain startup discovery while blanking readings");
component.render(healthy);
check(rows[6].hidden && input(7).value === "Spare", "Disabling a channel must preserve saved names");
for (const [changes, reason] of [
  [{ed593_mode: "off"}, "Off"], [{ed593_connected: false}, "Stale"],
  [{ed593_transport_error: "timeout"}, "Transport error"],
  [{ed593_tc0_enabled: false}, "Disabled"], [{ed593_tc0_fault: true}, "Sensor fault"],
  [{ed593_tc0_ready: false}, "No reading"], [{ed593_tc0_temperature_c: null}, "No reading"],
  [{ed593_tc0_temperature_c: true}, "No reading"], [{ed593_tc0_temperature_c: NaN}, "No reading"]
]) {
  component.render({...healthy, ...changes});
  check(value(0) === "--" && status(0) === reason, "Unavailable readings must not show cached healthy numbers");
}
component.render({...healthy, ed593_tc1_fault: true});
check(value(1) === "--" && value(2) === "22.00", "One fault must not hide or shift another probe's reading");

const response = (ok, payload) => ({ok, text: async () => JSON.stringify(payload)});
const requests = [];
globalThis.fetch = async (url, options) => {
  check(url === API.thermocouples && options.method === "POST", "Name edits may only change dashboard configuration");
  const names = JSON.parse(options.body);
  requests.push(names);
  return response(true, {thermocouples: descriptors.map(item => ({...item, component: names[item.device].trim()}))});
};
const event = {preventDefault() {}};
input(0).value = "  Chamber  ";
nodes.thermocoupleForm.listeners.input();
check(nodes.thermocoupleStatus.textContent === "Unsaved", "Editing must report unsaved names");
await nodes.thermocoupleForm.listeners.submit(event);
check(requests[0].tc0 === "  Chamber  " && input(0).value === "Chamber", "Saving must round-trip channel-specific names");
check(requests[0].tc7 === "Spare", "Saving discovered names must retain names for hidden inputs");
check(nodes.thermocoupleStatus.textContent === "Saved", "Only a successful response may mark names saved");

let resolveRequest;
globalThis.fetch = async () => new Promise(resolve => { resolveRequest = resolve; });
const pending = nodes.thermocoupleForm.listeners.submit(event);
input(0).value = "Newer edit";
nodes.thermocoupleForm.listeners.input();
resolveRequest(response(true, {thermocouples: descriptors.map(item => ({...item, component: "Old"}))}));
await pending;
check(input(0).value === "Newer edit" && nodes.thermocoupleStatus.textContent === "Unsaved",
  "A delayed save response must not discard later edits or mark them saved");
globalThis.fetch = async () => response(false, {error: "disk full"});
await nodes.thermocoupleForm.listeners.submit(event);
check(nodes.thermocoupleStatus.textContent.includes("Save failed") && input(0).value === "Newer edit",
  "Failed saves must retain the editable name and report failure");
// A new browser gets the backend's startup snapshot, even after device changes.
function newPage(sample) {
  for (const id of Object.keys(nodes)) nodes[id] = new Element();
  const page = createThermocoupleComponent({descriptors});
  page.render(sample);
  const pageRows = nodes.thermocoupleReadings.children;
  return {page, rows: pageRows, visible: () => pageRows.filter(row => !row.hidden).map(row => row.children[2].name)};
}
const refreshed = newPage(expanded);
check(refreshed.visible().join() === "tc0,tc1,tc2,tc3", "Browser refresh must not rediscover newly enabled channels");
const restarted = newPage({...expanded, ed593_discovered_mask: 0x4F,
  ed593_tc6_fault: true, ed593_tc6_ready: false, ed593_tc6_temperature_c: null});
check(restarted.visible().join() === "tc0,tc1,tc2,tc3,tc6" && restarted.rows[6].children[0].textContent === "TC-6",
  "Dashboard restart must discover new inputs with their physical IDs");
check(restarted.rows[6].children[1].children[1].textContent === "Sensor fault",
  "Faulted enabled inputs must be included in startup discovery");
const empty = newPage({...healthy, ed593_discovered_mask: 0});
empty.page.render({...expanded, ed593_discovered_mask: 0});
check(empty.visible().length === 0 && !nodes.thermocoupleEmpty.hidden && nodes.thermocoupleSave.disabled &&
  nodes.thermocoupleEmpty.textContent === "No enabled thermocouple inputs.",
  "An empty startup mask must stay empty until dashboard restart");
print("PASS: startup thermocouple discovery, stable IDs, freshness, faults, edits and saves");
