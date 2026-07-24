import {UI_CONFIG} from "./config.js";

function themeColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartBounds(history, series) {
  const values = [];
  for (const sample of history) {
    for (const item of series) {
      const value = Number(sample[item.key]);
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

function drawChart(canvasId, series, history) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) throw new Error(`Dashboard chart #${canvasId} is missing`);
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
  ctx.fillStyle = themeColor("--chart-bg");
  ctx.fillRect(0, 0, width, height);
  const padL = 68 * ratio;
  const padR = 18 * ratio;
  const padT = 18 * ratio;
  const padB = 34 * ratio;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  ctx.strokeStyle = themeColor("--chart-grid");
  ctx.lineWidth = 1 * ratio;
  ctx.fillStyle = themeColor("--chart-label");
  ctx.font = `${16 * ratio}px system-ui, sans-serif`;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";

  const [min, max] = chartBounds(history, series);
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
    ctx.strokeStyle = themeColor(item.color);
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

export function drawAllCharts(history) {
  for (const [canvasId, series] of Object.entries(UI_CONFIG.charts)) {
    drawChart(canvasId, series, history);
  }
}
