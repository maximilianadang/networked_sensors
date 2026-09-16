// Shared by both dashboards. Firmware declares which auxiliary panel is present.
export function createServoComponent({getLatest, applySample, postJson}) {
  const brushless = document.getElementById("brushlessMotorToggle").closest("fieldset");
  const panel = document.createElement("fieldset");
  panel.className = "brushless-control servo-control";
  panel.hidden = true;
  panel.style.display = "none";
  panel.innerHTML = `
    <legend>Position servo</legend>
    <span data-servo="state" class="pill" aria-live="polite">Unknown</span>
    <small data-servo="detail"></small>
    <label class="brushless-pulse-field">Position pulse (µs)
      <input data-servo="pulse" type="number" step="1" inputmode="numeric">
    </label>
    <div class="servo-actions">
      <button data-servo="apply" type="button">Apply position</button>
      <button data-servo="disable" type="button">Disable pulses</button>
    </div>
    <small data-servo="feedback" role="status"></small>`;
  brushless.insertAdjacentElement("afterend", panel);
  const el = Object.fromEntries([...panel.querySelectorAll("[data-servo]")]
    .map(node => [node.dataset.servo, node]));
  let pending = false;
  let dirty = false;

  function render(sample) {
    const servo = sample.stepper_aux_kind === "servo";
    brushless.hidden = servo;
    brushless.style.display = servo ? "none" : "";
    panel.hidden = !servo;
    panel.style.display = servo ? "" : "none";
    if (!servo) return;
    const live = sample.stepper_connected === true && !sample.stepper_transport_error;
    const enabled = sample.stepper_servo_enabled === true;
    const available = live && sample.stepper_servo_capable === true;
    el.state.textContent = !live ? "Disconnected" : enabled ? "Position pulses enabled" : "Pulses disabled";
    el.detail.textContent = `Commanded ${sample.stepper_servo_pulse_us} µs`;
    el.pulse.min = String(sample.stepper_servo_min_us);
    el.pulse.max = String(sample.stepper_servo_max_us);
    if (!dirty) el.pulse.value = String(sample.stepper_servo_pulse_us);
    const pulse = Number(el.pulse.value);
    const valid = Number.isInteger(pulse) && pulse >= Number(el.pulse.min) && pulse <= Number(el.pulse.max);
    el.pulse.disabled = pending || !available || sample.stepper_estop_latched;
    el.apply.disabled = el.pulse.disabled || !valid;
    el.disable.disabled = pending || !available || !enabled;
  }

  async function send(pulse) {
    pending = true;
    render(getLatest());
    el.feedback.textContent = "Waiting for controller confirmation…";
    try {
      const result = await postJson("/api/stepper/servo", {pulse_us: pulse});
      const status = result.stepper;
      if (result.confirmed !== true || status?.stepper_servo_enabled !== (pulse !== 0) ||
          (pulse !== 0 && status?.stepper_servo_pulse_us !== pulse)) {
        throw new Error("Servo output was not confirmed");
      }
      dirty = false;
      if (result.sample) applySample(result.sample);
      el.feedback.textContent = pulse ? `Position pulse confirmed: ${pulse} µs` : "Position pulses disabled";
    } catch (error) {
      el.feedback.textContent = error.message;
    } finally {
      pending = false;
      render(getLatest());
    }
  }

  el.pulse.addEventListener("input", () => { dirty = true; render(getLatest()); });
  el.apply.addEventListener("click", () => { if (!el.apply.disabled) void send(Number(el.pulse.value)); });
  el.disable.addEventListener("click", () => { if (!el.disable.disabled) void send(0); });
  return {render};
}
