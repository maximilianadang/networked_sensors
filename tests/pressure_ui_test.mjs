// Isolated pressure/velocity rendering; no network or device I/O.
const check = (condition, message) => { if (!condition) throw new Error(message); };
class Element {
  constructor() { this.children = []; this.style = {}; this.dataset = {}; this.textContent = ""; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
}
const card = () => {
  const element = new Element(), label = new Element(), value = new Element();
  label.childNodes = [];
  element.querySelector = selector => selector === "label" ? label : value;
  return element;
};
const nodes = {espPressureMetric: card(), sickPressureMetric: card()};
const grid = new Element();
for (const id of ["pressureChart", "espFlowChart", "sickFlowChart"]) {
  const panel = new Element(), title = new Element(), legend = new Element();
  panel.querySelector = selector => selector === "h2" ? title : legend;
  nodes[id] = {closest: () => panel};
}
globalThis.document = {getElementById: id => nodes[id], createElement: () => new Element(),
  createTextNode: text => ({textContent: text}), querySelector: selector => selector === ".plot-grid" ? grid : null};
const {createControllinoMeasurementsComponent, calculateAirPowderRatio} = await import("../dashboard_app/static/app-lean.js");
const descriptors = Array.from({length: 8}, (_, i) => ({id: i+1,
  group: i < 4 || i === 7 ? "low" : "high", group_label: "Pressure",
  unit: i < 4 || i === 7 ? "mbar" : "psi", calibrated: true, volts_per_count: .01,
  value_key: `p${i+1}`, valid_key: `v${i+1}`}));
const component = createControllinoMeasurementsComponent({descriptors, getHistory: () => []});
const low = nodes.espPressureMetric.children[0], high = nodes.sickPressureMetric.children[0];
const sample = {stepper_mode: "controllino", stepper_connected: true, stepper_pressure_capable: true,
  pitot_velocity_m_s: 12.345};
for (let i = 1; i <= 8; i++) Object.assign(sample, {[`p${i}`]: 1.234, [`v${i}`]: true});
component.render(sample);
check(low.children.length === 5 && high.children.length === 3, "AI08 belongs with low pressures");
check(low.children[0].children[1].textContent === "1.23", "mbar must retain two decimals");
check(high.children[0].children[1].textContent === "1.2", "psi precision must remain unchanged");
const p4 = low.children[3];
check(p4.children[2].children[1].textContent === "12.35", "Velocity must appear beside P4");
check(low.children.filter(row => row.children.length === 3).length === 1, "Only P4 has velocity");
component.render({...sample, pitot_velocity_m_s: 0});
check(p4.children[2].children[1].textContent === "0.00", "Zero velocity is valid");
component.render({...sample, pitot_velocity_m_s: null});
check(p4.children[2].children[1].textContent === "--", "Unavailable velocity must clear");

const flow = {dxmr90_connected: true, dxmr90_transport_error: null,
  dxmr90_total_mass_flow_g_min: 30, dxmr90_port3_mass_flow_g_min: 12,
  open_line_air_flow_g_min: 999};
check(calculateAirPowderRatio(flow, .1) === 7, "Ratio must sum all three SICK sensors and convert powder g/s to g/min");
check(calculateAirPowderRatio({...flow, dxmr90_total_mass_flow_g_min: 0,
  dxmr90_port3_mass_flow_g_min: 0}, 1) === 0, "Zero air flow must give zero ratio");
for (const change of [{dxmr90_connected: false}, {dxmr90_transport_error: "timeout"},
  {dxmr90_total_mass_flow_g_min: null}, {dxmr90_port3_mass_flow_g_min: null},
  {dxmr90_total_mass_flow_g_min: NaN}, {dxmr90_port3_mass_flow_g_min: Infinity}]) {
  check(calculateAirPowderRatio({...flow, ...change}, .1) === null,
    "Missing or unhealthy SICK data must leave the ratio unavailable");
}
for (const powder of [null, "", 0, -1, NaN, Infinity]) {
  check(calculateAirPowderRatio(flow, powder) === null, "Invalid powder rate must leave ratio unavailable");
}
