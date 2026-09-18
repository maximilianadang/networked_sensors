#!/usr/bin/env python3
"""Browser smoke test via an existing local WebDriver session.

Usage: python3 check_servo_panel.py SESSION_ID
Reads the live dashboard, then clones its DOM and intercepts ALL fetches for
isolated positional On/Off/Stop tests. No test motor command reaches the controller.
The screenshot is an artifact of the real, read-only page before mocking.
"""
import base64
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen


def main():
    base = 'http://127.0.0.1:4444/session/' + sys.argv[1]

    def call(path, body=None):
        req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=30) as response:
            return json.load(response)['value']

    call('/window/rect', {'width': 1440, 'height': 1000})
    call('/url', {'url': 'http://127.0.0.1:8000/'})
    for _ in range(50):
        ready = call('/execute/sync', {'script': '''return
          document.getElementById("servoPositionSwitch")?.disabled === false &&
          ["Signal off", "Holding target"].includes(document.getElementById("brushlessMotorState")?.textContent);
        '''.replace('return\n', 'return '), 'args': []})
        if ready:
            break
        time.sleep(.2)
    assert ready, 'Servo panel did not become available'
    screenshot = call('/screenshot')
    Path('documentation/servo-panel-browser.png').write_bytes(base64.b64decode(screenshot))
    result = call('/execute/async', {'args': [], 'script': r'''
const done = arguments[arguments.length - 1];
(async () => {
  const [{sample}, config, module] = await Promise.all([
    fetch('/api/stepper/status').then(r => r.json()),
    fetch('/api/config').then(r => r.json()),
    import('/assets/components/stepper-lean.js')
  ]);
  // Remove the live component's event listeners by replacing its DOM.
  document.body.replaceWith(document.body.cloneNode(true));
  let latest = {...sample};
  const calls = [];
  window.fetch = async (url, options) => {
    const body = JSON.parse(options?.body || '{}');
    calls.push({url, body});
    if (url === '/api/stepper/servo') latest.stepper_brushless_motor_on = body.on;
    else if (url === '/api/stepper/servo/position') latest.stepper_servo_switch_on = body.on;
    else throw Error('Unexpected fetch blocked: ' + url);
    latest.stepper_brushless_motor_pulse_us = latest.stepper_brushless_motor_on
      ? latest.stepper_brushless_motor_setpoint_us : latest.stepper_servo_neutral_us;
    return new Response(JSON.stringify({accepted: true, confirmed: true, on: latest.stepper_brushless_motor_on,
      pulse_us: latest.stepper_brushless_motor_pulse_us,
      setpoint_us: latest.stepper_brushless_motor_setpoint_us, sample: {...latest}, stepper: {...latest}}),
      {status: 200, headers: {'Content-Type': 'application/json'}});
  };
  let panel;
  panel = module.createStepperComponent({getLatest: () => latest,
    applySample: value => {latest = value; panel.render(latest);},
    limits: {...config.stepper, min_distance_mm: 0.01}});
  panel.render(latest);
  if (!latest.stepper_servo_positional) throw Error("Positional firmware required");
  const toggle = document.getElementById('servoPositionSwitch');
  latest.stepper_servo_switch_on = false; panel.render(latest);
  toggle.click(); await new Promise(r => setTimeout(r, 100));
  panel.render(latest);
  toggle.click(); await new Promise(r => setTimeout(r, 100));
  document.getElementById('servoRelease').click();
  await new Promise(r => setTimeout(r, 100));
  done({calls});
})().catch(error => done({error: String(error)}));
'''})
    assert 'error' not in result, result
    assert [c['body'] for c in result['calls']] == [{'on': True}, {'on': False}, {'on': False}], result
    print('PASS: positional On/Off/Stop signal; no hardware commands sent')


if __name__ == '__main__':
    main()
