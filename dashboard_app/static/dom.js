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
