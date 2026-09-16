// TOOLBAR: Start, Stop, Export, Solenoid 1–4 and recording/UTC indicators.
// E-STOP belongs to Motor Control in stepper.js; shared appearance: dashboard.css.
import {API, downloadLatestExport, postJson} from "../api.js";
import {elements, setDot, setText} from "../dom.js";

export function createToolbarComponent({
  getState,
  applySample,
  setRunState,
  setEspTransportMessage,
  solenoidCount
}) {
  const ids = [
    "runDot", "runStatus", "clockText", "startRun", "stopRun", "exportRun"
  ];
  for (let index = 0; index < solenoidCount; index += 1) ids.push(`sol${index}`);
  const els = elements(ids);
  const pendingSolenoids = new Set();

  function updateSolenoidControls() {
    const {latest, run} = getState();
    for (let index = 0; index < solenoidCount; index += 1) {
      const enabled = latest?.[`solenoid${index + 1}_connected`] === true;
      const owner = latest?.[`solenoid${index + 1}_source`] === "stepper" ? "Controllino" : "ESP32";
      const pending = pendingSolenoids.has(index);
      els[`sol${index}`].disabled = !enabled || pending;
      els[`sol${index}`].title = pending
        ? `Sending Solenoid ${index + 1} command`
        : enabled
          ? `Toggle Solenoid ${index + 1} (keyboard ${index + 1})`
          : `${owner} control stream is not live`;
    }
  }

  function renderRun(run) {
    const recording = run.recording === true;
    const latestRecording = run.latest_recording || null;
    setText(els.runStatus, recording ? "Recording" : "Not recording");
    setDot(els.runDot, recording ? "ok" : "warn");
    els.startRun.disabled = recording;
    els.stopRun.disabled = !recording;
    els.exportRun.disabled = recording || !latestRecording;
    updateSolenoidControls();
  }

  function renderSample(sample) {
    const timestamp = sample.timestamp_iso || "";
    const date = new Date(timestamp);
    const compactTimestamp = Number.isNaN(date.getTime())
      ? (timestamp || "No timestamp")
      : `${date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "")} UTC`;
    setText(els.clockText, compactTimestamp);
    els.clockText.title = timestamp;
    for (let index = 0; index < solenoidCount; index += 1) {
      const on = sample[`solenoid${index + 1}_on`] === true;
      els[`sol${index}`].classList.toggle("on", on);
    }
    updateSolenoidControls();
  }

  async function toggleSolenoid(index) {
    const button = els[`sol${index}`];
    if (!button || button.disabled || pendingSolenoids.has(index)) return false;
    pendingSolenoids.add(index);
    updateSolenoidControls();
    setEspTransportMessage(`Sending Solenoid ${index + 1} command`);
    try {
      const payload = await postJson(API.solenoidToggle(index));
      if (payload.sample) applySample(payload.sample);
    } catch (error) {
      setEspTransportMessage(`Command failed: ${error.message}`);
    } finally {
      pendingSolenoids.delete(index);
      updateSolenoidControls();
    }
    return true;
  }

  els.startRun.addEventListener("click", async () => {
    const payload = await postJson(API.startRun);
    setRunState(payload.run);
  });
  els.stopRun.addEventListener("click", async () => {
    const payload = await postJson(API.stopRun);
    setRunState(payload.run);
  });
  els.exportRun.addEventListener("click", downloadLatestExport);
  for (let index = 0; index < solenoidCount; index += 1) {
    els[`sol${index}`].addEventListener("click", () => void toggleSolenoid(index));
  }

  return {renderRun, renderSample, toggleSolenoid};
}
