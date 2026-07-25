export const API = Object.freeze({
  config: "/api/config",
  state: "/api/state",
  latest: "/api/latest",
  history: "/api/history",
  events: "/api/events",
  startRun: "/api/run/start",
  stopRun: "/api/run/stop",
  exportLatest: "/api/export/latest",
  metadata: "/api/metadata",
  solenoidToggle: index => `/api/solenoid/toggle?n=${index}`,
  stepperMove: "/api/stepper/move",
  stepperStop: "/api/stepper/stop",
  stepperEstop: "/api/stepper/estop",
  stepperEstopReset: "/api/stepper/estop/reset",
  stepperMotorToggle: "/api/stepper/motor/toggle",
  stepperMotorPulse: "/api/stepper/motor/pulse",
  stepperHome: "/api/stepper/home",
  stepperControlMode: "/api/stepper/control-mode",
  stepperSpeed: "/api/stepper/speed",
  stepperDroZero: "/api/stepper/dro-zero"
});

export async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(await responseError(response));
  return response.json();
}

export async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json();
}

async function responseError(response) {
  const text = await response.text();
  try {
    const payload = JSON.parse(text);
    if (payload && typeof payload.error === "string") return payload.error;
  } catch (_error) {
    // Fall through to the unstructured response below.
  }
  return text || `${response.status} ${response.statusText}`;
}

export function downloadLatestExport() {
  // Download without navigating away from the live SSE dashboard.
  const link = document.createElement("a");
  link.href = API.exportLatest;
  link.download = "export.csv";
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
}
