// Manual target/ramp control; the backend renews the firmware's connection lease.
export function auxMotorKind(sample) {
  return sample?.stepper_aux_kind || "cubemars";
}

export function canMotorStatus(sample = {}) {
  const field = name => sample[`stepper_can_motor_${name}`];
  const connected = sample.stepper_connected === true && !sample.stepper_transport_error;
  const identified = sample.stepper_aux_kind === "cubemars";
  const age = Number.isFinite(field("feedback_age_ms")) && Number.isFinite(sample.stepper_age_ms)
    ? field("feedback_age_ms") + sample.stepper_age_ms : null;
  const fresh = age !== null && age >= 0 && age <= field("stale_ms");
  const ready = connected && identified && fresh && field("ready") === true && field("fault") === 0
    && !sample.stepper_estop_latched;
  const text = !connected ? "Controller disconnected"
    : sample.stepper_estop_latched ? "E-STOP"
    : !identified ? "Waiting for motor identification"
    : field("fault") ? `Motor fault ${field("fault")}`
    : !fresh ? "Waiting for motor feedback"
    : !field("ready") ? "CAN unavailable"
    : !field("manual_capable") ? "Firmware update required"
    : field("active") ? (field("target_rpm") === 0 ? "Motor stopping" : "Motor running") : "Motor ready";
  const tone = !connected || sample.stepper_estop_latched || (identified && field("fault"))
    ? "bad" : ready && field("manual_capable") ? "ok" : "warn";
  return {connected, identified, age, fresh, ready, text, tone};
}

export function createCanMotorComponent({getLatest, applySample, postJson, createActions}) {
  const panel = document.createElement("fieldset");
  panel.className = "brushless-control servo-control can-motor-control";
  panel.hidden = true;
  panel.innerHTML = `
    <legend>AK80-6 CAN motor</legend>
    <div class="piston-readout-values can-motor-readout" aria-label="Motor speed">
      <div><span class="piston-eyebrow">Target RPM</span><strong data-can="target" class="piston-secondary-value">—</strong></div>
      <div><span class="piston-eyebrow">Measured RPM</span><strong data-can="measured" class="piston-secondary-value">—</strong></div>
    </div>
    <div class="can-motor-inputs">
      <label>Target speed (RPM)
        <input data-can="rpm" type="number" step="1" value="400" inputmode="numeric">
      </label>
    </div>
    <div class="servo-actions">
      <button data-can="run" type="button">Start</button>
      <button data-can="release" class="stop" type="button">Stop</button>
    </div>
    <small data-can="feedback" role="status" aria-live="polite" hidden></small>
    <details><summary>CAN diagnostics</summary>
      <small data-can="rampHint"></small>
      <dl class="can-motor-readings">
        <dt>Current / temperature</dt><dd data-can="electrical">— / —</dd>
        <dt>Motor fault / feedback age</dt><dd data-can="health">— / —</dd>
      </dl>
      <small data-can="diagnostic"></small>
    </details>`;
  document.getElementById("brushlessMotorToggle").closest("fieldset")
    .insertAdjacentElement("afterend", panel);
  const el = Object.fromEntries([...panel.querySelectorAll("[data-can]")]
    .map(node => [node.dataset.can, node]));
  const value = (number, digits = 1) => Number.isFinite(number) ? number.toFixed(digits) : "—";
  let intent = 0, owned = 0, acceptedSequence = 0, rampStopping = false, pending = Promise.resolve();
  const actions = createActions({refresh: () => render(getLatest()), onError: fail});

  // Serialize this page's requests, including a Stop clicked during Start/Apply.
  // Never retry a start or adopt a run started by another page.
  function request(body) {
    const next = pending.catch(() => {}).then(async () => {
      if (body.action && intent !== body.token) throw new Error("Manual request canceled");
      const result = await postJson("/api/stepper/motor/can", body);
      if (result.confirmed !== true) throw new Error("Controller did not confirm the request");
      return result;
    }).catch(error => {
      if (body.action && intent === body.token) fail(error);
      throw error;
    });
    pending = next;
    return next;
  }

  function forget() {
    intent = owned = 0;
    rampStopping = false;
  }
  function fail(error) {
    forget();
    el.feedback.hidden = false;
    el.feedback.textContent = error.message;
  }

  function stopDescription(reason, flags) {
    if (reason === "can_bus_error" && Number.isInteger(flags) && (flags & 0xc0) && !(flags & 0x3f))
      return "receive buffer overflow";
    return ({can_bus_error: "CAN controller error", can_tx_failed: "CAN transmit failed",
      control_timeout: "controller connection timed out", feedback_stale: "motor feedback lost",
      motor_restart: "motor restarted", motor_fault: "motor fault", emergency_stop: "E-STOP",
      operator_stop: "operator stop", duration_elapsed: "timed run completed"})[reason] || reason;
  }

  function render(sample = {}) {
    panel.hidden = auxMotorKind(sample) !== "cubemars";
    if (panel.hidden) { forget(); return; }
    const field = name => sample[`stepper_can_motor_${name}`];
    if (owned && ((Number(field("command_sequence")) - acceptedSequence) >>> 0) > 0x7fffffff) return;
    const {connected, identified, age, fresh, ready} = canMotorStatus(sample);
    if (owned && (!ready || field("manual_token") !== owned || !field("active"))) {
      const stopped = rampStopping && !field("active") && field("error") === "operator_stop";
      forget();
      el.feedback.hidden = false;
      el.feedback.textContent = stopped ? "Stopped." : !ready ? "Control unavailable." : "Control changed.";
    }
    const runPending = actions.has("run"), releasePending = actions.has("release");
    const stopping = rampStopping || (field("active") && field("manual_token") && field("target_rpm") === 0);
    const rpm = Number(el.rpm.value), ramp = field("default_ramp_rpm_s"), maxRpm = field("max_rpm");
    el.rpm.min = Number.isFinite(maxRpm) ? -maxRpm : "";
    el.rpm.max = Number.isFinite(maxRpm) ? maxRpm : "";
    el.rampHint.textContent = Number.isFinite(ramp)
      ? `Fixed acceleration · ${value(ramp, 0)} RPM/s`
      : "Fixed acceleration · Waiting for controller settings";
    const valid = el.rpm.value !== "" && Number.isInteger(rpm) && rpm !== 0
      && Math.abs(rpm) <= maxRpm
      && Number.isInteger(ramp) && ramp >= 1 && ramp <= field("max_ramp_rpm_s");
    el.run.disabled = stopping || runPending || releasePending || !ready || !valid
      || !field("manual_capable") || (!!field("active") && !owned);
    el.run.textContent = owned ? "Apply speed" : "Start";
    el.release.disabled = stopping || releasePending || !connected || !identified;
    el.release.textContent = stopping ? "Stopping…" : "Stop";
    el.rpm.disabled = runPending;
    el.target.textContent = connected && identified ? value(field("target_rpm"), 0) : "—";
    el.measured.textContent = connected && identified && fresh ? value(field("rpm")) : "—";
    el.electrical.textContent = fresh
      ? `${value(field("current_a"), 2)} A / ${value(field("temperature_c"), 0)} °C` : "— / —";
    el.health.textContent = `${value(field("fault"), 0)} / ${value(age, 0)} ms`;
    const hex = number => Number.isInteger(number) && number >= 0 ? `0x${number.toString(16).padStart(2, "0")}` : "—";
    el.diagnostic.textContent = field("diag_sr")
      ? `Last stop: ${stopDescription(field("diag_sr"), field("diag_se"))}. Flags ${hex(field("diag_se"))}; slow read ${hex(field("diag_ss"))}. Error counters TX ${value(field("diag_st"), 0)}, RX ${value(field("diag_srec"), 0)}. Service gap ${value(field("diag_sg") / 1000, 2)} ms.`
      : `Current flags ${hex(field("diag_ef"))}; error counters TX ${value(field("diag_tec"), 0)}, RX ${value(field("diag_rec"), 0)}. No recorded stop event.`;
    panel.title = field("error") && field("error") !== "none" ? field("error") : "";
  }

  async function send(release) {
    if ((release ? el.release : el.run).disabled) return;
    const token = owned || ((crypto.getRandomValues(new Uint32Array(1))[0] & 0x7fffffff) || 1);
    const rampStop = release && owned && getLatest().stepper_can_motor_ramped_stop_capable === true;
    const body = rampStop ? {action: "update", token, rpm: 0,
      ramp_rpm_s: getLatest().stepper_can_motor_default_ramp_rpm_s}
      : release ? {rpm: 0} : {action: owned ? "update" : "start", token,
      rpm: Number(el.rpm.value), ramp_rpm_s: getLatest().stepper_can_motor_default_ramp_rpm_s};
    if (rampStop) rampStopping = true;
    if (release && !rampStop) forget(); else intent = token;
    el.feedback.hidden = false;
    el.feedback.textContent = release ? "Stopping…" : "Setting speed…";
    return actions.run(release ? "release" : "run", () => request(body), {
      success: result => {
        if (!release && intent !== token) return; // Stop won the race.
        if (!release || rampStop) {
          owned = token;
          acceptedSequence = result.stepper?.stepper_can_motor_command_sequence
            ?? result.sample?.stepper_can_motor_command_sequence ?? 0;
        }
        if (result.sample) applySample(result.sample);
        el.feedback.textContent = release ? "Stop accepted." : "Speed set.";
      }
    });
  }
  el.run.addEventListener("click", () => void send(false));
  el.release.addEventListener("click", () => void send(true));
  el.rpm.addEventListener("input", () => render(getLatest()));
  return {render};
}
