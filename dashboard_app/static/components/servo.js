// PANEL: Position servo — MS62 calibration and On/Off; shared by both dashboards.
// Firmware owns return-and-release timing. Rendering never sends motion commands.
export function createServoComponent({getLatest, applySample, postJson}) {
  const brushless = document.getElementById("brushlessMotorToggle").closest("fieldset");
  const panel = document.createElement("fieldset");
  panel.className = "brushless-control servo-control";
  panel.style.display = "none";
  panel.innerHTML = `
    <legend>Position servo</legend>
    <span data-servo="state" class="pill" aria-live="polite">Unknown</span>
    <label class="brushless-pulse-field">Displacement from off (° clockwise)
      <input data-servo="angle" type="number" min="0" step="0.1" inputmode="decimal">
    </label>
    <div class="servo-actions">
      <button data-servo="toggle" class="servo-toggle" type="button" role="switch"
        aria-label="Servo" aria-checked="false">
        <span class="servo-toggle-track" aria-hidden="true"></span>
        <span data-servo="toggleLabel">Off</span>
      </button>
      <button data-servo="zero" type="button" title="Moves to the counterclockwise endpoint (0°), then disables pulses">Zero</button>
    </div>
    <small data-servo="feedback" role="status"></small>`;
  brushless.insertAdjacentElement("afterend", panel);
  const el = Object.fromEntries([...panel.querySelectorAll("[data-servo]")]
    .map(node => [node.dataset.servo, node]));
  let pending = false, dirty = false;

  function render(sample) {
    const servo = sample.stepper_aux_kind === "servo";
    brushless.hidden = servo;
    brushless.style.display = servo ? "none" : "";
    panel.style.display = servo ? "" : "none";
    if (!servo) return;
    const settings = sample.servo_settings;
    const live = sample.stepper_connected === true && !sample.stepper_transport_error;
    const releasing = sample.stepper_servo_releasing === true;
    const enabled = sample.stepper_servo_enabled === true;
    const ready = live && settings && sample.stepper_servo_release_capable === true;
    el.state.textContent = !live ? "Disconnected" : !ready ? "Firmware update required" :
      releasing ? "Returning to off" : enabled ? "Holding target" : "Pulses disabled";
    if (settings) {
      if (!dirty) el.angle.value = settings.displacement_deg;
      el.angle.max = Math.floor((settings.off_pulse_us - sample.stepper_servo_min_us) * 0.135 * 10) / 10;
    }
    const on = enabled && !releasing;
    el.toggle.setAttribute("aria-checked", String(on));
    el.toggleLabel.textContent = on ? "On" : "Off";
    const valid = el.angle.value !== "" && el.angle.checkValidity();
    el.angle.disabled = pending || !ready;
    el.toggle.disabled = pending || !ready || (!on && !valid) || sample.stepper_estop_latched;
    el.zero.disabled = pending || !ready || enabled || releasing || sample.stepper_estop_latched;
  }

  async function send(action) {
    pending = true;
    render(getLatest());
    el.feedback.textContent = "";
    try {
      const result = await postJson("/api/stepper/servo", {
        action, displacement_deg: Number(el.angle.value)
      });
      if (result.confirmed !== true) throw new Error("Servo command was not confirmed");
      dirty = false;
      if (result.sample) applySample(result.sample);
      if (action === "endpoint") el.feedback.textContent = "Off endpoint commanded and saved. Clockwise range: 0–270°.";
    } catch (error) {
      el.feedback.textContent = error.message;
    } finally {
      pending = false;
      render(getLatest());
    }
  }
  el.angle.addEventListener("input", () => { dirty = true; render(getLatest()); });
  el.toggle.addEventListener("click", () => {
    if (!el.toggle.disabled) void send(el.toggle.getAttribute("aria-checked") === "true" ? "off" : "on");
  });
  el.zero.addEventListener("click", () => { if (!el.zero.disabled) void send("endpoint"); });
  return {render};
}
