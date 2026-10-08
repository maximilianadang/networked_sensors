// Manual target/ramp control; firmware owns ramp timing and connection-loss expiry.
export function createCanMotorComponent({getLatest, applySample, postJson, createActions}) {
  const panel = document.createElement("fieldset");
  panel.className = "brushless-control servo-control can-motor-control";
  panel.hidden = true;
  panel.innerHTML = `
    <legend>AK80-6 CAN motor</legend>
    <span data-can="state" class="pill" role="status">Disconnected</span>
    <div class="can-motor-inputs">
      <label>Target speed (RPM)
        <input data-can="rpm" type="number" step="1" value="400" inputmode="numeric">
      </label>
    </div>
    <small data-can="rampHint"></small>
    <div class="servo-actions">
      <button data-can="run" type="button">Start</button>
      <button data-can="release" class="stop" type="button">Stop / release</button>
    </div>
    <small>Negative RPM reverses direction. Apply changes while running. Stop releases torque immediately; the motor can coast. Leaving this tab or losing communication also releases it.</small>
    <dl class="can-motor-readings">
      <dt>Target / ramped / measured RPM</dt><dd data-can="speed">— / — / —</dd>
      <dt>Current / driver temperature</dt><dd data-can="electrical">— / —</dd>
      <dt>Motor fault / feedback age</dt><dd data-can="health">— / —</dd>
    </dl>
    <small data-can="feedback" role="status" aria-live="polite"></small>
    <details><summary>CAN diagnostics</summary><small data-can="diagnostic"></small></details>`;
  document.getElementById("brushlessMotorToggle").closest("fieldset")
    .insertAdjacentElement("afterend", panel);
  const el = Object.fromEntries([...panel.querySelectorAll("[data-can]")]
    .map(node => [node.dataset.can, node]));
  const value = (number, digits = 1) => Number.isFinite(number) ? number.toFixed(digits) : "—";
  let intent = 0, owned = 0, acceptedSequence = 0, timer, pending = Promise.resolve(), renewing = false;
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
    clearTimeout(timer);
  }
  function fail(error) {
    forget();
    el.feedback.textContent = `${error.message}. Manual control ended; no automatic restart.`;
  }
  function scheduleRenewal() {
    clearTimeout(timer);
    if (owned) timer = setTimeout(renew, 250);
  }
  async function renew() {
    if (!owned || document.hidden) return;
    if (renewing || actions.has("run") || actions.has("release")) { scheduleRenewal(); return; }
    const token = owned;
    renewing = true;
    try {
      const result = await request({action: "renew", token});
      if (owned === token && result.sample) applySample(result.sample);
    } catch (error) { if (owned === token) fail(error); }
    finally { renewing = false; scheduleRenewal(); render(getLatest()); }
  }

  function stopDescription(reason, flags) {
    if (reason === "can_bus_error" && Number.isInteger(flags) && (flags & 0xc0) && !(flags & 0x3f))
      return "receive buffer overflow";
    return ({can_bus_error: "CAN controller error", can_tx_failed: "CAN transmit failed",
      control_timeout: "dashboard connection timed out", feedback_stale: "motor feedback lost",
      motor_restart: "motor restarted", motor_fault: "motor fault", emergency_stop: "E-STOP",
      operator_stop: "operator stop", duration_elapsed: "timed run completed"})[reason] || reason;
  }

  function render(sample = {}) {
    panel.hidden = sample.stepper_aux_kind !== "cubemars";
    if (panel.hidden) { forget(); return; }
    const field = name => sample[`stepper_can_motor_${name}`];
    if (owned && ((Number(field("command_sequence")) - acceptedSequence) >>> 0) > 0x7fffffff) return;
    const connected = sample.stepper_connected === true && !sample.stepper_transport_error;
    const age = Number.isFinite(field("feedback_age_ms")) && Number.isFinite(sample.stepper_age_ms)
      ? field("feedback_age_ms") + sample.stepper_age_ms : null;
    const fresh = age !== null && age >= 0 && age <= field("stale_ms");
    const ready = connected && fresh && field("ready") === true && field("fault") === 0
      && !sample.stepper_estop_latched;
    if (owned && (!ready || field("manual_token") !== owned || !field("active"))) {
      forget();
      el.feedback.textContent = "Manual control ended. Start explicitly to run again.";
    }
    const runPending = actions.has("run"), releasePending = actions.has("release");
    const rpm = Number(el.rpm.value), ramp = field("default_ramp_rpm_s");
    el.rpm.min = -field("max_rpm"); el.rpm.max = field("max_rpm");
    el.rampHint.textContent = Number.isFinite(ramp)
      ? `Fixed acceleration · 0 → ${field("max_rpm")} RPM in ${value(field("max_rpm") / ramp, 1)} s` : "";
    const valid = el.rpm.value !== "" && Number.isInteger(rpm) && rpm !== 0
      && Math.abs(rpm) <= field("max_rpm")
      && Number.isInteger(ramp) && ramp >= 1 && ramp <= field("max_ramp_rpm_s");
    el.run.disabled = runPending || releasePending || !ready || !valid
      || !field("manual_capable") || (!!field("active") && !owned);
    el.run.textContent = owned ? "Apply speed" : "Start";
    el.release.disabled = releasePending || !connected;
    el.rpm.disabled = runPending;
    const stoppedFor = field("diag_sr") || field("error");
    const stopText = stoppedFor && !["none", "boot", "running"].includes(stoppedFor)
      ? stopDescription(stoppedFor, field("diag_se")) : "";
    el.state.textContent = !connected ? "Controller disconnected"
      : sample.stepper_estop_latched ? "E-STOP"
      : field("fault") ? `Motor fault ${field("fault")}`
      : !fresh ? "Waiting for motor feedback"
      : !field("ready") ? "CAN unavailable"
      : !field("manual_capable") ? "Manual controls require firmware 1.4.0"
      : field("active") ? (owned ? "Running · manual control" : "Running · another control source")
      : stopText ? `Stopped · ${stopText}` : "Ready · stopped";
    el.speed.textContent = `${value(field("target_rpm"), 0)} / ${value(field("applied_rpm"))} / ${fresh ? value(field("rpm")) : "—"}`;
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
    const body = release ? {rpm: 0} : {action: owned ? "update" : "start", token,
      rpm: Number(el.rpm.value), ramp_rpm_s: getLatest().stepper_can_motor_default_ramp_rpm_s};
    if (release) forget(); else intent = token;
    el.feedback.textContent = release ? "Requesting torque release…" : "Sending target speed…";
    return actions.run(release ? "release" : "run", () => request(body), {
      success: result => {
        if (!release && intent !== token) return; // Stop/hidden tab won the race.
        if (!release) {
          owned = token;
          acceptedSequence = result.stepper?.stepper_can_motor_command_sequence
            ?? result.sample?.stepper_can_motor_command_sequence ?? 0;
        }
        if (result.sample) applySample(result.sample);
        el.feedback.textContent = release ? "Torque release accepted; check measured speed."
          : "Target speed accepted; measured speed is shown above.";
      },
      finish: scheduleRenewal
    });
  }
  function leave() {
    if (!intent && !owned) return;
    forget();
    // The firmware lease is authoritative if this best-effort release is lost.
    void request({rpm: 0}).catch(() => {});
  }
  document.addEventListener("visibilitychange", () => { if (document.hidden) leave(); });
  window.addEventListener("pagehide", leave);
  el.run.addEventListener("click", () => void send(false));
  el.release.addEventListener("click", () => void send(true));
  el.rpm.addEventListener("input", () => render(getLatest()));
  return {render};
}
