#!/usr/bin/env python3
"""Isolated Firefox servo-panel check. Fake telemetry only; no device access."""
import json
import base64
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


def main():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT / 'dashboard_app/static'), **kwargs)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with TemporaryDirectory(prefix='.servo-browser-', dir=ROOT) as profiles:
        driver = subprocess.Popen(['geckodriver', '--port', '4445', '--profile-root', profiles],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def call(path, body=None, method=None):
            req = Request('http://127.0.0.1:4445' + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json'}, method=method)
            with urlopen(req, timeout=30) as response:
                return json.load(response)['value']
        session = None
        try:
            for _ in range(30):
                try:
                    call('/status')
                    break
                except OSError:
                    time.sleep(.1)
            session = call('/session', {'capabilities': {'alwaysMatch': {
                'browserName': 'firefox', 'moz:firefoxOptions': {'args': ['-headless']}}}})['sessionId']
            base = '/session/' + session
            # Directory index only: do not load the real dashboard app or its transports.
            call(base + '/url', {'url': f'http://127.0.0.1:{server.server_port}/components/'})
            result = call(base + '/execute/async', {'args': [], 'script': r'''
const done = arguments[arguments.length - 1];
(async () => {
  document.body.innerHTML = '<fieldset><button id="brushlessMotorToggle">Old ESC</button></fieldset>';
  const css = document.createElement('link');css.rel='stylesheet';css.href='/dashboard-lean.css';
  await new Promise((resolve,reject)=>{css.onload=resolve;css.onerror=reject;document.head.append(css);});
  document.body.style.cssText='padding:24px;max-width:460px';
  const {createServoComponent} = await import('/components/servo.js');
  let sample = {stepper_aux_kind:'servo', stepper_connected:true,
    stepper_servo_release_capable:true, stepper_servo_enabled:false,
    stepper_servo_min_us:500, stepper_servo_max_us:2500,
    servo_settings:{off_pulse_us:1550, displacement_deg:120}};
  const calls=[];
  const component=createServoComponent({getLatest:()=>sample,
    applySample:s=>{sample=s;component.render(s);},
    postJson:async(url, body)=>{
      calls.push(body);
      if(body.action==='on') sample.stepper_servo_enabled=true;
      if(body.action==='off') sample.stepper_servo_releasing=true;
      if(body.action==='endpoint') {
        sample.servo_settings.off_pulse_us=2500;
        sample.stepper_servo_enabled=true;sample.stepper_servo_releasing=true;
      }
      return {confirmed:true,sample};
    }});
  const el=k=>document.querySelector(`[data-servo="${k}"]`);
  const assert=(ok,msg)=>{if(!ok)throw Error(msg);};
  const click=async k=>{el(k).click();await new Promise(r=>setTimeout(r,300));};
  component.render(sample);
  assert(calls.length===0&&el('toggle').getAttribute('aria-checked')==='false','No motion on load; Off default');
  assert(getComputedStyle(el('toggle')).backgroundColor==='rgb(180, 35, 44)','Off is red');
  assert(el('angle').value==='120'&&!el('toggle').disabled,'120 degree target available');
  assert(el('angle').max==='141.7','Clockwise travel is bounded by the minimum pulse');
  await click('toggle');
  assert(calls[0].action==='on'&&calls[0].displacement_deg===120&&el('toggle').getAttribute('aria-checked')==='true','On');
  assert(getComputedStyle(el('toggle')).backgroundColor==='rgb(24, 115, 67)','On is green');
  assert(el('zero').disabled,'Cannot zero while On');
  await click('zero');assert(calls.length===1,'Disabled zero sends no command');
  el('angle').value='999';el('angle').dispatchEvent(new Event('input'));
  assert(!el('toggle').disabled,'Invalid displacement must not block Off');
  await click('toggle');
  assert(calls[1].action==='off'&&el('state').textContent==='Returning to off','Off');
  assert(el('zero').disabled,'Cannot zero while returning to Off');
  sample.stepper_servo_enabled=false;sample.stepper_servo_releasing=false;
  component.render(sample);await click('zero');
  assert(calls[2].action==='endpoint'&&el('angle').max==='270','Endpoint unlocks full travel');
  assert(el('zero').disabled,'Endpoint button locked during return');
  sample.stepper_connected=false;component.render(sample);
  assert(el('toggle').disabled&&el('zero').disabled,'Disconnected');
  sample.stepper_connected=true;sample.stepper_servo_release_capable=false;component.render(sample);
  assert(el('state').textContent==='Firmware update required'&&el('toggle').disabled,'Old firmware');
  sample.stepper_servo_release_capable=true;component.render(sample);
  return {passed:true,commands:calls};
})().then(done,error=>done({error:error.message,stack:error.stack}));
'''})
            assert result.get('passed'), result
            print(json.dumps(result, indent=2))
            Path('/tmp/ms62-toggle.png').write_bytes(base64.b64decode(call(base + '/screenshot')))
        finally:
            if session:
                call('/session/' + session, method='DELETE')
            driver.terminate()
            driver.wait(timeout=10)
            server.shutdown()


if __name__ == '__main__':
    main()
