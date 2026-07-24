# Dashboard code map

Field guide for changing the laptop dashboard without internet access or a
frontend build tool. All paths are relative to `networked_sensors/`.

## Offline edit loop

1. Start `python3 dashboard.py --host 127.0.0.1 --port 8000` from this directory,
   or use the equivalent command in `RUNBOOK.md` from the parent directory.
2. Edit the local file identified below.
3. Save and reload `http://127.0.0.1:8000/`.

There is no Node, package-install, bundling, CDN, font, or other internet step.
The server sends HTML, CSS, and JavaScript with `Cache-Control: no-store`.

## File layout

```text
dashboard.py                         CLI/bootstrap; compatibility imports
dashboard_app/
  config.py                          asset allowlist and metadata defaults
  runtime.py                         live state and operator command boundary
  http.py                            HTTP routes, SSE, and local asset serving
  static/
    index.html                       visible page markup in screen order
    dashboard.css                    theme/layout styles in screen order
    app.js                           shared state, hydration, SSE, polling
    api.js                           endpoint names and GET/POST/download helpers
    config.js                        presentation-only settings and chart series
    charts.js                        shared canvas chart renderer
    dom.js                           formatting, DOM, and keyboard-guard helpers
    components/
      toolbar.js                     run, export, and Solenoid 1-4 controls
      metrics.js                     five-card metric grid
      stepper.js                     stepper/piston state and controls
      metadata.js                    test metadata form
      sources.js                     source status and diagnostics
```

## Page-to-code map

| Visible region | Markup | Behavior/data | Main tests |
| --- | --- | --- | --- |
| Header/source pills and timestamp | `static/index.html` | source state in `components/sources.js`; timestamp and run state in `components/toolbar.js`; stream state in `app.js` | `Esp32FirmwareLayoutTests` |
| Run, export, solenoids | `static/index.html` | `components/toolbar.js`; endpoints in `api.js` | ESP32 layout and real-adapter tests |
| E-STOP | `static/index.html` | `components/stepper.js`; runtime acknowledgement in `runtime.py` | `UsbStepperDashboardTests` |
| Five-card metric grid | `static/index.html` | `components/metrics.js`; precision in `static/config.js` | `test_dashboard_metric_grid_uses_channel_and_open_line_summaries` |
| Three charts | `static/index.html` | series in `static/config.js`; rendering in `charts.js` | `test_dashboard_chart_layout_and_open_flow_sums` |
| Stepper/piston and condensed interlocks | `static/index.html` | `components/stepper.js`; commands in `runtime.py`; limits in `supervisor_core.py` | `UsbStepperDashboardTests`, `SimulatedStepperSourceTests` |
| Test metadata | `static/index.html` | `components/metadata.js`; allowed keys in `dashboard_app/config.py` | recorder/dashboard runtime tests |
| Source details drawer | `static/index.html` | source health in `components/sources.js`; stepper telemetry and optional Command ID in `components/stepper.js`; merged fields from `supervisor_core.py` | real-source, stepper, and independence tests |

Shared panel styling and responsive layout live in `static/dashboard.css`. Its
comments follow the page from theme/base rules through header, toolbar, metrics,
charts, lower panels, source detail, and responsive breakpoints.

## Desktop layout contract

At a CSS content viewport of at least 1121 px by 900 px,
`static/dashboard.css` changes the primary page to a fixed `100dvh` operator
view with no page scrollbar. `100dvh` is based on the browser's content
viewport, so Firefox/Zen toolbar height is not part of the available dashboard
height. The four main rows are:

1. Start/Stop/Export, centered E-STOP/Reset, and Solenoid 1-4;
2. the five live metric cards;
3. three equal-width pressure/flow charts;
4. a 50/50 Stepper/Test Metadata row.

Within the Stepper half, `.stepper-layout` is another 50/50 split: the vertical
piston is the left-left area and mode, motion controls, and interlocks occupy
the left-right area. The fixed upper-right `.source-drawer` holds detailed
source and motion telemetry in an internally scrolling overlay so it does not
consume a fifth page row.

Do not increase one of the fixed row heights without reducing another and
rechecking a real browser at 100% zoom. Below the desktop breakpoint, normal
responsive flow is intentional and may scroll.

## Constants: where to edit

Presentation-only values are safe to change without altering controller guards:

- Theme colors and viewport breakpoints: `static/dashboard.css`.
- Chart history shown in the browser, polling interval, numeric precision,
  long/fast-move confirmation thresholds, input steps/default distance, and
  chart series/colors: `static/config.js`.
- Labels, units, legends, and panel order: `static/index.html`.

Operational values remain authoritative in Python:

- Stepper travel, minimum/maximum speed, Home speed, acceleration, pulse scale,
  and limit qualification: constants near the top of `supervisor_core.py`.
- Solenoid count: `ESP32_SOLENOID_COUNT` in `supervisor_core.py`.
- Server host, port, rate, and CLI defaults: `dashboard.py`.
- In-memory history capacity and metadata keys: `dashboard_app/config.py`.
- Solenoid-to-flow SUM mapping: `SourceMerger.OPEN_FLOW_PAIRS` and the
  Solenoid-4 SICK pair in `supervisor_core.py`.

`DashboardRuntime.dashboard_config()` publishes the relevant read-only Python
values at `/api/config`. `components/stepper.js` applies them to HTML input
attributes and validation. Do not replace those values with frontend-only
numbers: browser validation is operator feedback, while Python/firmware guards
remain the command authority.

## Common edits

- Change a label or rearrange panels: edit `static/index.html`; adjust the
  corresponding labelled CSS section only if layout changes.
- Add or remove a chart line: edit the matching array under `charts` in
  `static/config.js`, then update the legend in `static/index.html`.
- Change metric formatting: edit `metricPrecision` in `static/config.js`.
- Change which field a metric displays: edit `components/metrics.js`.
- Change a control interaction: edit only its component first; shared endpoint
  names belong in `api.js`, and cross-component sample flow belongs in `app.js`.
- Add an endpoint: implement it in `dashboard_app/http.py` and/or
  `dashboard_app/runtime.py`, add its name to `static/api.js`, then update
  `protocol_map.py`, regenerate `PROTOCOL.md`, and add an HTTP/runtime test.

## Verification

From the repository parent (`terraforming_mars/`):

```bash
python3 -m unittest discover -s networked_sensors -v
```

From `networked_sensors/`:

```bash
python3 protocol_map.py --check
```

For a visual change, reload healthy simulation and also check stale/missing
source modes at desktop, laptop, and narrow widths. Changes to motion controls,
E-STOP, limits, or command acknowledgement require the matching stepper tests;
do not validate those changes by appearance alone.
