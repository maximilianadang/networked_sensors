// SHARED PANELS: startup, live samples and keyboard routing. No hardware commands on load.
import {API, getJson} from "./api-lean.js";
import {drawAllCharts} from "./charts.js";
import {UI_CONFIG} from "./config.js";
import {elements, setDot, setText, shortcutTargetIsGuarded} from "./dom.js";
import {renderMetrics} from "./components/metrics.js";
import {createMetadataComponent} from "./components/metadata-lean.js";
import {renderSources, setEspTransportMessage} from "./components/sources.js";
import {createStepperComponent} from "./components/stepper-lean.js";
import {createToolbarComponent} from "./components/toolbar-lean.js";

const streamEls = elements(["streamDot", "streamStatus"]);

function setStreamStatus(label, state) {
  setText(streamEls.streamStatus, `Dashboard ${label.toLowerCase()}`);
  setDot(streamEls.streamDot, state);
}

async function startDashboard() {
  const operationalConfig = await getJson(API.config);
  const historyLimit = Math.min(
    UI_CONFIG.historyLimit,
    operationalConfig.history_limit
  );
  const state = {
    latest: null,
    run: {},
    history: []
  };
  let pollTimer = null;
  let pollRequestPending = false;
  let toolbar;
  let stepper;
  let metadata;

  let chartFrame = null;
  function scheduleCharts() {
    if (chartFrame !== null) return;
    chartFrame = requestAnimationFrame(() => {
      chartFrame = null;
      drawAllCharts(state.history);
    });
  }

  function renderAll() {
    toolbar.renderRun(state.run);
    if (!state.latest) return;
    toolbar.renderSample(state.latest);
    renderMetrics(state.latest, metadata?.powderFlowRateGPerS());
    renderSources(state.latest, state.history.length);
    stepper.render(state.latest);
    scheduleCharts();
  }

  function applySample(sample, append = true) {
    state.latest = sample;
    if (append && sample.timestamp_iso !== state.history.at(-1)?.timestamp_iso) {
      state.history.push(sample);
      if (state.history.length > historyLimit) state.history.shift();
    }
    renderAll();
  }

  function setRunState(run) {
    state.run = run || {};
    toolbar.renderRun(state.run);
  }

  toolbar = createToolbarComponent({
    getState: () => state,
    applySample,
    setRunState,
    setEspTransportMessage,
    solenoidCount: operationalConfig.solenoid_count
  });
  const stepperLimits = {
    ...operationalConfig.stepper,
    min_distance_mm: UI_CONFIG.stepperInputs.minDistanceMm
  };
  stepper = createStepperComponent({
    getLatest: () => state.latest,
    applySample,
    limits: stepperLimits
  });
  metadata = createMetadataComponent({
    geometry: operationalConfig.geometry,
    stepperLimits,
    applyMotionPlan: plan => stepper.applyMotionPlan(plan),
    onPowderFlowChange: powderFlowGPerS => {
      if (state.latest) renderMetrics(state.latest, powderFlowGPerS);
    }
  });

  function applyState(payload) {
    if (payload.run) state.run = payload.run;
    if (payload.sample) applySample(payload.sample, false);
    if (payload.metadata) metadata.fill(payload.metadata);
    toolbar.renderRun(state.run);
  }

  async function hydrate() {
    const historyPayload = await getJson(`${API.history}?limit=${historyLimit}`);
    state.history = historyPayload.history || [];
    applyState(await getJson(API.state));
  }

  function startPollingFallback() {
    if (pollTimer) return;
    pollTimer = window.setInterval(async () => {
      if (pollRequestPending) return;
      pollRequestPending = true;
      try {
        const payload = await getJson(API.latest);
        if (payload.run) state.run = payload.run;
        if (payload.sample) applySample(payload.sample);
        else toolbar.renderRun(state.run);
        setStreamStatus("Polling", "warn");
      } catch (error) {
        setStreamStatus("Offline", "bad");
      } finally {
        pollRequestPending = false;
      }
    }, UI_CONFIG.pollingIntervalMs);
  }

  function stopPollingFallback() {
    if (!pollTimer) return;
    window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function connectEvents() {
    if (!window.EventSource) {
      startPollingFallback();
      return;
    }
    const events = new EventSource(API.events);
    events.addEventListener("open", () => {
      stopPollingFallback();
      setStreamStatus("Live", "ok");
    });
    events.addEventListener("sample", event => {
      setStreamStatus("Live", "ok");
      applySample(JSON.parse(event.data));
    });
    events.addEventListener("state", event => {
      setRunState(JSON.parse(event.data));
    });
    events.addEventListener("error", () => {
      setStreamStatus("Reconnecting", "warn");
      startPollingFallback();
    });
  }

  document.addEventListener("keydown", event => {
    if (shortcutTargetIsGuarded(event)) return;

    const spacePressed = event.code === "Space" || event.key === " ";
    if (spacePressed) {
      if (!stepper.handleSpaceShortcut()) return;
      event.preventDefault();
      return;
    }

    const motorPressed = event.code === "KeyM" || event.key.toLowerCase() === "m";
    if (motorPressed) {
      if (!stepper.handleMotorShortcut()) return;
      event.preventDefault();
      return;
    }

    const index = Number(event.key) - 1;
    if (!Number.isInteger(index) ||
        index < 0 || index >= operationalConfig.solenoid_count) return;
    event.preventDefault();
    void toolbar.toggleSolenoid(index);
  });

  const chartObserver = new ResizeObserver(scheduleCharts);
  document.querySelectorAll(".plot-panel").forEach(panel => chartObserver.observe(panel));
  await hydrate();
  connectEvents();
}

await startDashboard().catch(error => {
  console.error("Dashboard startup failed", error);
  setStreamStatus("Offline", "bad");
});
