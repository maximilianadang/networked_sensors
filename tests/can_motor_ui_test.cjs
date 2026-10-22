// All telemetry and command responses are mocked. No controller or hardware I/O.
// `node tests/can_motor_ui_test.cjs --serve` exposes the same assertions for an
// in-app browser. The default developer runner uses Node + Playwright.
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {createServer} = require('node:http');
const assert = require('node:assert/strict');

async function browserChecks() {
  const checks = [], requests = [];
  const report = document.getElementById('fixtureResult');
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const check = (condition, label) => {
    if (!condition) throw Error(label);
    checks.push(label);
  };
  const waitFor = async (condition, label) => {
    const deadline = Date.now() + 3000;
    while (!condition()) {
      if (Date.now() >= deadline) throw Error(`Timed out: ${label}`);
      await sleep(10);
    }
  };
  const base = {
    stepper_aux_kind: 'cubemars', stepper_connected: true, stepper_age_ms: 0,
    stepper_can_motor_feedback_age_ms: 0, stepper_can_motor_stale_ms: 500,
    stepper_can_motor_ready: true, stepper_can_motor_fault: 0,
    stepper_can_motor_active: false, stepper_can_motor_manual_capable: true,
    stepper_can_motor_manual_token: 0, stepper_can_motor_command_sequence: 0,
    stepper_can_motor_max_rpm: 400, stepper_can_motor_max_ramp_rpm_s: 80,
    stepper_can_motor_default_ramp_rpm_s: 80, stepper_can_motor_target_rpm: 0,
    stepper_can_motor_applied_rpm: 0, stepper_can_motor_rpm: 0,
    stepper_can_motor_current_a: 0, stepper_can_motor_temperature_c: 28
  };
  let sample = {...base}, holdNext = false, releaseHeld, rejectNext = false;
  let panel;
  const apply = changes => {
    sample = {...sample, ...changes};
    panel.render(sample);
    const status = canMotorStatus(sample);
    const topStatus = document.getElementById('fixtureMotorStatus');
    topStatus.textContent = status.text;
    topStatus.dataset.tone = status.tone;
  };
  // Prevent accidental network use even if a future component bypasses postJson.
  window.fetch = async () => { throw Error('Fixture blocks every network request'); };
  panel = createCanMotorComponent({getLatest: () => sample, applySample: apply, createActions,
    postJson: async (url, body) => {
      check(url === '/api/stepper/motor/can', 'command uses the CAN endpoint');
      requests.push({...body});
      if (holdNext) {
        holdNext = false;
        await new Promise(resolve => { releaseHeld = resolve; });
      }
      if (rejectNext) {
        rejectNext = false;
        throw Error('Simulated transport loss');
      }
      sample = {...sample, stepper_can_motor_command_sequence: sample.stepper_can_motor_command_sequence + 1};
      if (body.rpm === 0 && body.action === 'update' && sample.stepper_can_motor_ramped_stop_capable)
        sample = {...sample, stepper_can_motor_target_rpm: 0};
      else if (body.rpm === 0) sample = {...sample, stepper_can_motor_manual_token: 0,
        stepper_can_motor_active: false, stepper_can_motor_target_rpm: 0};
      else sample = {...sample, stepper_can_motor_manual_token: body.token,
        stepper_can_motor_active: true, stepper_can_motor_target_rpm: body.rpm};
      return {confirmed: true, sample: {...sample}};
    }});
  const element = name => document.querySelector(`[data-can="${name}"]`);
  const run = element('run'), stop = element('release');
  const owned = () => /^Apply/.test(run.textContent);
  const settledStop = async () => {
    stop.click();
    await waitFor(() => run.textContent === 'Start' && !sample.stepper_can_motor_active && !stop.disabled,
      'explicit Stop completed');
  };
  const start = async () => {
    run.click();
    await waitFor(owned, 'Start acknowledged');
  };
  const setRpm = rpm => {
    element('rpm').value = String(rpm);
    element('rpm').dispatchEvent(new Event('input', {bubbles: true}));
  };
  window.fixtureRequests = requests;
  window.fixtureSample = () => ({...sample});
  window.fixtureApply = apply;
  window.fixtureChecks = checks;
  try {
    apply(base);
    const fieldset = document.querySelector('.can-motor-control');
    check(!element('state') && !element('speed'), 'no local pill or combined speed row');
    check(stop.textContent.trim() === 'Stop', 'Stop has a simple label');
    check(element('target') && element('measured'), 'separate target and measured RPM readouts');
    check(!element('target').closest('details') && !element('measured').closest('details'),
      'primary RPM readouts remain visible');
    check(!fieldset.querySelector('details'), 'motor readings have no collapsible diagnostics');
    check(element('electrical').getBoundingClientRect().height > 0,
      'current and temperature are always visible');
    check(element('electrical').textContent === '0.00 A / 28 °C',
      'current and temperature display live values with units');
    check(!element('health') && !element('rampHint') && !element('diagnostic'),
      'motor panel omits fault, feedback age, acceleration and protocol diagnostic text');
    check(!/Negative RPM reverses|Leaving this tab|losing communication also releases/.test(fieldset.textContent),
      'no persistent instruction paragraph or obsolete focus-stop claim');
    check(!element('ramp') && !/Run duration/.test(fieldset.textContent), 'no extra ramp or duration input');
    check(canMotorStatus(sample).ready === true, 'shared status reports ready telemetry');
    check(auxMotorKind({}) === 'cubemars', 'missing kind defaults to CubeMars');
    apply({stepper_aux_kind: undefined, stepper_connected: false});
    check(!fieldset.hidden && run.disabled && stop.disabled, 'offline default is visible and inert');
    apply({stepper_connected: true});
    check(run.disabled && stop.disabled && /identification/.test(canMotorStatus(sample).text),
      'unidentified connected controller remains inert');
    apply(base);

    apply({stepper_can_motor_max_rpm: 793});
    element('rpm').value = '500';
    element('rpm').dispatchEvent(new Event('input'));
    check(!run.disabled, '500 RPM is accepted without the old operating cap');
    element('rpm').value = '-500';
    element('rpm').dispatchEvent(new Event('input'));
    check(!run.disabled, 'negative 500 RPM is accepted');
    element('rpm').value = '400';
    apply(base);

    await start();
    const first = requests[0];
    check(first.action === 'start' && first.rpm === 400 && first.ramp_rpm_s === 80,
      'Start submits explicit target and configured ramp');
    check(Number.isInteger(first.token) && first.token > 0 && first.token <= 0x7fffffff,
      'Start uses a positive protocol token');
    check(!('duration_ms' in first), 'manual Start does not fabricate timed duration');
    const started = requests.length;
    await sleep(600);
    check(requests.length === started && !requests.some(body => body.action === 'renew'),
      'browser sends no renewals while running');
    Object.defineProperty(document, 'hidden', {configurable: true, value: true});
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('pagehide'));
    await sleep(300);
    check(requests.length === started && owned(), 'hidden tab and pagehide neither Stop nor forget the run');
    Object.defineProperty(document, 'hidden', {configurable: true, value: false});
    document.dispatchEvent(new Event('visibilitychange'));
    await sleep(100);
    check(requests.length === started && owned(), 'focus return sends no command');

    setRpm(-3);
    run.click();
    await waitFor(() => requests.some(body => body.action === 'update') && !run.disabled, 'Apply acknowledged');
    const update = requests.find(body => body.action === 'update');
    check(update.token === first.token && update.rpm === -3 && update.ramp_rpm_s === 80,
      'Apply preserves ownership and sends the new signed target');
    apply({stepper_can_motor_rpm: 12.5, stepper_can_motor_current_a: 1.25,
      stepper_can_motor_temperature_c: 35});
    check(element('target').textContent.includes('-3') && element('measured').textContent.includes('12.5'),
      'accepted target and actual measured speed remain distinct');
    check(element('electrical').textContent === '1.25 A / 35 °C',
      'persistent current and temperature update with telemetry');
    await settledStop();
    const stopped = requests.length;
    await sleep(350);
    check(requests.length === stopped, 'explicit Stop has no subsequent automatic command');

    await start();
    const acknowledged = {...sample};
    apply({stepper_can_motor_command_sequence: 0, stepper_can_motor_active: false,
      stepper_can_motor_manual_token: 0});
    check(owned(), 'older telemetry cannot cancel an acknowledged client session');
    apply(acknowledged);
    await settledStop();

    // A request already in flight completes before Stop; its late success must
    // never restore ownership after the operator has canceled it.
    for (const action of ['start', 'update']) {
      if (action === 'update') await start();
      holdNext = true;
      const index = requests.length;
      run.click();
      await waitFor(() => requests.length === index + 1 && releaseHeld, `${action} held in flight`);
      stop.click();
      const release = releaseHeld; releaseHeld = undefined; release();
      await waitFor(() => requests.length === index + 2 && !sample.stepper_can_motor_active && !stop.disabled,
        `${action} then Stop completed`);
      check(requests[index].action === action && requests[index + 1].rpm === 0,
        `pending ${action} is followed by one explicit Stop`);
      check(run.textContent === 'Start', `late ${action} response cannot restore ownership`);
    }

    await start();
    apply({stepper_can_motor_fault: 5, stepper_can_motor_active: false, stepper_can_motor_manual_token: 0});
    check(run.disabled && !owned(), 'motor fault ends UI ownership');
    const faultCount = requests.length;
    apply({stepper_can_motor_fault: 0});
    await sleep(350);
    check(requests.length === faultCount && run.textContent === 'Start', 'fresh recovery never restarts after fault');
    await start();
    apply({stepper_can_motor_feedback_age_ms: 1000});
    check(run.disabled && !owned(), 'stale feedback ends UI ownership');
    check(element('electrical').textContent === '— / —', 'stale electrical readings are unavailable');
    const staleCount = requests.length;
    apply({stepper_can_motor_feedback_age_ms: 0, stepper_can_motor_active: false, stepper_can_motor_manual_token: 0});
    await sleep(350);
    check(requests.length === staleCount && run.textContent === 'Start', 'fresh recovery never restarts after stale feedback');

    rejectNext = true;
    run.click();
    await waitFor(() => element('feedback').textContent.includes('Simulated transport loss'), 'failed Start reported');
    const failed = requests.length;
    await sleep(350);
    check(requests.length === failed && !owned(), 'failed Start is not retried automatically');
    await start();
    rejectNext = true;
    setRpm(4); run.click();
    await waitFor(() => element('feedback').textContent.includes('Simulated transport loss'), 'failed Apply reported');
    const failedApply = requests.length;
    apply({stepper_can_motor_active: false, stepper_can_motor_manual_token: 0});
    await sleep(350);
    check(requests.length === failedApply && !owned(), 'failed Apply is not retried or restarted');
    apply({stepper_can_motor_manual_capable: false});
    check(run.disabled, 'old firmware cannot Start');
    apply({stepper_can_motor_manual_capable: true, stepper_can_motor_diag_sr: 'can_bus_error',
      stepper_can_motor_diag_se: 64, stepper_can_motor_diag_ss: 64, stepper_can_motor_diag_st: 0,
      stepper_can_motor_diag_srec: 0, stepper_can_motor_diag_sg: 12500});
    check(canMotorStatus(sample).text === 'Motor ready' && canMotorStatus(sample).tone === 'ok',
      'shared status reflects recovered current health');

    apply({stepper_can_motor_ramped_stop_capable: true});
    await start();
    const decelIndex = requests.length;
    stop.click();
    await waitFor(() => requests.length > decelIndex && element('feedback').textContent === 'Stop accepted.',
      'ramped Stop accepted');
    check(requests[decelIndex].action === 'update' && requests[decelIndex].rpm === 0,
      'Stop updates the active target to zero rather than releasing torque immediately');
    check(owned() && sample.stepper_can_motor_active && run.disabled && stop.disabled,
      'deceleration retains ownership and disables Start/Apply until completion');
    check(canMotorStatus(sample).text === 'Motor stopping', 'deceleration has an actual-state label');
    apply({stepper_can_motor_active: false, stepper_can_motor_manual_token: 0,
      stepper_can_motor_error: 'operator_stop', stepper_can_motor_applied_rpm: 0});
    check(!owned() && !run.disabled && element('feedback').textContent === 'Stopped.',
      'completed ramp returns to Start without restarting');

    const main = document.querySelector('main');
    for (const width of [456, 296]) {
      main.style.width = `${width}px`;
      await sleep(30);
      check(fieldset.scrollWidth <= fieldset.clientWidth + 1, `panel does not overflow at ${width}px`);
      for (const key of ['rpm', 'run', 'release', 'target', 'measured', 'electrical']) {
        const inner = element(key).getBoundingClientRect(), outer = fieldset.getBoundingClientRect();
        check(inner.left >= outer.left - 1 && inner.right <= outer.right + 1, `${key} stays inside ${width}px panel`);
      }
    }
    main.style.width = '';
    check(!requests.some(body => body.action === 'renew'), 'every recorded request is an explicit user command');
    report.textContent = `PASS ${checks.length} CAN UI assertions; all commands mocked`;
    window.fixtureDone = {passed: true, count: checks.length};
  } catch (error) {
    report.textContent = `FAILED: ${error.message}`;
    window.fixtureDone = {passed: false, error: error.message, count: checks.length};
  }
  document.title = report.textContent;
}

function fixtureHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="/dashboard-lean.css"><style>body{display:block;padding:12px}main{display:block;max-width:460px;padding:0}#fixtureResult{margin:8px 0;font-size:12px}</style></head><body>
    <div id="fixtureMotorStatus" role="status"></div><main><fieldset hidden><button id="brushlessMotorToggle"></button></fieldset></main>
    <div id="fixtureResult" role="status">Running mocked CAN UI checks…</div>
    <script type="module">import {createCanMotorComponent,canMotorStatus,auxMotorKind} from '/can-motor.js';
      import {createActions} from '/app-lean.js';(${browserChecks.toString()})();</script></body></html>`;
}
function fixtureResponse(path) {
  if (path === '/') return {type: 'text/html', body: fixtureHtml()};
  if (['/can-motor.js', '/app-lean.js', '/dashboard-lean.css'].includes(path)) {
    return {type: path.endsWith('.css') ? 'text/css' : 'text/javascript',
      body: readFileSync(resolve(__dirname, '../dashboard_app/static', path.slice(1)), 'utf8')};
  }
  return null;
}
async function main() {
  if (process.argv.includes('--serve')) {
    const port = Number(process.env.CAN_UI_FIXTURE_PORT || 53888);
    const server = createServer((request, response) => {
      const result = request.method === 'GET' && fixtureResponse(new URL(request.url, 'http://localhost').pathname);
      if (!result) { response.writeHead(403); response.end('Fixture blocks command and unknown routes'); return; }
      response.writeHead(200, {'Content-Type': result.type, 'Cache-Control': 'no-store'}); response.end(result.body);
    });
    server.listen(port, '127.0.0.1', () => console.log(`PID ${process.pid} | http://127.0.0.1:${port}/ | all command I/O mocked`));
    return;
  }
  const {chromium} = require('playwright');
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 480, height: 800}});
    await page.route('**/*', route => {
      const result = route.request().method() === 'GET' && fixtureResponse(new URL(route.request().url()).pathname);
      return result ? route.fulfill({contentType: result.type, body: result.body}) : route.abort();
    });
    await page.goto('http://can-ui.test/');
    await page.waitForFunction(() => window.fixtureDone, undefined, {timeout: 15000});
    const result = await page.evaluate(() => window.fixtureDone);
    assert.equal(result.passed, true, result.error);
    console.log(`CAN UI: ${result.count} assertions passed; backend renewal, focus independence, explicit commands and races verified.`);
  } finally { await browser.close(); }
}
module.exports = {fixtureHtml, fixtureResponse};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
