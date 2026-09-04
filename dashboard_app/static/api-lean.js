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
  stepperLocalRun: "/api/stepper/local-run",
  stepperSpeed: "/api/stepper/speed",
  stepperDroZero: "/api/stepper/dro-zero"
});

// One response parser for reads and commands; never retry a motor command.
async function request(url, payload) {
  const response = await fetch(url, payload === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  const text = await response.text();
  let data;
  try { data = JSON.parse(text); }
  catch { throw new Error(text || `${response.status} ${response.statusText}`); }
  if (!response.ok) throw new Error(data?.error || text || String(response.status));
  return data;
}
export const getJson = url => request(url);
export const postJson = (url, payload = {}) => request(url, payload);

// Keyed command lifecycle shared by panels. Separate stop/E-STOP keys remain
// available while other requests wait. No automatic POST retry or optimistic ACK.
export function createActions({refresh = () => {}, onError = () => {}} = {}) {
  const pending = new Set();
  return {
    has: key => pending.has(key),
    async run(key, execute, {success = () => {}, failure = onError, finish = () => {}} = {}) {
      if (pending.has(key)) return false;
      pending.add(key);
      try {
        refresh();
        await success(await execute());
      } catch (error) {
        failure(error);
      } finally {
        pending.delete(key);
        try { finish(); } finally { refresh(); }
      }
      return true;
    }
  };
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
