// Run with Node + Playwright. Entire page and controller are mocked: no hardware I/O.
const {chromium} = require('playwright');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 480, height: 800}});
    await page.route('**/*', route => {
      const path = new URL(route.request().url()).pathname;
      if (['/can-motor.js','/app-lean.js','/dashboard-lean.css'].includes(path)) {
        return route.fulfill({contentType: path.endsWith('.css') ? 'text/css' : 'text/javascript',
          body: readFileSync(resolve(__dirname,'../dashboard_app/static',path.slice(1)), 'utf8')});
      }
      return route.fulfill({contentType: 'text/html', body: `<!doctype html><link rel="stylesheet" href="/dashboard-lean.css">
        <main style="max-width:460px"><fieldset hidden><button id="brushlessMotorToggle"></button></fieldset></main>
        <script type="module">
        import {createCanMotorComponent} from '/can-motor.js';
        import {createActions} from '/app-lean.js';
        window.requests=[]; window.delayNext=0;
        let s = {stepper_aux_kind:'cubemars', stepper_connected:true, stepper_age_ms:0,
          stepper_can_motor_feedback_age_ms:0, stepper_can_motor_stale_ms:500,
          stepper_can_motor_ready:true, stepper_can_motor_fault:0, stepper_can_motor_active:false,
          stepper_can_motor_manual_capable:true, stepper_can_motor_manual_token:0, stepper_can_motor_command_sequence:0,
          stepper_can_motor_max_rpm:400, stepper_can_motor_max_ramp_rpm_s:80, stepper_can_motor_default_ramp_rpm_s:80,
          stepper_can_motor_target_rpm:0, stepper_can_motor_applied_rpm:0,
          stepper_can_motor_rpm:0, stepper_can_motor_current_a:0, stepper_can_motor_temperature_c:28};
        window.snapshot = () => ({...s}); window.apply = changes => { s={...s,...changes}; panel.render(s); };
        const panel = createCanMotorComponent({getLatest:()=>s, applySample:sample=>apply(sample), createActions,
          postJson:async (url,body)=>{
            requests.push(body);
            const delay=delayNext; delayNext=0;
            if(delay) await new Promise(r=>setTimeout(r,delay));
            if(window.rejectNext) {window.rejectNext=false; throw Error('Simulated transport loss');}
            s={...s,stepper_can_motor_command_sequence:s.stepper_can_motor_command_sequence+1};
            if(body.rpm===0) s={...s,stepper_can_motor_manual_token:0,stepper_can_motor_active:false,stepper_can_motor_target_rpm:0};
            else if(body.action!=='renew') s={...s,stepper_can_motor_manual_token:body.token,
              stepper_can_motor_active:true,stepper_can_motor_target_rpm:body.rpm};
            return {confirmed:true,sample:{...s}};
          }});
        panel.render(s); window.ready=true;
        </script>`});
    });
    await page.goto('http://can-ui.test/');
    await page.waitForFunction(()=>window.ready);
    const run = page.locator('[data-can=run]'), stop = page.locator('[data-can=release]');
    assert.equal(await page.getByText('Run duration').count(),0);
    assert.equal(await page.locator('[data-can=ramp]').count(),0);
    assert.match(await page.locator('[data-can=rampHint]').textContent(),/400 RPM in 5.0 s/);
    await run.click();
    await page.waitForFunction(()=>document.querySelector('[data-can=run]').textContent==='Apply speed');
    await page.waitForFunction(()=>requests.some(r=>r.action==='renew'));
    const first=await page.evaluate(()=>requests[0]);
    assert.equal(first.action,'start'); assert.equal(first.rpm,400); assert.equal(first.ramp_rpm_s,80);
    assert.equal('duration_ms' in first,false);
    await page.locator('[data-can=rpm]').fill('-3');
    await run.click();
    await page.waitForFunction(()=>requests.some(r=>r.action==='update'));
    const update=await page.evaluate(()=>requests.find(r=>r.action==='update'));
    assert.equal(update.token,first.token); assert.equal(update.rpm,-3); assert.equal(update.ramp_rpm_s,80);
    await stop.click();
    await page.waitForFunction(()=>document.querySelector('[data-can=run]').textContent==='Start');
    const n=await page.evaluate(()=>requests.length);
    await page.waitForTimeout(550); assert.equal(await page.evaluate(()=>requests.length),n);
    // Older SSE snapshots cannot cancel an acknowledged session.
    await run.click();
    await page.waitForFunction(()=>document.querySelector('[data-can=run]').textContent==='Apply speed');
    assert.equal(await page.evaluate(()=>{
      const current=snapshot();
      apply({stepper_can_motor_command_sequence:0,stepper_can_motor_active:false,stepper_can_motor_manual_token:0});
      const kept=document.querySelector('[data-can=run]').textContent==='Apply speed'; apply(current); return kept;
    }),true);
    // Apply queued behind a renewal must be dropped if Stop cancels its intent.
    await page.evaluate(()=>{delayNext=700;requests=[];});
    await page.waitForFunction(()=>requests.some(r=>r.action==='renew'));
    await run.click(); await stop.click();
    await page.waitForFunction(()=>requests.some(r=>r.rpm===0));
    await page.waitForTimeout(550);
    assert.equal(await page.evaluate(()=>requests.some(r=>r.action==='update')),false);
    // A late successful Start response cannot override an intervening Stop.
    await page.evaluate(()=>{delayNext=700; requests=[];});
    await run.click(); await stop.click();
    await page.waitForFunction(()=>requests.some(r=>r.rpm===0));
    await page.waitForTimeout(550);
    assert.deepEqual(await page.evaluate(()=>requests.map(r=>r.action||'release')),['start','release']);
    // Fresh feedback following a fault/expiry never adopts or restarts the run.
    await run.click();
    await page.waitForFunction(()=>document.querySelector('[data-can=run]').textContent==='Apply speed');
    await page.evaluate(()=>apply({stepper_can_motor_active:false,stepper_can_motor_manual_token:0}));
    const ended=await page.evaluate(()=>requests.length);
    await page.waitForTimeout(550); assert.equal(await page.evaluate(()=>requests.length),ended);
    // A failed renewal ends ownership without retrying a start.
    await run.click();
    await page.waitForFunction(()=>document.querySelector('[data-can=run]').textContent==='Apply speed');
    await page.evaluate(()=>{rejectNext=true;});
    await page.waitForFunction(()=>document.querySelector('[data-can=feedback]').textContent.includes('Simulated transport loss'));
    const failed=await page.evaluate(()=>requests.length);
    await page.waitForTimeout(550); assert.equal(await page.evaluate(()=>requests.length),failed);
    // Hidden pages release, then remain inactive when made visible again.
    await page.evaluate(()=>apply({stepper_can_motor_active:false,stepper_can_motor_manual_token:0}));
    await run.click();
    await page.waitForFunction(()=>document.querySelector('[data-can=run]').textContent==='Apply speed');
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
    await page.waitForFunction(()=>requests.at(-1).rpm===0);
    const hidden=await page.evaluate(()=>requests.length);
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
    await page.waitForTimeout(550); assert.equal(await page.evaluate(()=>requests.length),hidden);
    await page.evaluate(()=>apply({stepper_can_motor_manual_capable:false}));
    assert.equal(await run.isDisabled(),true);
    await page.evaluate(()=>apply({stepper_can_motor_manual_capable:true,stepper_can_motor_active:false,stepper_can_motor_manual_token:0}));
    await page.evaluate(()=>apply({stepper_can_motor_diag_sr:'can_bus_error',stepper_can_motor_diag_se:64,
      stepper_can_motor_diag_ss:64,stepper_can_motor_diag_st:0,stepper_can_motor_diag_srec:0,stepper_can_motor_diag_sg:12500}));
    assert.equal(await page.locator('[data-can=state]').textContent(),'Stopped · receive buffer overflow');
    assert.match(await page.locator('[data-can=diagnostic]').textContent(),/Flags 0x40; slow read 0x40/);
    await page.evaluate(()=>apply({stepper_can_motor_diag_sr:'control_timeout',stepper_can_motor_diag_se:0}));
    assert.equal(await page.locator('[data-can=state]').textContent(),'Stopped · dashboard connection timed out');
    for (const width of [480,320]) {
      await page.setViewportSize({width,height:800});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      if(process.env.CAN_UI_SCREENSHOT_DIR) await page.locator('.can-motor-control').screenshot({path:resolve(process.env.CAN_UI_SCREENSHOT_DIR,`can-panel-${width}.png`)});
    }
    console.log('CAN UI: target/ramp, renewal, update, stop race, expiry, transport failure, hidden tab, old firmware, and narrow layout passed.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
