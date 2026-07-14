#!/usr/bin/env python3
"""Local web dashboard and API for the flow-management supervisor."""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, urlparse

try:
    from .supervisor_core import (
        DEFAULT_DXMR90_HOST,
        DEFAULT_DXMR90_PORT,
        DEFAULT_DXMR90_REAL_RATE_HZ,
        DEFAULT_DXMR90_UNIT_ID,
        DEFAULT_DROP_AFTER_S,
        DEFAULT_ESP32_BASE_URL,
        DEFAULT_ESP32_TIMEOUT_S,
        DEFAULT_STEPPER_USB_BAUD,
        DEFAULT_STEPPER_USB_PORT,
        DEFAULT_STEPPER_STEPS_PER_MM,
        DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        DEFAULT_STEPPER_NETWORK_URL,
        DEFAULT_STALE_AFTER_S,
        SIMULATION_SCENARIOS,
        DXMR90_DATA_PATHS,
        SOURCE_MODES,
        STEPPER_SOURCE_MODES,
        SourceMerger,
        make_sources,
    )
    from .recorder import (
        DEFAULT_RECORD_DIR,
        FlowRunRecorder,
        list_recordings,
        resolve_artifact,
    )
except ImportError:  # pragma: no cover - direct script execution fallback
    from supervisor_core import (
        DEFAULT_DXMR90_HOST,
        DEFAULT_DXMR90_PORT,
        DEFAULT_DXMR90_REAL_RATE_HZ,
        DEFAULT_DXMR90_UNIT_ID,
        DEFAULT_DROP_AFTER_S,
        DEFAULT_ESP32_BASE_URL,
        DEFAULT_ESP32_TIMEOUT_S,
        DEFAULT_STEPPER_USB_BAUD,
        DEFAULT_STEPPER_USB_PORT,
        DEFAULT_STEPPER_STEPS_PER_MM,
        DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        DEFAULT_STEPPER_NETWORK_URL,
        DEFAULT_STALE_AFTER_S,
        SIMULATION_SCENARIOS,
        DXMR90_DATA_PATHS,
        SOURCE_MODES,
        STEPPER_SOURCE_MODES,
        SourceMerger,
        make_sources,
    )
    from recorder import (
        DEFAULT_RECORD_DIR,
        FlowRunRecorder,
        list_recordings,
        resolve_artifact,
    )


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_RATE_HZ = 10.0
DEFAULT_HISTORY_LIMIT = 600
DEFAULT_METADATA = {
    "sample_number": "",
    "sub_number": "",
    "dispenser": "",
    "material": "",
    "powder_flow_rate_g_per_min": "",
    "description": "",
    "notes": "",
}


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Flow Management Supervisor</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101114;
      --surface: #181b20;
      --surface-2: #20242b;
      --line: #303844;
      --text: #f2f5f8;
      --muted: #9ba8b6;
      --green: #36c275;
      --blue: #4aa3ff;
      --amber: #f2b84b;
      --red: #ff6b6b;
      --violet: #a78bfa;
      --shadow: rgba(0, 0, 0, 0.32);
    }

    * {
      box-sizing: border-box;
    }

    /* Author rules such as `label { display: grid; }` otherwise override the
       browser's default hidden presentation. Keep mode-scoped controls absent. */
    [hidden] {
      display: none !important;
    }

    html,
    body {
      margin: 0;
      min-height: 100%;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    body {
      display: flex;
      flex-direction: column;
    }

    button,
    input,
    select,
    textarea {
      font: inherit;
    }

    button {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #242a32;
      color: var(--text);
      cursor: pointer;
      padding: 0 14px;
      transition: transform 120ms ease, border-color 120ms ease, background 120ms ease;
    }

    button:hover {
      border-color: #5a6776;
      background: #2b323d;
    }

    button:active {
      transform: translateY(1px);
    }

    button.primary {
      background: #1f7a4c;
      border-color: #299a60;
    }

    button.stop {
      background: #7b2c31;
      border-color: #a53c43;
    }

    .emergency-actions {
      display: grid;
      gap: 5px;
      min-width: 210px;
    }

    button.emergency-stop {
      min-height: 52px;
      border: 2px solid #ff7b83;
      background: #bd1e2d;
      color: #fff;
      font-size: 1.02rem;
      font-weight: 800;
      letter-spacing: 0.04em;
    }

    button.emergency-stop:hover:not(:disabled) {
      border-color: #fff;
      background: #e02b3c;
    }

    button.emergency-reset {
      min-height: 32px;
      background: #242a32;
    }

    .emergency-state {
      color: var(--muted);
      font-size: 0.76rem;
      text-align: center;
    }

    body.estop-latched button.emergency-stop,
    body.estop-latched .emergency-state {
      color: #fff;
      background: #bd1e2d;
    }

    button.solenoid.on {
      color: #08130d;
      background: var(--green);
      border-color: var(--green);
      font-weight: 700;
    }

    input,
    select,
    textarea {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #0f1217;
      color: var(--text);
      padding: 8px 10px;
    }

    textarea {
      min-height: 86px;
      resize: vertical;
    }

    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 0.84rem;
    }

    .toggle-control {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      min-height: 38px;
      width: fit-content;
      color: var(--text);
      cursor: pointer;
    }

    .toggle-control input {
      position: absolute;
      width: 1px;
      height: 1px;
      min-height: 0;
      margin: -1px;
      padding: 0;
      opacity: 0;
    }

    .toggle-track {
      position: relative;
      width: 46px;
      height: 24px;
      border: 1px solid #5a6776;
      border-radius: 999px;
      background: #242a32;
      transition: background 120ms ease, border-color 120ms ease;
    }

    .toggle-track::after {
      content: "";
      position: absolute;
      top: 3px;
      left: 3px;
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: var(--text);
      transition: transform 120ms ease;
    }

    .toggle-control input:checked + .toggle-track {
      border-color: var(--blue);
      background: #1f5f99;
    }

    .toggle-control input:checked + .toggle-track::after {
      transform: translateX(22px);
    }

    .toggle-control input:focus-visible + .toggle-track {
      outline: 2px solid var(--blue);
      outline-offset: 2px;
    }

    .toggle-control input:disabled + .toggle-track {
      opacity: 0.45;
      cursor: not-allowed;
    }

    .topbar {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: center;
      padding: 18px clamp(16px, 3vw, 32px);
      border-bottom: 1px solid var(--line);
      background: #13161a;
      box-shadow: 0 12px 30px var(--shadow);
      position: sticky;
      top: 0;
      z-index: 10;
    }

    .brand {
      display: grid;
      gap: 2px;
    }

    .brand h1 {
      margin: 0;
      font-size: clamp(1.12rem, 2.4vw, 1.55rem);
      font-weight: 760;
      letter-spacing: 0;
    }

    .brand span {
      color: var(--muted);
      font-size: 0.9rem;
    }

    .status-strip {
      display: flex;
      flex-wrap: wrap;
      justify-content: end;
      gap: 8px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-height: 30px;
      padding: 0 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--muted);
      white-space: nowrap;
      font-size: 0.86rem;
    }

    .dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--muted);
    }

    .dot.ok {
      background: var(--green);
    }

    .dot.warn {
      background: var(--amber);
    }

    .dot.bad {
      background: var(--red);
    }

    main {
      width: min(1480px, 100%);
      margin: 0 auto;
      padding: 20px clamp(14px, 2.8vw, 32px) 34px;
      display: grid;
      gap: 18px;
    }

    .toolbar,
    .source-grid,
    .form-grid {
      display: grid;
      gap: 12px;
    }

    .toolbar {
      grid-template-columns: minmax(220px, 1fr) auto auto auto;
      align-items: center;
      padding-bottom: 2px;
    }

    .toolbar-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: end;
    }

    .readout {
      display: grid;
      gap: 4px;
    }

    .readout strong {
      font-size: 1rem;
    }

    .readout span {
      color: var(--muted);
      font-size: 0.88rem;
    }

    .metric-grid {
      display: grid;
      grid-template-columns: repeat(6, minmax(150px, 1fr));
      gap: 10px;
    }

    .metric {
      min-height: 112px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      padding: 13px;
      display: grid;
      align-content: space-between;
      box-shadow: 0 8px 20px rgba(0, 0, 0, 0.18);
    }

    .metric label {
      color: var(--muted);
      font-size: 0.8rem;
      text-transform: uppercase;
    }

    .metric .value {
      font-size: clamp(1.45rem, 3vw, 2rem);
      font-weight: 760;
      white-space: nowrap;
    }

    .metric .unit {
      color: var(--muted);
      font-size: 0.86rem;
    }

    .plot-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }

    .plot-panel,
    .source-panel,
    .metadata-panel,
    .control-panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: 0 8px 20px rgba(0, 0, 0, 0.18);
    }

    .plot-head,
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      min-height: 46px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }

    .plot-head h2,
    .panel-head h2 {
      margin: 0;
      font-size: 0.98rem;
      letter-spacing: 0;
    }

    .legend {
      display: flex;
      flex-wrap: wrap;
      justify-content: end;
      gap: 8px 12px;
      color: var(--muted);
      font-size: 0.78rem;
    }

    .legend span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .swatch {
      width: 16px;
      height: 3px;
      border-radius: 999px;
      background: currentColor;
    }

    canvas {
      display: block;
      width: 100%;
      height: 260px;
    }

    .lower-grid {
      display: grid;
      grid-template-columns: minmax(280px, 1.05fr) minmax(280px, 0.95fr);
      gap: 12px;
    }

    .metadata-panel form,
    .control-body,
    .source-body {
      padding: 14px;
    }

    .form-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .wide {
      grid-column: 1 / -1;
    }

    .form-actions,
    .solenoids {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .control-body {
      display: grid;
      gap: 14px;
    }

    .source-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .source-row {
      display: grid;
      gap: 8px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
    }

    .source-row header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .source-row h3 {
      margin: 0;
      font-size: 0.95rem;
    }

    .source-row dl {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 5px 12px;
      margin: 0;
      color: var(--muted);
      font-size: 0.86rem;
    }

    .source-row dd {
      margin: 0;
      color: var(--text);
      text-align: right;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    @media (max-width: 1120px) {
      .metric-grid {
        grid-template-columns: repeat(3, minmax(150px, 1fr));
      }
    }

    @media (max-width: 820px) {
      .topbar,
      .toolbar,
      .lower-grid,
      .plot-grid,
      .source-grid {
        grid-template-columns: 1fr;
      }

      .status-strip,
      .toolbar-actions {
        justify-content: start;
      }
    }

    @media (max-width: 580px) {
      .metric-grid,
      .form-grid {
        grid-template-columns: 1fr;
      }

      .topbar {
        position: static;
      }

      canvas {
        height: 220px;
      }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <h1>Flow Management Supervisor</h1>
      <span id="configuredMode">Simulation</span>
    </div>
    <div class="status-strip" aria-live="polite">
      <span class="pill"><span id="streamDot" class="dot warn"></span><span id="streamStatus">Connecting</span></span>
      <span class="pill"><span id="espDot" class="dot warn"></span><span id="espStatus">ESP32</span></span>
      <span class="pill"><span id="dxDot" class="dot warn"></span><span id="dxStatus">DXMR90</span></span>
      <span class="pill"><span id="stepperDot" class="dot warn"></span><span id="stepperStatus">Stepper</span></span>
      <span class="pill"><span id="runDot" class="dot"></span><span id="runStatus">Idle</span></span>
    </div>
  </header>

  <main>
    <section class="toolbar" aria-label="Run state">
      <div class="readout">
        <strong id="clockText">No sample yet</strong>
        <span id="sampleText">Waiting for supervisor data</span>
        <span id="recordingText">No recording yet</span>
      </div>
      <div class="emergency-actions" aria-label="Stepper software emergency stop">
        <button id="emergencyStop" class="emergency-stop" type="button"
          title="Stops step pulses through the dashboard, USB, and Yún firmware; not a hardwired safety circuit">SOFTWARE E-STOP</button>
        <button id="emergencyReset" class="emergency-reset" type="button">Reset E-STOP</button>
        <span id="emergencyState" class="emergency-state" aria-live="assertive">E-STOP status unknown</span>
      </div>
      <div class="toolbar-actions">
        <button id="startRun" class="primary" type="button">Start</button>
        <button id="stopRun" class="stop" type="button">Stop</button>
        <button id="exportRun" type="button">Export</button>
      </div>
      <div class="toolbar-actions">
        <button id="sol0" class="solenoid" type="button">Solenoid 1</button>
        <button id="sol1" class="solenoid" type="button">Solenoid 2</button>
        <button id="sol2" class="solenoid" type="button">Solenoid 3</button>
        <button id="sol3" class="solenoid" type="button">Solenoid 4</button>
      </div>
    </section>

    <section class="metric-grid" aria-label="Live values">
      <article class="metric"><label>P combined</label><div><span id="mPressure" class="value">--</span><span class="unit"> bar</span></div></article>
      <article class="metric"><label>ESP32 flow</label><div><span id="mEspFlow" class="value">--</span><span class="unit"> g/min</span></div></article>
      <article class="metric"><label>SICK total flow</label><div><span id="mDxFlow" class="value">--</span><span class="unit"> g/min</span></div></article>
      <article class="metric"><label>SICK 1 Pressure</label><div><span id="mDxP1" class="value">--</span><span class="unit"> bar</span></div></article>
      <article class="metric"><label>SICK 2 Pressure</label><div><span id="mDxP2" class="value">--</span><span class="unit"> bar</span></div></article>
      <article class="metric"><label>Heartbeat</label><div><span id="mHeartbeat" class="value">--</span><span class="unit"> count</span></div></article>
    </section>

    <section class="plot-grid" aria-label="Plots">
      <article class="plot-panel">
        <div class="plot-head">
          <h2>ESP32 Pressure (bar)</h2>
          <div class="legend">
            <span style="color: var(--blue)"><i class="swatch"></i>P1</span>
            <span style="color: var(--green)"><i class="swatch"></i>P2</span>
            <span style="color: var(--amber)"><i class="swatch"></i>P3</span>
          </div>
        </div>
        <canvas id="pressureChart" width="900" height="320"></canvas>
      </article>
      <article class="plot-panel">
        <div class="plot-head">
          <h2>SICK Pressure (bar)</h2>
          <div class="legend">
            <span style="color: var(--red)"><i class="swatch"></i>SICK 1</span>
            <span style="color: #22d3ee"><i class="swatch"></i>SICK 2</span>
          </div>
        </div>
        <canvas id="sickPressureChart" width="900" height="320"></canvas>
      </article>
      <article class="plot-panel">
        <div class="plot-head">
          <h2>Mass Flow Rate (g/min)</h2>
          <div class="legend">
            <span style="color: var(--blue)"><i class="swatch"></i>ESP32</span>
            <span style="color: var(--green)"><i class="swatch"></i>SICK 1</span>
            <span style="color: var(--amber)"><i class="swatch"></i>SICK 2</span>
            <span style="color: var(--violet)"><i class="swatch"></i>SICK total</span>
          </div>
        </div>
        <canvas id="flowChart" width="900" height="320"></canvas>
      </article>
    </section>

    <section class="lower-grid">
      <article class="metadata-panel">
        <div class="panel-head"><h2>Test Metadata</h2><span class="pill" id="metadataStatus">Unsaved</span></div>
        <form id="metadataForm">
          <div class="form-grid">
            <label>Sample number<input name="sample_number" autocomplete="off"></label>
            <label>Sub number<input name="sub_number" autocomplete="off"></label>
            <label>Dispenser<input name="dispenser" autocomplete="off"></label>
            <label>Material<input name="material" autocomplete="off"></label>
            <label class="wide">Powder flow rate<input name="powder_flow_rate_g_per_min" autocomplete="off"></label>
            <label class="wide">Description<input name="description" autocomplete="off"></label>
            <label class="wide">Notes<textarea name="notes"></textarea></label>
          </div>
          <div class="form-actions">
            <button class="primary" type="submit">Save Metadata</button>
          </div>
        </form>
      </article>

      <article class="source-panel">
        <div class="panel-head"><h2>Sources</h2><span class="pill" id="historyStatus">0 samples</span></div>
        <div class="source-body">
          <div class="source-grid">
            <section class="source-row">
              <header><h3>ESP32</h3><span class="pill"><span id="espRowDot" class="dot warn"></span><span id="espRowStatus">Unknown</span></span></header>
              <dl>
                <dt>Mode</dt><dd id="espMode">--</dd>
                <dt>Age</dt><dd id="espAge">--</dd>
                <dt>Pressure</dt><dd id="espPressure">--</dd>
                <dt>Flow</dt><dd id="espFlow">--</dd>
                <dt>Transport</dt><dd id="espTransport">--</dd>
              </dl>
            </section>
            <section class="source-row">
              <header><h3>DXMR90</h3><span class="pill"><span id="dxRowDot" class="dot warn"></span><span id="dxRowStatus">Unknown</span></span></header>
              <dl>
                <dt>Mode</dt><dd id="dxMode">--</dd>
                <dt>Age</dt><dd id="dxAge">--</dd>
                <dt>Port 1</dt><dd id="dxPort1">--</dd>
                <dt>Port 2</dt><dd id="dxPort2">--</dd>
              </dl>
            </section>
          </div>
        </div>
      </article>

      <article class="control-panel wide">
        <div class="panel-head"><h2>Yún Stepper Motion</h2><span class="pill" id="stepperState">Unknown</span></div>
        <div class="control-body">
          <form id="stepperForm">
            <div class="form-grid">
              <label>Control mode
                <span class="toggle-control">
                  <input id="stepperControlMode" type="checkbox" role="switch" aria-label="Enable Web Position mode">
                  <span class="toggle-track" aria-hidden="true"></span>
                  <span id="stepperControlModeLabel">Local Velocity</span>
                </span>
              </label>
              <label id="stepperDistanceField" hidden>Relative travel distance (mm)<input id="stepperDistance" name="distance_mm" type="number" step="0.01" min="0.01" max="137.18" value="1.0" required disabled></label>
              <label><span id="stepperSpeedLabel">Local velocity speed (mm/s)</span><input id="stepperSpeed" name="speed_mm_s" type="number" step="0.1" min="0.1" max="10" value="1.5" required></label>
              <label id="stepperCommandField" hidden>Command ID (optional)<input id="stepperCommandInput" name="command_id" maxlength="64" autocomplete="off" disabled></label>
            </div>
            <div class="form-actions">
              <button id="stepperMove" class="primary" type="submit" hidden>Move</button>
              <button id="stepperHome" type="button" hidden>Move to D8 Limit</button>
              <button id="stepperStop" class="stop" type="button" hidden>Stop Motion</button>
              <button id="stepperApplySpeed" type="button">Apply Local Velocity Speed</button>
              <span class="pill" id="stepperMessage" aria-live="polite">Simulation only</span>
            </div>
          </form>
          <div class="source-grid">
            <section class="source-row">
              <header><h3>Motion</h3></header>
              <dl>
                <dt>Source / owner</dt><dd id="stepperOwner">--</dd>
                <dt>Control mode</dt><dd id="stepperModeStatus">--</dd>
                <dt>Configured speed</dt><dd id="stepperConfiguredSpeed">--</dd>
                <dt>Scheduled speed</dt><dd id="stepperEffectiveSpeed">--</dd>
                <dt>Measured STEP output</dt><dd id="stepperMeasuredSpeed">--</dd>
                <dt>Command</dt><dd id="stepperCommand">--</dd>
              </dl>
            </section>
            <section class="source-row">
              <header><h3>Interlocks</h3></header>
              <dl>
                <dt>Local enable (D4)</dt><dd id="stepperLocal">--</dd>
                <dt id="stepperD5Label">Manual direction (D5)</dt><dd id="stepperManualDirection">--</dd>
                <dt>Fixed physical direction</dt><dd id="stepperDirectionStatus">--</dd>
                <dt>Driver output (D9 / ENA-)</dt><dd id="stepperDriverOutput">--</dd>
                <dt>Positive limit (D6)</dt><dd id="stepperPositiveLimit">--</dd>
                <dt>Negative limit (D8)</dt><dd id="stepperNegativeLimit">--</dd>
                <dt>Motion decision</dt><dd id="stepperBlocked">--</dd>
                <dt>USB status sequence</dt><dd id="stepperSequence">--</dd>
                <dt>Transport</dt><dd id="stepperTransport">--</dd>
              </dl>
            </section>
          </div>
        </div>
      </article>
    </section>
  </main>

  <script>
    const maxHistory = 240;
    const solenoidCount = 4;
    let latest = null;
    let runState = {};
    let history = [];
    let pollTimer = null;
    let controlModeDirty = false;
    let controlModeRequestPending = false;
    let stepperMessageSticky = false;
    let speedRequestPending = false;

    const els = {};
    for (const id of [
      "configuredMode", "streamDot", "streamStatus", "espDot", "espStatus",
      "dxDot", "dxStatus", "stepperDot", "stepperStatus", "runDot", "runStatus", "clockText", "sampleText",
      "recordingText", "mPressure", "mEspFlow", "mDxFlow", "mDxP1", "mDxP2", "mHeartbeat",
      "metadataStatus", "historyStatus", "espRowDot", "espRowStatus",
      "dxRowDot", "dxRowStatus", "espMode", "espAge", "espPressure",
      "espFlow", "espTransport", "dxMode", "dxAge", "dxPort1", "dxPort2",
      "sol0", "sol1", "sol2", "sol3", "startRun", "stopRun", "exportRun", "metadataForm",
      "emergencyStop", "emergencyReset", "emergencyState",
      "stepperForm", "stepperDistanceField", "stepperDistance", "stepperSpeed", "stepperSpeedLabel", "stepperControlMode", "stepperControlModeLabel",
      "stepperCommandField", "stepperCommandInput", "stepperMove", "stepperHome", "stepperStop", "stepperApplySpeed", "stepperMessage",
      "stepperState",
      "stepperConfiguredSpeed", "stepperEffectiveSpeed", "stepperMeasuredSpeed", "stepperCommand", "stepperOwner", "stepperModeStatus", "stepperLocal",
      "stepperD5Label", "stepperManualDirection", "stepperDirectionStatus", "stepperDriverOutput", "stepperPositiveLimit", "stepperNegativeLimit",
      "stepperBlocked", "stepperSequence", "stepperTransport"
    ]) {
      els[id] = document.getElementById(id);
    }

    function text(id, value) {
      els[id].textContent = value;
    }

    function numberValue(key, digits) {
      if (!latest || latest[key] === null || latest[key] === undefined) return "--";
      const value = Number(latest[key]);
      return Number.isFinite(value) ? value.toFixed(digits) : String(latest[key]);
    }

    function ageText(key) {
      if (!latest || latest[key] === null || latest[key] === undefined) return "--";
      const ms = Number(latest[key]);
      if (!Number.isFinite(ms)) return "--";
      return ms < 1000 ? `${ms.toFixed(0)} ms` : `${(ms / 1000).toFixed(1)} s`;
    }

    function dot(el, state) {
      el.classList.remove("ok", "warn", "bad");
      el.classList.add(state);
    }

    function connectedDot(value) {
      return value === true ? "ok" : value === false ? "bad" : "warn";
    }

    function setStreamStatus(label, state) {
      text("streamStatus", label);
      dot(els.streamDot, state);
    }

    function applyState(payload) {
      if (payload.sample) applySample(payload.sample, false);
      if (payload.run) runState = payload.run;
      if (payload.metadata) fillMetadata(payload.metadata);
      renderRun();
      if (payload.run) {
        const esp = payload.run.esp32_source || "sim";
        const dx = payload.run.dxmr90_source || "sim";
        const stepper = payload.run.stepper_source || "sim";
        const dxPath = payload.run.dxmr90_data_path || "direct";
        const dxRate = payload.run.dxmr90_rate_hz || 1;
        text("configuredMode", `ESP32 ${esp} / SICK-DXMR90 ${dx} ${dxPath} ${dxRate} Hz / stepper ${stepper} / stream ${payload.run.rate_hz} Hz`);
      }
    }

    function applySample(sample, append = true) {
      latest = sample;
      if (append) {
        history.push(sample);
        if (history.length > maxHistory) history.shift();
      }
      render();
    }

    function renderRun() {
      const recording = runState.recording === true;
      const active = runState.active_recording || null;
      const latestRecording = runState.latest_recording || null;
      const currentRecording = active || latestRecording;
      const esp32Controls = ["sim", "real"].includes(runState.esp32_source);
      text("runStatus", recording ? "Recording" : "Idle");
      dot(els.runDot, recording ? "ok" : "warn");
      els.startRun.disabled = recording;
      els.stopRun.disabled = !recording;
      els.exportRun.disabled = recording || !latestRecording;
      for (let i = 0; i < solenoidCount; i += 1) {
        els[`sol${i}`].disabled = !esp32Controls;
      }
      if (currentRecording && currentRecording.run_id) {
        const rows = currentRecording.merged_rows || 0;
        text("recordingText", `${currentRecording.run_id} (${rows} rows)`);
      } else {
        text("recordingText", "No recording yet");
      }
    }

    function render() {
      if (!latest) return;
      const elapsed = Number(latest.elapsed_s || 0);
      text("clockText", latest.timestamp_iso || "No timestamp");
      text("sampleText", `Elapsed ${elapsed.toFixed(1)} s`);
      text("mPressure", numberValue("esp32_p_combined_bar", 3));
      text("mEspFlow", numberValue("esp32_f_combined_gmin", 2));
      text("mDxFlow", numberValue("dxmr90_total_mass_flow_g_min", 2));
      text("mDxP1", numberValue("dxmr90_port1_pressure_bar", 3));
      text("mDxP2", numberValue("dxmr90_port2_pressure_bar", 3));
      text("mHeartbeat", numberValue("dxmr90_heartbeat", 0));

      const espConnected = latest.esp32_connected;
      const dxConnected = latest.dxmr90_connected;
      const stepperConnected = latest.stepper_connected;
      dot(els.espDot, connectedDot(espConnected));
      dot(els.dxDot, connectedDot(dxConnected));
      dot(els.stepperDot, connectedDot(stepperConnected));
      dot(els.espRowDot, connectedDot(espConnected));
      dot(els.dxRowDot, connectedDot(dxConnected));
      text("espStatus", espConnected ? "ESP32 live" : "ESP32 stale");
      text("dxStatus", dxConnected ? "DXMR90 live" : "DXMR90 stale");
      text("stepperStatus", stepperConnected ? "Stepper live" : "Stepper stale");
      text("espRowStatus", espConnected ? "Live" : "Stale");
      text("dxRowStatus", dxConnected ? "Live" : "Stale");
      text("espMode", latest.esp32_mode || "--");
      text("dxMode", latest.dxmr90_mode || "--");
      text("espAge", ageText("esp32_age_ms"));
      text("dxAge", ageText("dxmr90_age_ms"));
      text("espPressure", `${numberValue("esp32_p_combined_bar", 3)} bar`);
      text("espFlow", `${numberValue("esp32_f_combined_gmin", 2)} g/min`);
      text("espTransport", latest.esp32_transport_error || (espConnected ? "Connected" : "Waiting for stream"));
      text("dxPort1", `${numberValue("dxmr90_port1_mass_flow_g_min", 2)} g/min`);
      text("dxPort2", `${numberValue("dxmr90_port2_mass_flow_g_min", 2)} g/min`);
      text("historyStatus", `${history.length} samples`);

      const moving = latest.stepper_moving === true;
      const estopCapable = latest.stepper_estop_capable === true;
      const estopLatched = latest.stepper_estop_latched === true;
      const localEnabled = latest.stepper_local_enabled === true;
      const commandCapable = latest.stepper_command_capable === true;
      const controlMode = latest.stepper_control_mode || "unknown";
      const webPositionMode = controlMode === "web_position";
      document.body.classList.toggle("estop-latched", estopLatched);
      text("emergencyState", estopLatched
        ? "LATCHED — step pulses inhibited"
        : !stepperConnected
          ? "Unavailable — stepper offline"
          : estopCapable
            ? "Ready — software path only"
            : "Unavailable — firmware update required");
      text("stepperState", latest.stepper_state || "Unknown");
      text("stepperOwner", `${latest.stepper_mode || "--"} / ${latest.stepper_control_owner || "--"}`);
      text("stepperModeStatus", webPositionMode ? "Web Position" : controlMode === "local_velocity" ? "Local Velocity" : "--");
      text("stepperConfiguredSpeed", `${numberValue("stepper_command_speed_mm_s", 3)} mm/s`);
      text("stepperEffectiveSpeed", `${numberValue("stepper_speed_mm_s", 3)} mm/s`);
      const pulseMeasurementCapable = latest.stepper_pulse_measurement_capable === true;
      text("stepperMeasuredSpeed", pulseMeasurementCapable
        ? `${numberValue("stepper_measured_speed_mm_s", 3)} mm/s (${numberValue("stepper_measured_pulse_rate_sps", 0)} pulses/s)`
        : "Unavailable — firmware update required");
      text("stepperCommand", latest.stepper_command_id || "--");
      text("stepperLocal", `${localEnabled ? (webPositionMode ? "Armed" : "Running") : (webPositionMode ? "Disarmed" : "Stopped")} / ${latest.stepper_d4_raw || "--"}`);
      text("stepperD5Label", webPositionMode ? "Travel direction (D5)" : "Manual direction (D5)");
      text("stepperManualDirection", `${latest.stepper_manual_direction || "--"} / ${latest.stepper_d5_raw || "--"}`);
      if (latest.stepper_control_mode) {
        if (!controlModeDirty) {
          els.stepperControlMode.checked = webPositionMode;
        } else if (els.stepperControlMode.checked === webPositionMode) {
          controlModeDirty = false;
        }
      }
      text("stepperControlModeLabel", els.stepperControlMode.checked ? "Web Position" : "Local Velocity");
      const directionCalibrationSafe = latest.stepper_direction_calibration_safe === true;
      text("stepperDirectionStatus", directionCalibrationSafe
        ? "Normal (Forward → D6; Reverse → D8)"
        : latest.stepper_direction_mapping === "inverted"
          ? "UNSAFE LEGACY INVERSION — upload required"
          : "Unavailable — firmware update required");
      text("stepperDriverOutput", latest.stepper_driver_enable_capable === true
        ? latest.stepper_driver_enabled
          ? "ENERGIZED / D9 HIGH"
          : "DISABLED / D9 LOW"
        : "Unavailable — firmware update required");
      const positiveLatch = latest.stepper_positive_limit_latched ? " / LATCHED" : "";
      const negativeLatch = latest.stepper_negative_limit_latched ? " / LATCHED" : "";
      text("stepperPositiveLimit", `${latest.stepper_positive_limit_active ? "ACTIVE / LOW" : "Clear / HIGH"}${positiveLatch}`);
      text("stepperNegativeLimit", `${latest.stepper_negative_limit_active ? "ACTIVE / LOW" : "Clear / HIGH"}${negativeLatch}`);
      const decisionReason = latest.stepper_blocked_reason || "none";
      const decisionText = estopLatched
        ? "E-STOP LATCHED: motion inhibited"
        : latest.stepper_blocked
        ? `BLOCKED: ${decisionReason}`
        : decisionReason === "run_off"
          ? "Stopped: run_off"
          : decisionReason === "boot_disarmed"
            ? "Stopped: cycle D4 OFF after reset"
            : decisionReason === "driver_wakeup"
              ? "Waiting: 200 ms driver wake-up"
            : ["d4_abort", "direction_auth", "operator_stop"].includes(decisionReason)
              ? `ABORTED: ${decisionReason}`
          : `Allowed: ${decisionReason}`;
      text("stepperBlocked", decisionText);
      text("stepperSequence", latest.stepper_status_sequence ?? "--");
      text("stepperTransport", latest.stepper_transport_error || (stepperConnected ? "Connected" : "Waiting for status"));
      if (!stepperMessageSticky && ["usb", "network"].includes(latest.stepper_mode)) {
        const transportLabel = latest.stepper_mode === "network" ? "LAN" : "USB";
        text("stepperMessage", !directionCalibrationSafe
          ? `${transportLabel} unsafe legacy direction mapping; upload fixed-direction firmware before motion`
          : commandCapable
          ? webPositionMode
            ? "Web Position ready; D4 arms, D5 selects direction, and D6/D8 stop travel"
            : "Local Velocity: D4 runs/stops and D5 selects direction"
          : latest.stepper_speed_command_capable
          ? `${transportLabel} speed tuning ready; upload position-capable firmware for Home and Move`
          : `${transportLabel} diagnostics only; upload T4B firmware for speed tuning`);
      }
      updateStepperControls();

      for (let i = 0; i < solenoidCount; i += 1) {
        const on = latest[`esp32_sol${i + 1}`] === true;
        els[`sol${i}`].classList.toggle("on", on);
      }

      drawAllCharts();
    }

    function fillMetadata(metadata) {
      for (const [key, value] of Object.entries(metadata)) {
        const field = els.metadataForm.elements.namedItem(key);
        if (field && field.value !== value) field.value = value || "";
      }
      text("metadataStatus", "Saved");
    }

    function chartBounds(series) {
      const values = [];
      for (const sample of history) {
        for (const key of series) {
          const value = Number(sample[key]);
          if (Number.isFinite(value)) values.push(value);
        }
      }
      if (!values.length) return [0, 1];
      let min = Math.min(...values);
      let max = Math.max(...values);
      if (min === max) {
        min -= 1;
        max += 1;
      }
      const pad = (max - min) * 0.12;
      return [min - pad, max + pad];
    }

    function drawChart(canvasId, series) {
      const canvas = document.getElementById(canvasId);
      const ctx = canvas.getContext("2d");
      const ratio = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(320, Math.floor(rect.width * ratio));
      const height = Math.max(220, Math.floor(rect.height * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#12161b";
      ctx.fillRect(0, 0, width, height);
      const padL = 54 * ratio;
      const padR = 18 * ratio;
      const padT = 18 * ratio;
      const padB = 34 * ratio;
      const plotW = width - padL - padR;
      const plotH = height - padT - padB;

      ctx.strokeStyle = "#2f3945";
      ctx.lineWidth = 1 * ratio;
      ctx.fillStyle = "#9ba8b6";
      ctx.font = `${11 * ratio}px system-ui, sans-serif`;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";

      const keys = series.map(item => item.key);
      const [min, max] = chartBounds(keys);
      for (let i = 0; i <= 4; i += 1) {
        const y = padT + (plotH * i / 4);
        const value = max - ((max - min) * i / 4);
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(width - padR, y);
        ctx.stroke();
        ctx.fillText(value.toFixed(1), padL - 8 * ratio, y);
      }

      if (history.length < 2) return;
      const xFor = index => padL + plotW * (index / Math.max(1, history.length - 1));
      const yFor = value => padT + plotH * (1 - (value - min) / (max - min));

      for (const item of series) {
        ctx.strokeStyle = item.color;
        ctx.lineWidth = 2.2 * ratio;
        ctx.beginPath();
        let drawing = false;
        history.forEach((sample, index) => {
          const value = Number(sample[item.key]);
          if (!Number.isFinite(value)) {
            drawing = false;
            return;
          }
          const x = xFor(index);
          const y = yFor(value);
          if (!drawing) {
            ctx.moveTo(x, y);
            drawing = true;
          } else {
            ctx.lineTo(x, y);
          }
        });
        ctx.stroke();
      }
    }

    function drawAllCharts() {
      drawChart("pressureChart", [
        {key: "esp32_p1_bar", color: "#4aa3ff"},
        {key: "esp32_p2_bar", color: "#36c275"},
        {key: "esp32_p3_bar", color: "#f2b84b"}
      ]);
      drawChart("sickPressureChart", [
        {key: "dxmr90_port1_pressure_bar", color: "#ff6b6b"},
        {key: "dxmr90_port2_pressure_bar", color: "#22d3ee"}
      ]);
      drawChart("flowChart", [
        {key: "esp32_f_combined_gmin", color: "#4aa3ff"},
        {key: "dxmr90_port1_mass_flow_g_min", color: "#36c275"},
        {key: "dxmr90_port2_mass_flow_g_min", color: "#f2b84b"},
        {key: "dxmr90_total_mass_flow_g_min", color: "#a78bfa"}
      ]);
    }

    async function postJson(url, payload = {}) {
      const response = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }

    function updateStepperControls() {
      const distance = Number(els.stepperDistance.value);
      const speed = Number(els.stepperSpeed.value);
      const connected = latest && latest.stepper_connected === true;
      const enabled = latest && latest.stepper_local_enabled === true;
      const d4Off = latest && latest.stepper_d4_raw === "HIGH";
      const commandCapable = latest && latest.stepper_command_capable === true;
      const speedCommandCapable = latest && latest.stepper_speed_command_capable === true;
      const directionCalibrationSafe = latest && latest.stepper_direction_calibration_safe === true;
      const modeCommandCapable = latest && latest.stepper_mode_command_capable === true;
      const homeCapable = latest && latest.stepper_home_capable === true;
      const estopCapable = latest && latest.stepper_estop_capable === true;
      const estopLatched = latest && latest.stepper_estop_latched === true;
      const webPositionMode = latest && latest.stepper_control_mode === "web_position";
      const authorizedDirection = latest && latest.stepper_authorized_direction;
      // The operator enters only a positive travel magnitude. The physical D5
      // switch supplies the direction; "both" is the simulator's forward
      // default because it has no physical selector.
      const selectedDirection = authorizedDirection === "both" ? "forward" : authorizedDirection;
      const signedDistance = selectedDirection === "reverse"
        ? -distance
        : selectedDirection === "forward" ? distance : Number.NaN;
      const moving = latest && latest.stepper_moving === true;
      const positiveBlocked = latest && signedDistance > 0 &&
        (latest.stepper_positive_limit_active || latest.stepper_positive_limit_latched);
      const negativeBlocked = latest && signedDistance < 0 &&
        (latest.stepper_negative_limit_active || latest.stepper_negative_limit_latched);
      const directionSelected = selectedDirection === "forward" || selectedDirection === "reverse";
      const valid = Number.isFinite(distance) && distance > 0 && distance <= 137.18 &&
        Number.isFinite(speed) && speed > 0 && speed <= 10;
      const simulated = latest && latest.stepper_mode === "sim";
      els.emergencyStop.disabled = !connected || !estopCapable || estopLatched;
      els.emergencyReset.disabled = !connected || !estopCapable || !estopLatched ||
        moving || (!simulated && !d4Off);
      els.emergencyReset.title = !estopLatched
        ? "Software E-STOP is not latched"
        : !simulated && !d4Off
          ? "Turn physical D4 OFF before resetting"
          : "Reset the software latch; this does not start motion";
      // Distance and command ID have no meaning in continuous Local Velocity
      // mode. Hide their rows entirely so the form does not imply otherwise.
      els.stepperDistanceField.hidden = !webPositionMode;
      els.stepperCommandField.hidden = !webPositionMode;
      els.stepperMove.hidden = !webPositionMode;
      els.stepperHome.hidden = !webPositionMode;
      els.stepperStop.hidden = !webPositionMode;
      els.stepperApplySpeed.hidden = webPositionMode;
      els.stepperDistance.disabled = !webPositionMode;
      els.stepperDistance.title = webPositionMode
        ? "Positive travel magnitude; physical D5 selects Forward or Reverse"
        : "Available only in Web Position mode";
      els.stepperCommandInput.disabled = !webPositionMode;
      els.stepperCommandInput.title = webPositionMode
        ? "Optional identifier for this position command"
        : "Available only in Web Position mode";
      text("stepperSpeedLabel", webPositionMode
        ? "Web Position move speed (mm/s)"
        : "Local velocity speed (mm/s)");
      els.stepperMove.disabled = !commandCapable || !directionCalibrationSafe || !connected || !webPositionMode ||
        estopLatched || !enabled || moving || !valid || !directionSelected || positiveBlocked || negativeBlocked;
      els.stepperStop.disabled = estopLatched || !commandCapable || !webPositionMode || !moving;
      els.stepperHome.disabled = !homeCapable || !directionCalibrationSafe || !connected || !webPositionMode ||
        estopLatched || !enabled || moving || (authorizedDirection !== "reverse" && authorizedDirection !== "both");
      els.stepperControlMode.disabled = controlModeRequestPending || !directionCalibrationSafe || !modeCommandCapable ||
        estopLatched || !connected || !d4Off || moving;
      if (controlModeRequestPending) {
        els.stepperControlMode.title = "Waiting for the Yún to confirm the control mode";
      } else if (!connected) {
        els.stepperControlMode.title = "Yún USB status is not connected";
      } else if (!modeCommandCapable) {
        els.stepperControlMode.title = "The connected firmware does not support control modes";
      } else if (!d4Off || moving) {
        els.stepperControlMode.title = "Turn D4 OFF and stop motion before changing mode";
      } else {
        els.stepperControlMode.title = "Toggle to apply immediately";
      }
      // Keep this control clickable whenever a request is not already in flight.
      // The handler and server then explain invalid values, D4 state, or transport
      // failures instead of presenting an inert button with no reason.
      els.stepperApplySpeed.disabled = speedRequestPending || webPositionMode || estopLatched;
      if (speedRequestPending) {
        els.stepperApplySpeed.title = "Waiting for the Yún to confirm the new speed";
      } else if (webPositionMode) {
        els.stepperApplySpeed.title = "Move uses the speed field directly in Web Position mode";
      } else if (!Number.isFinite(speed) || speed < 0.1 || speed > 10) {
        els.stepperApplySpeed.title = "Enter a speed from 0.1 through 10.0 mm/s";
      } else if (!connected) {
        els.stepperApplySpeed.title = "Yún USB status is not connected";
      } else if (!speedCommandCapable) {
        els.stepperApplySpeed.title = "The connected firmware does not support speed tuning";
      } else if (!d4Off || moving) {
        els.stepperApplySpeed.title = "Turn D4 OFF before applying a speed";
      } else {
        els.stepperApplySpeed.title = `Apply ${speed.toFixed(1)} mm/s and wait for Yún confirmation`;
      }
    }

    async function hydrate() {
      const historyResponse = await fetch("/api/history?limit=240");
      const historyPayload = await historyResponse.json();
      history = historyPayload.history || [];
      const stateResponse = await fetch("/api/state");
      applyState(await stateResponse.json());
    }

    function startPollingFallback() {
      if (pollTimer) return;
      pollTimer = window.setInterval(async () => {
        try {
          const response = await fetch("/api/latest");
          const payload = await response.json();
          if (payload.sample) applySample(payload.sample);
          if (payload.run) {
            runState = payload.run;
            renderRun();
          }
          setStreamStatus("Polling", "warn");
        } catch (error) {
          setStreamStatus("Offline", "bad");
        }
      }, 1000);
    }

    function connectEvents() {
      if (!window.EventSource) {
        startPollingFallback();
        return;
      }
      const events = new EventSource("/api/events");
      events.addEventListener("open", () => setStreamStatus("Live", "ok"));
      events.addEventListener("sample", event => {
        setStreamStatus("Live", "ok");
        applySample(JSON.parse(event.data));
      });
      events.addEventListener("state", event => {
        runState = JSON.parse(event.data);
        renderRun();
      });
      events.addEventListener("error", () => {
        setStreamStatus("Reconnecting", "warn");
        startPollingFallback();
      });
    }

    els.startRun.addEventListener("click", async () => {
      const payload = await postJson("/api/run/start");
      runState = payload.run;
      renderRun();
    });

    els.stopRun.addEventListener("click", async () => {
      const payload = await postJson("/api/run/stop");
      runState = payload.run;
      renderRun();
    });

    els.exportRun.addEventListener("click", () => {
      window.location.href = "/api/export/latest";
    });

    for (let i = 0; i < solenoidCount; i += 1) {
      els[`sol${i}`].addEventListener("click", async () => {
        const payload = await postJson(`/api/solenoid/toggle?n=${i}`);
        if (payload.sample) applySample(payload.sample);
      });
    }

    els.emergencyStop.addEventListener("click", async () => {
      // Deliberately no confirmation dialog: an emergency stop must be a
      // single action. Confirmation is required only when re-enabling motion.
      try {
        const payload = await postJson("/api/stepper/estop");
        if (payload.sample) applySample(payload.sample);
        stepperMessageSticky = true;
        text("stepperMessage", "Software E-STOP latched; step pulses inhibited");
      } catch (error) {
        stepperMessageSticky = true;
        text("stepperMessage", `Software E-STOP failed: ${error.message}`);
      }
    });

    els.emergencyReset.addEventListener("click", async () => {
      if (!window.confirm("Reset the software E-STOP latch? Physical D4 must be OFF. This re-enables motion commands but does not start motion.")) return;
      try {
        const payload = await postJson("/api/stepper/estop/reset");
        if (payload.sample) applySample(payload.sample);
        stepperMessageSticky = true;
        text("stepperMessage", "Software E-STOP reset; motion remains stopped");
      } catch (error) {
        stepperMessageSticky = true;
        text("stepperMessage", `E-STOP reset rejected: ${error.message}`);
      }
    });

    els.stepperForm.addEventListener("input", updateStepperControls);
    els.stepperSpeed.addEventListener("input", () => {
      stepperMessageSticky = false;
    });
    els.stepperControlMode.addEventListener("change", async () => {
      const webPosition = els.stepperControlMode.checked;
      const previousWebPosition = latest?.stepper_control_mode === "web_position";
      controlModeDirty = true;
      controlModeRequestPending = true;
      stepperMessageSticky = true;
      text("stepperControlModeLabel", webPosition ? "Web Position" : "Local Velocity");
      text("stepperMessage", `Selecting ${webPosition ? "Web Position" : "Local Velocity"}…`);
      updateStepperControls();
      try {
        const payload = await postJson("/api/stepper/control-mode", {web_position: webPosition});
        if (payload.sample) applySample(payload.sample);
        const confirmedMode = payload.stepper?.stepper_control_mode || payload.sample?.stepper_control_mode;
        if (payload.confirmed !== true || confirmedMode !== (webPosition ? "web_position" : "local_velocity")) {
          throw new Error("the Yún did not return the requested control mode");
        }
        text("stepperMessage", webPosition
          ? "Web Position selected; D5 chooses travel direction; D6/D8 stop travel"
          : "Local Velocity selected; D4 runs/stops and D5 selects direction");
      } catch (error) {
        controlModeDirty = false;
        els.stepperControlMode.checked = previousWebPosition;
        text("stepperControlModeLabel", previousWebPosition ? "Web Position" : "Local Velocity");
        text("stepperMessage", `Mode rejected: ${error.message}`);
      } finally {
        controlModeRequestPending = false;
        updateStepperControls();
      }
    });
    els.stepperForm.addEventListener("submit", async event => {
      event.preventDefault();
      const distance = Number(els.stepperDistance.value);
      const speed = Number(els.stepperSpeed.value);
      const selectedDirection = latest?.stepper_authorized_direction === "both"
        ? "forward"
        : latest?.stepper_authorized_direction;
      if (selectedDirection !== "forward" && selectedDirection !== "reverse") {
        text("stepperMessage", "Rejected: D5 direction is unavailable");
        return;
      }
      if ((Math.abs(distance) > 50 || speed > 5) &&
          !window.confirm(`Confirm ${selectedDirection} move: ${distance} mm at ${speed} mm/s?`)) return;
      const body = {
        distance_mm: distance,
        speed_mm_s: speed
      };
      const commandId = els.stepperCommandInput.value.trim();
      if (commandId) body.command_id = commandId;
      try {
        const payload = await postJson("/api/stepper/move", body);
        text("stepperMessage", `${payload.resolved_direction || selectedDirection} move accepted`);
        if (payload.sample) applySample(payload.sample);
      } catch (error) {
        text("stepperMessage", `Rejected: ${error.message}`);
      }
    });

    els.stepperStop.addEventListener("click", async () => {
      try {
        const payload = await postJson("/api/stepper/stop");
        text("stepperMessage", "Motion stopped");
        if (payload.sample) applySample(payload.sample);
      } catch (error) {
        text("stepperMessage", `Stop failed: ${error.message}`);
      }
    });

    els.stepperHome.addEventListener("click", async () => {
      if (!window.confirm("Move toward D8 until its limit switch activates? Speed is fixed at 1.5 mm/s; D4 must be armed and D5 set to Reverse.")) return;
      stepperMessageSticky = true;
      text("stepperMessage", "Moving toward the D8 limit…");
      try {
        const payload = await postJson("/api/stepper/home");
        if (payload.sample) applySample(payload.sample);
        text("stepperMessage", payload.stepper?.stepper_negative_limit_active
          ? "D8 limit reached"
          : "D8-limit move accepted at 1.5 mm/s");
      } catch (error) {
        text("stepperMessage", `D8-limit move rejected: ${error.message}`);
      }
    });

    els.stepperApplySpeed.addEventListener("click", async () => {
      const speed = Number(els.stepperSpeed.value);
      if (!Number.isFinite(speed) || speed < 0.1 || speed > 10) {
        stepperMessageSticky = true;
        text("stepperMessage", "Speed rejected: enter 0.1 through 10.0 mm/s");
        return;
      }
      speedRequestPending = true;
      stepperMessageSticky = true;
      els.stepperApplySpeed.textContent = "Applying…";
      text("stepperMessage", `Sending ${speed.toFixed(1)} mm/s; waiting for Yún confirmation…`);
      updateStepperControls();
      try {
        const payload = await postJson("/api/stepper/speed", {speed_mm_s: speed});
        if (payload.sample) applySample(payload.sample);
        const confirmedSpeed = Number(payload.sample?.stepper_command_speed_mm_s);
        if (payload.confirmed !== true || !Number.isFinite(confirmedSpeed)) {
          throw new Error("the Yún did not return a confirmed configured speed");
        }
        text("stepperMessage", `Confirmed by Yún: ${confirmedSpeed.toFixed(1)} mm/s`);
      } catch (error) {
        text("stepperMessage", `Speed rejected: ${error.message}`);
      } finally {
        speedRequestPending = false;
        els.stepperApplySpeed.textContent = "Apply Local Velocity Speed";
        updateStepperControls();
      }
    });

    els.metadataForm.addEventListener("input", () => text("metadataStatus", "Unsaved"));
    els.metadataForm.addEventListener("submit", async event => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(els.metadataForm).entries());
      const payload = await postJson("/api/metadata", data);
      fillMetadata(payload.metadata);
    });

    window.addEventListener("resize", drawAllCharts);
    hydrate().then(connectEvents).catch(() => {
      setStreamStatus("Offline", "bad");
      startPollingFallback();
    });
  </script>
</body>
</html>
"""


class DashboardRuntime:
    """Realtime selected-source supervisor stream shared by HTTP handlers."""

    def __init__(
        self,
        *,
        scenario: str,
        rate_hz: float,
        drop_after_s: float,
        stale_after_s: float,
        history_limit: int,
        record_dir: Path,
        esp32_source: str,
        esp32_base_url: str = DEFAULT_ESP32_BASE_URL,
        esp32_timeout: float = DEFAULT_ESP32_TIMEOUT_S,
        dxmr90_source: str,
        stepper_source: str,
        stepper_port: str,
        stepper_baud: int,
        dxmr90_host: str,
        dxmr90_port: int,
        dxmr90_unit_id: int,
        dxmr90_timeout: float,
        dxmr90_addressing: str,
        dxmr90_word_order: str,
        dxmr90_data_path: str,
        dxmr90_rate_hz: float,
        stepper_network_url: str = DEFAULT_STEPPER_NETWORK_URL,
        stepper_network_timeout: float = DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        if history_limit < 1:
            raise ValueError("history_limit must be positive")

        self.scenario = scenario
        self.rate_hz = rate_hz
        self.period_s = 1.0 / rate_hz
        self.drop_after_s = drop_after_s
        self.stale_after_s = stale_after_s
        self.record_dir = Path(record_dir)
        self.esp32_source = esp32_source
        self.esp32_base_url = esp32_base_url
        self.esp32_timeout = esp32_timeout
        self.dxmr90_source = dxmr90_source
        self.stepper_source = stepper_source
        self.stepper_port = stepper_port
        self.stepper_baud = stepper_baud
        self.stepper_network_url = stepper_network_url
        self.stepper_network_timeout = stepper_network_timeout
        self.dxmr90_host = dxmr90_host
        self.dxmr90_port = dxmr90_port
        self.dxmr90_unit_id = dxmr90_unit_id
        self.dxmr90_timeout = dxmr90_timeout
        self.dxmr90_addressing = dxmr90_addressing
        self.dxmr90_word_order = dxmr90_word_order
        self.dxmr90_data_path = dxmr90_data_path
        self.dxmr90_rate_hz = dxmr90_rate_hz
        self.sources = make_sources(
            esp32_source=esp32_source,
            esp32_base_url=esp32_base_url,
            esp32_timeout=esp32_timeout,
            dxmr90_source=dxmr90_source,
            stepper_source=stepper_source,
            stepper_port=stepper_port,
            stepper_baud=stepper_baud,
            stepper_network_url=stepper_network_url,
            stepper_network_timeout=stepper_network_timeout,
            scenario=scenario,
            drop_after_s=drop_after_s,
            esp32_auto_sequence=False,
            dxmr90_host=dxmr90_host,
            dxmr90_port=dxmr90_port,
            dxmr90_unit_id=dxmr90_unit_id,
            dxmr90_timeout=dxmr90_timeout,
            dxmr90_addressing=dxmr90_addressing,
            dxmr90_word_order=dxmr90_word_order,
            dxmr90_data_path=dxmr90_data_path,
            dxmr90_rate_hz=dxmr90_rate_hz,
        )
        self.merger = SourceMerger(self.sources, stale_after_s=stale_after_s)
        self.history: deque[dict[str, object]] = deque(maxlen=history_limit)
        self.metadata = dict(DEFAULT_METADATA)
        self.recording = False
        self.run_started_iso: str | None = None
        self.run_stopped_iso: str | None = None
        self.recorder: FlowRunRecorder | None = None
        self.latest_recording: dict[str, object] | None = None
        self.start_time = datetime.now(timezone.utc)
        self.monotonic0 = time.monotonic()
        self.latest: dict[str, object] | None = None
        self.sequence = 0
        self._stop_event = threading.Event()
        self._condition = threading.Condition(threading.RLock())
        self._thread: threading.Thread | None = None
        with self._condition:
            self._poll_locked(0.0)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="dashboard-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.recording:
            self.set_recording(False)
        for source in self.sources:
            if hasattr(source, "close"):
                source.close()  # type: ignore[attr-defined]

    def _run(self) -> None:
        next_emit = time.monotonic()
        while not self._stop_event.is_set():
            elapsed_s = time.monotonic() - self.monotonic0
            with self._condition:
                self._poll_locked(elapsed_s)
            next_emit += self.period_s
            delay = max(0.0, next_emit - time.monotonic())
            self._stop_event.wait(delay)

    def _poll_locked(self, elapsed_s: float) -> None:
        timestamp = self.start_time + timedelta(seconds=elapsed_s)
        self.latest = self.merger.poll(elapsed_s, timestamp)
        fresh_readings = self.merger.fresh_readings()
        self.history.append(self.latest)
        if self.recording and self.recorder is not None:
            self.recorder.record_sample(self.latest, fresh_readings)
        self.sequence += 1
        self._condition.notify_all()

    def run_config_locked(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "rate_hz": self.rate_hz,
            "drop_after_s": self.drop_after_s,
            "stale_after_s": self.stale_after_s,
            "record_dir": str(self.record_dir),
            "esp32_source": self.esp32_source,
            "esp32_base_url": self.esp32_base_url,
            "esp32_timeout": self.esp32_timeout,
            "dxmr90_source": self.dxmr90_source,
            "stepper_source": self.stepper_source,
            "stepper_port": self.stepper_port,
            "stepper_baud": self.stepper_baud,
            "dxmr90_host": self.dxmr90_host,
            "dxmr90_port": self.dxmr90_port,
            "dxmr90_unit_id": self.dxmr90_unit_id,
            "dxmr90_timeout": self.dxmr90_timeout,
            "dxmr90_addressing": self.dxmr90_addressing,
            "dxmr90_word_order": self.dxmr90_word_order,
            "dxmr90_data_path": self.dxmr90_data_path,
            "dxmr90_rate_hz": self.dxmr90_rate_hz,
        }

    def run_state_locked(self) -> dict[str, object]:
        return {
            "recording": self.recording,
            "run_started_iso": self.run_started_iso,
            "run_stopped_iso": self.run_stopped_iso,
            "scenario": self.scenario,
            "rate_hz": self.rate_hz,
            "drop_after_s": self.drop_after_s,
            "stale_after_s": self.stale_after_s,
            "record_dir": str(self.record_dir),
            "esp32_source": self.esp32_source,
            "esp32_base_url": self.esp32_base_url,
            "dxmr90_source": self.dxmr90_source,
            "stepper_source": self.stepper_source,
            "stepper_port": self.stepper_port,
            "stepper_baud": self.stepper_baud,
            "dxmr90_host": self.dxmr90_host,
            "dxmr90_port": self.dxmr90_port,
            "dxmr90_data_path": self.dxmr90_data_path,
            "dxmr90_rate_hz": self.dxmr90_rate_hz,
            "active_recording": (
                self.recorder.status_payload() if self.recorder is not None else None
            ),
            "latest_recording": self.latest_recording,
        }

    def state(self) -> dict[str, object]:
        with self._condition:
            return {
                "sample": self.latest,
                "run": self.run_state_locked(),
                "metadata": dict(self.metadata),
                "history_size": len(self.history),
            }

    def latest_payload(self) -> dict[str, object]:
        with self._condition:
            return {"sample": self.latest, "run": self.run_state_locked()}

    def history_payload(self, limit: int) -> dict[str, object]:
        with self._condition:
            rows = list(self.history)[-max(1, limit) :]
            return {"history": rows, "run": self.run_state_locked()}

    def wait_for_sample(
        self,
        last_sequence: int,
        timeout_s: float = 15.0,
    ) -> tuple[int, dict[str, object] | None]:
        with self._condition:
            if self.sequence <= last_sequence and not self._stop_event.is_set():
                self._condition.wait(timeout=timeout_s)
            if self.sequence <= last_sequence:
                return self.sequence, None
            return self.sequence, self.latest

    def set_recording(self, recording: bool) -> dict[str, object]:
        with self._condition:
            if recording:
                if self.recording:
                    return self.run_state_locked()
                source_fields = {
                    source.name: source.expected_fields for source in self.sources
                }
                self.recorder = FlowRunRecorder(
                    record_dir=self.record_dir,
                    metadata=self.metadata,
                    run_config=self.run_config_locked(),
                    source_fields=source_fields,
                    first_sample=self.latest,
                )
                self.recording = True
                self.run_started_iso = self.recorder.started_iso
                self.run_stopped_iso = None
                self.latest_recording = None
            else:
                if not self.recording:
                    return self.run_state_locked()
                recorder = self.recorder
                self.recording = False
                if recorder is not None:
                    self.latest_recording = recorder.finish(self.metadata)
                    stopped = self.latest_recording.get("stopped_iso")
                    self.run_stopped_iso = str(stopped) if stopped is not None else None
                else:
                    self.run_stopped_iso = datetime.now(timezone.utc).isoformat(
                        timespec="milliseconds"
                    )
                self.recorder = None
            self._condition.notify_all()
            return self.run_state_locked()

    def update_metadata(self, values: dict[str, object]) -> dict[str, str]:
        with self._condition:
            for key in DEFAULT_METADATA:
                if key in values:
                    value = values[key]
                    self.metadata[key] = "" if value is None else str(value)
            if self.recorder is not None:
                self.recorder.update_metadata(self.metadata)
            self._condition.notify_all()
            return dict(self.metadata)

    def toggle_solenoid(self, index: int) -> dict[str, object]:
        with self._condition:
            esp32 = next(
                (source for source in self.sources if source.name == "esp32"),
                None,
            )
            if esp32 is None or not hasattr(esp32, "toggle_solenoid"):
                raise RuntimeError("ESP32 source does not support solenoid controls")
            state = esp32.toggle_solenoid(index)  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            states = (
                list(esp32.solenoid_states())  # type: ignore[attr-defined]
                if hasattr(esp32, "solenoid_states")
                else None
            )
            return {
                "index": index,
                "state": state,
                "solenoids": states,
                "sample": self.latest,
            }

    def _stepper_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "move"):
            raise RuntimeError("stepper source does not support motion controls")
        return stepper

    def _stepper_speed_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "set_speed"):
            raise RuntimeError("stepper source does not support manual speed tuning")
        return stepper

    def _stepper_mode_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "set_control_mode"):
            raise RuntimeError("stepper source does not support control modes")
        return stepper

    def _stepper_home_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "home"):
            raise RuntimeError("stepper source does not support Home")
        return stepper

    def _stepper_estop_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if (
            stepper is None
            or not hasattr(stepper, "emergency_stop")
            or not hasattr(stepper, "reset_emergency_stop")
        ):
            raise RuntimeError("stepper source does not support software E-STOP")
        return stepper

    def _stepper_payload_locked(self) -> dict[str, object]:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None:
            raise RuntimeError("stepper source is unavailable")
        payload: dict[str, object] = {
            "stepper_mode": stepper.mode,
            "stepper_connected": False,
            "stepper_age_ms": None,
        }
        if self.latest is not None:
            for key, value in self.latest.items():
                if key.startswith("stepper_"):
                    payload[key] = value
        if hasattr(stepper, "status"):
            payload.update(stepper.status())  # type: ignore[attr-defined]
        return payload

    def stepper_status(self) -> dict[str, object]:
        with self._condition:
            return {
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def move_stepper(self, values: dict[str, object]) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_locked()
            if "distance_mm" not in values:
                raise ValueError("distance_mm is required")
            if "speed_mm_s" not in values:
                raise ValueError("speed_mm_s is required")
            raw_distance = values["distance_mm"]
            if isinstance(raw_distance, bool):
                raise ValueError("distance_mm must be a positive finite travel magnitude")
            try:
                travel_mm = float(raw_distance)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "distance_mm must be a positive finite travel magnitude"
                ) from exc
            if not math.isfinite(travel_mm) or travel_mm <= 0:
                raise ValueError("distance_mm must be a positive finite travel magnitude")

            # Snapshot the physical D5 selection and turn the operator's
            # positive magnitude into the signed internal/wire command. The
            # USB adapter and firmware both re-check D5, so a selector change
            # during this handoff rejects or aborts instead of reversing.
            current = self._stepper_payload_locked()
            selected_direction = current.get("stepper_authorized_direction")
            if selected_direction == "both":
                # Simulation has no physical D5 input; use Forward by default.
                selected_direction = "forward"
            if selected_direction not in ("forward", "reverse"):
                raise RuntimeError("D5 direction is unavailable")
            signed_distance_mm = (
                -travel_mm if selected_direction == "reverse" else travel_mm
            )
            command_id = values.get("command_id")
            before_sequence = current.get("stepper_status_sequence")
            stepper.move(  # type: ignore[attr-defined]
                signed_distance_mm,
                values["speed_mm_s"],
                command_id,
            )
            expected_id = getattr(stepper, "pending_command_id", None)
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_command_id") == expected_id
                        and payload.get("stepper_state")
                        in ("moving", "completed", "limit_blocked")
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm the move within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
                "travel_mm": travel_mm,
                "resolved_direction": selected_direction,
                "signed_distance_mm": signed_distance_mm,
            }

    def stop_stepper(self) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_locked()
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.stop()  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_moving") is False
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm Stop within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def emergency_stop_stepper(self) -> dict[str, object]:
        """Dispatch E-STOP before any blocking source poll, then confirm it.

        This command deliberately bypasses the runtime condition for its first
        write. A slow or unreachable DXMR90 poll may hold that shared lock, but
        it must not delay delivery of the short E-STOP command to the Yún.
        Status acknowledgement still uses the normal merged-data condition.
        """

        stepper = self._stepper_estop_locked()
        status = (
            stepper.status()  # type: ignore[attr-defined]
            if hasattr(stepper, "status")
            else {}
        )
        before_sequence = status.get("stepper_status_sequence")
        stepper.emergency_stop()  # type: ignore[attr-defined]

        with self._condition:
            elapsed_s = time.monotonic() - self.monotonic0
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_estop_latched") is True
                        and payload.get("stepper_moving") is False
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm software E-STOP within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            else:
                self._poll_locked(elapsed_s)
            return {
                "confirmed": True,
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def reset_stepper_emergency_stop(self) -> dict[str, object]:
        """Reset the software latch and require fresh device confirmation."""

        with self._condition:
            stepper = self._stepper_estop_locked()
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.reset_emergency_stop()  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_estop_latched") is False
                        and payload.get("stepper_moving") is False
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm E-STOP reset within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "confirmed": True,
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def set_stepper_control_mode(
        self,
        values: dict[str, object],
    ) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_mode_locked()
            if "web_position" not in values:
                raise ValueError("web_position is required")
            requested = values["web_position"]
            if not isinstance(requested, bool):
                raise ValueError("web_position must be true or false")
            expected_mode = "web_position" if requested else "local_velocity"
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.set_control_mode(requested)  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_control_mode") == expected_mode
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm the control mode within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "confirmed": True,
                "requested_control_mode": expected_mode,
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def home_stepper(self) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_home_locked()
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.home()  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_state") in ("homing", "ready")
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm Home within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "confirmed": True,
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def set_stepper_speed(self, values: dict[str, object]) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_speed_locked()
            if "speed_mm_s" not in values:
                raise ValueError("speed_mm_s is required")
            requested_speed = values["speed_mm_s"]
            if isinstance(requested_speed, bool):
                raise ValueError("speed_mm_s must be a finite number")
            try:
                requested_speed_value = float(requested_speed)
                expected_speed = (
                    round(requested_speed_value * DEFAULT_STEPPER_STEPS_PER_MM)
                    / DEFAULT_STEPPER_STEPS_PER_MM
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("speed_mm_s must be a finite number") from exc
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.set_speed(values["speed_mm_s"])  # type: ignore[attr-defined]

            # A successful serial write only proves that bytes entered the USB
            # driver. Do not tell the browser the change succeeded until a new
            # firmware status frame echoes the requested configured speed.
            deadline = time.monotonic() + 1.5
            while True:
                payload = self._stepper_payload_locked()
                echoed_speed = payload.get("stepper_command_speed_mm_s")
                echoed_sequence = payload.get("stepper_status_sequence")
                if (
                    isinstance(echoed_speed, (int, float))
                    and not isinstance(echoed_speed, bool)
                    and abs(float(echoed_speed) - expected_speed) < 0.0001
                    and echoed_sequence != before_sequence
                ):
                    return {
                        "confirmed": True,
                        "requested_speed_mm_s": expected_speed,
                        "stepper": payload,
                        "sample": self.latest,
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Yún did not confirm the requested speed within 1.5 seconds"
                    )
                # Condition.wait releases the runtime lock, allowing the 10 Hz
                # source thread to ingest the firmware acknowledgement.
                self._condition.wait(timeout=min(remaining, 0.1))

    def recordings_payload(self) -> dict[str, object]:
        with self._condition:
            recordings = list_recordings(self.record_dir)
            latest = self.latest_recording
            if latest is None and recordings:
                latest = recordings[0]
            return {
                "record_dir": str(self.record_dir),
                "active": (
                    self.recorder.status_payload() if self.recorder is not None else None
                ),
                "latest": latest,
                "recordings": recordings,
            }

    def artifact_path(self, run_id: str, filename: str) -> Path:
        return resolve_artifact(self.record_dir, run_id, filename)

    def latest_export_path(self) -> Path:
        with self._condition:
            latest = self.latest_recording
            if latest is None:
                recordings = list_recordings(self.record_dir)
                latest = recordings[0] if recordings else None
            if not latest:
                raise FileNotFoundError("no completed recording is available")
            run_id = latest.get("run_id")
            if not isinstance(run_id, str):
                raise FileNotFoundError("latest recording has no run_id")
        return self.artifact_path(run_id, "export.csv")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True


def parse_body(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" in content_type:
        payload = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload
    decoded = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in decoded.items()}


def build_handler(runtime: DashboardRuntime, quiet: bool) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "FlowSupervisorHTTP/0.1"

        def log_message(self, fmt: str, *args: object) -> None:
            if not quiet:
                super().log_message(fmt, *args)

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path == "/":
                    self._send_html(INDEX_HTML)
                elif path == "/api/state":
                    self._send_json(runtime.state())
                elif path == "/api/latest":
                    self._send_json(runtime.latest_payload())
                elif path == "/api/history":
                    limit = int(query.get("limit", ["240"])[0])
                    self._send_json(runtime.history_payload(limit))
                elif path == "/api/stepper/status":
                    self._send_json(runtime.stepper_status())
                elif path == "/api/recordings":
                    self._send_json(runtime.recordings_payload())
                elif path == "/api/export/latest":
                    self._send_file(runtime.latest_export_path(), download_name="export.csv")
                elif path == "/api/export":
                    run_id = query.get("run_id", [""])[0]
                    filename = query.get("file", ["export.csv"])[0]
                    self._send_file(runtime.artifact_path(run_id, filename))
                elif path == "/api/events":
                    self._send_events()
                else:
                    self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except FileNotFoundError as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.NOT_FOUND,
                )

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path == "/api/run/start":
                    self._send_json({"run": runtime.set_recording(True)})
                elif path == "/api/run/stop":
                    self._send_json({"run": runtime.set_recording(False)})
                elif path == "/api/metadata":
                    metadata = runtime.update_metadata(parse_body(self))
                    self._send_json({"metadata": metadata})
                elif path == "/api/solenoid/toggle":
                    index = int(query.get("n", [""])[0])
                    self._send_json(runtime.toggle_solenoid(index))
                elif path == "/api/stepper/move":
                    self._send_json(runtime.move_stepper(parse_body(self)))
                elif path == "/api/stepper/stop":
                    self._send_json(runtime.stop_stepper())
                elif path == "/api/stepper/estop":
                    self._send_json(runtime.emergency_stop_stepper())
                elif path == "/api/stepper/estop/reset":
                    self._send_json(runtime.reset_stepper_emergency_stop())
                elif path == "/api/stepper/home":
                    self._send_json(runtime.home_stepper())
                elif path == "/api/stepper/control-mode":
                    self._send_json(
                        runtime.set_stepper_control_mode(parse_body(self))
                    )
                elif path == "/api/stepper/speed":
                    self._send_json(runtime.set_stepper_speed(parse_body(self)))
                else:
                    self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except FileNotFoundError as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.NOT_FOUND,
                )
            except RuntimeError as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.CONFLICT,
                )

        def _send_html(self, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_json(
            self,
            payload: dict[str, object],
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, download_name: str | None = None) -> None:
            if not path.exists() or not path.is_file():
                self._send_json(
                    {"error": f"artifact not found: {path.name}"},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            suffix = path.suffix.lower()
            content_type = "application/json" if suffix == ".json" else "text/csv"
            payload = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            name = download_name or path.name
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            self.wfile.write(payload)

        def _send_sse_event(self, event: str, payload: dict[str, object]) -> None:
            body = (
                f"event: {event}\n"
                f"data: {json.dumps(payload, separators=(',', ':'), sort_keys=True)}\n\n"
            )
            self.wfile.write(body.encode("utf-8"))
            self.wfile.flush()

        def _send_events(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            sequence = 0
            try:
                state_payload = runtime.state()
                run_payload = state_payload.get("run", {})
                if isinstance(run_payload, dict):
                    self._send_sse_event("state", run_payload)
                while True:
                    sequence, sample = runtime.wait_for_sample(sequence)
                    if sample is None:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        continue
                    self._send_sse_event("sample", sample)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the selected-source flow-management dashboard and JSON API."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port")
    parser.add_argument("--rate-hz", type=float, default=DEFAULT_RATE_HZ, help="sample rate")
    parser.add_argument(
        "--scenario",
        choices=SIMULATION_SCENARIOS,
        default="healthy",
        help="simulated source scenario",
    )
    parser.add_argument(
        "--esp32-source",
        choices=SOURCE_MODES,
        default="sim",
        help="ESP32 source mode",
    )
    parser.add_argument(
        "--esp32-url",
        default=DEFAULT_ESP32_BASE_URL,
        help="base URL for the headless ESP32 API when --esp32-source real",
    )
    parser.add_argument(
        "--esp32-timeout",
        type=float,
        default=DEFAULT_ESP32_TIMEOUT_S,
        help="ESP32 SSE connection/read and command timeout in seconds",
    )
    parser.add_argument(
        "--dxmr90-source",
        choices=SOURCE_MODES,
        default="sim",
        help="SICK/DXMR90 source mode",
    )
    parser.add_argument(
        "--stepper-source",
        choices=STEPPER_SOURCE_MODES,
        default="sim",
        help="Yún stepper source mode",
    )
    parser.add_argument(
        "--stepper-port",
        default=DEFAULT_STEPPER_USB_PORT,
        help="USB Serial device used when --stepper-source usb",
    )
    parser.add_argument(
        "--stepper-baud",
        type=int,
        default=DEFAULT_STEPPER_USB_BAUD,
        help="USB Serial baud used when --stepper-source usb",
    )
    parser.add_argument(
        "--stepper-url",
        default=DEFAULT_STEPPER_NETWORK_URL,
        help="Yún Linux bridge base URL when --stepper-source network",
    )
    parser.add_argument(
        "--stepper-timeout",
        type=float,
        default=DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        help="Yún network status/command timeout in seconds",
    )
    parser.add_argument(
        "--dxmr90-host",
        default=DEFAULT_DXMR90_HOST,
        help="SICK/DXMR90 Modbus host",
    )
    parser.add_argument(
        "--dxmr90-port",
        type=int,
        default=DEFAULT_DXMR90_PORT,
        help="SICK/DXMR90 Modbus TCP port",
    )
    parser.add_argument(
        "--dxmr90-unit-id",
        type=int,
        default=DEFAULT_DXMR90_UNIT_ID,
        help="SICK/DXMR90 Modbus unit id",
    )
    parser.add_argument(
        "--dxmr90-timeout",
        type=float,
        default=1.0,
        help="SICK/DXMR90 socket timeout in seconds",
    )
    parser.add_argument(
        "--dxmr90-addressing",
        choices=("one-based", "zero-based"),
        default="one-based",
        help="SICK/DXMR90 register addressing convention",
    )
    parser.add_argument(
        "--dxmr90-word-order",
        choices=("high-low", "low-high"),
        default="high-low",
        help="SICK/DXMR90 float32 word order",
    )
    parser.add_argument(
        "--dxmr90-data-path",
        choices=DXMR90_DATA_PATHS,
        default="direct",
        help="direct SICK process data or ScriptBasic republished registers",
    )
    parser.add_argument(
        "--dxmr90-rate-hz",
        type=float,
        default=DEFAULT_DXMR90_REAL_RATE_HZ,
        help="real SICK/DXMR90 Modbus polling rate",
    )
    parser.add_argument(
        "--drop-after-s",
        type=float,
        default=DEFAULT_DROP_AFTER_S,
        help="elapsed seconds before stale scenarios stop emitting source updates",
    )
    parser.add_argument(
        "--stale-after-s",
        type=float,
        default=DEFAULT_STALE_AFTER_S,
        help="seconds after the latest source update before connected flips false",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_HISTORY_LIMIT,
        help="number of samples to keep in memory for the dashboard",
    )
    parser.add_argument(
        "--record-dir",
        type=Path,
        default=DEFAULT_RECORD_DIR,
        help="directory for disk-backed run artifacts",
    )
    parser.add_argument(
        "--verbose-http",
        action="store_true",
        help="print HTTP access logs",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.port < 1 or args.port > 65535:
        print("--port must be between 1 and 65535", file=sys.stderr)
        return 2
    if args.drop_after_s < 0:
        print("--drop-after-s must be non-negative", file=sys.stderr)
        return 2
    if args.stale_after_s < 0:
        print("--stale-after-s must be non-negative", file=sys.stderr)
        return 2
    if args.esp32_timeout <= 0:
        print("--esp32-timeout must be positive", file=sys.stderr)
        return 2
    if args.dxmr90_port < 1 or args.dxmr90_port > 65535:
        print("--dxmr90-port must be between 1 and 65535", file=sys.stderr)
        return 2
    if args.dxmr90_timeout <= 0:
        print("--dxmr90-timeout must be positive", file=sys.stderr)
        return 2
    if args.stepper_timeout <= 0:
        print("--stepper-timeout must be positive", file=sys.stderr)
        return 2
    if args.dxmr90_rate_hz <= 0:
        print("--dxmr90-rate-hz must be positive", file=sys.stderr)
        return 2

    try:
        runtime = DashboardRuntime(
            scenario=args.scenario,
            rate_hz=args.rate_hz,
            drop_after_s=args.drop_after_s,
            stale_after_s=args.stale_after_s,
            history_limit=args.history_limit,
            record_dir=args.record_dir,
            esp32_source=args.esp32_source,
            esp32_base_url=args.esp32_url,
            esp32_timeout=args.esp32_timeout,
            dxmr90_source=args.dxmr90_source,
            stepper_source=args.stepper_source,
            stepper_port=args.stepper_port,
            stepper_baud=args.stepper_baud,
            stepper_network_url=args.stepper_url,
            stepper_network_timeout=args.stepper_timeout,
            dxmr90_host=args.dxmr90_host,
            dxmr90_port=args.dxmr90_port,
            dxmr90_unit_id=args.dxmr90_unit_id,
            dxmr90_timeout=args.dxmr90_timeout,
            dxmr90_addressing=args.dxmr90_addressing,
            dxmr90_word_order=args.dxmr90_word_order,
            dxmr90_data_path=args.dxmr90_data_path,
            dxmr90_rate_hz=args.dxmr90_rate_hz,
        )
    except (NotImplementedError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    runtime.start()
    handler = build_handler(runtime, quiet=not args.verbose_http)
    server = DashboardServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Serving flow-management dashboard at {url}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server", file=sys.stderr)
    finally:
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
