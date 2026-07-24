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
    const simulated = run.esp32_source === "sim";
    const realAndLive = run.esp32_source === "real" &&
      latest && latest.esp32_connected === true;
    const enabled = simulated || realAndLive;
    for (let index = 0; index < solenoidCount; index += 1) {
      const pending = pendingSolenoids.has(index);
      els[`sol${index}`].disabled = !enabled || pending;
      els[`sol${index}`].title = pending
        ? `Sending Solenoid ${index + 1} command`
        : enabled
          ? `Toggle Solenoid ${index + 1} (keyboard ${index + 1})`
          : "ESP32 control stream is not live";
    }
  }

  function renderRun(run) {
    const recording = run.recording === true;
    const latestRecording = run.latest_recording || null;
    setText(els.runStatus, recording ? "Recording" : "Idle");
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
      : date.toLocaleString([], {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        });
    setText(els.clockText, compactTimestamp);
    els.clockText.title = timestamp;
    for (let index = 0; index < solenoidCount; index += 1) {
      const on = sample[`esp32_sol${index + 1}`] === true;
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
