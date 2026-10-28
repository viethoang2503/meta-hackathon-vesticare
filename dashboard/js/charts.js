/**
 * charts.js — SVM rolling line chart (Chart.js).
 *
 * Shows the last MAX_POINTS SVM samples as a waveform.
 * Threshold reference lines (SWAY=1.5g, FALL=2.5g) are drawn as
 * constant datasets so no extra plugin is needed.
 */

// ~10 s at 50 Hz.
const MAX_POINTS = 500;

const THRESHOLDS = {
  SWAY: 1.5,
  FALL: 2.5,
};

let _chart = null;
let _accelChart = null;
let _gyroChart = null;

const IMU_SERIES = {
  accel: [
    { key: "ax", color: "#ef4444" },
    { key: "ay", color: "#22c55e" },
    { key: "az", color: "#3b82f6" },
  ],
  gyro: [
    { key: "gx", color: "#f97316" },
    { key: "gy", color: "#a855f7" },
    { key: "gz", color: "#06b6d4" },
  ],
};

function initSvmChart(canvasId) {
  const ctx = document.getElementById(canvasId).getContext("2d");

  _chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: new Array(MAX_POINTS).fill(""),
      datasets: [
        {
          label: "SVM (g)",
          data: new Array(MAX_POINTS).fill(null),
          borderColor: "#64748b",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.15,
          fill: false,
          order: 1,
        },
        {
          label: "FALL 2.5g",
          data: new Array(MAX_POINTS).fill(THRESHOLDS.FALL),
          borderColor: "rgba(239,68,68,0.45)",
          borderWidth: 1,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
          order: 2,
        },
        {
          label: "SWAY 1.5g",
          data: new Array(MAX_POINTS).fill(THRESHOLDS.SWAY),
          borderColor: "rgba(245,158,11,0.45)",
          borderWidth: 1,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
          order: 3,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "none" },
      scales: {
        x: { display: false },
        y: {
          min: 0,
          max: 5,
          title: { display: true, text: "SVM (g)", color: "#94a3b8", font: { size: 11 } },
          ticks: { color: "#94a3b8", stepSize: 1 },
          grid: { color: "rgba(148,163,184,0.1)" },
        },
      },
      plugins: {
        legend: {
          display: true,
          labels: { color: "#94a3b8", boxWidth: 20, font: { size: 11 } },
        },
        tooltip: { enabled: false },
      },
    },
  });

  return _chart;
}

/**
 * Push one SVM data point. Shifts the window when the buffer is full.
 * @param {number} svm - current sample SVM in g
 */
function pushSvmPoint(svm) {
  if (!_chart) return;
  const svmData = _chart.data.datasets[0].data;
  svmData.push(svm);
  if (svmData.length > MAX_POINTS) svmData.shift();

  // Keep threshold lines at the same length as the SVM dataset
  for (let i = 1; i <= 2; i++) {
    const td = _chart.data.datasets[i].data;
    td.push(td[0]);
    if (td.length > MAX_POINTS) td.shift();
  }

  _chart.data.labels.push("");
  if (_chart.data.labels.length > MAX_POINTS) _chart.data.labels.shift();

  _chart.update("none");
}

/**
 * Update the SVM line color to reflect the current detection state.
 * @param {"NORMAL"|"SWAY"|"FALL"|"BUFFERING"} state
 */
function setChartStateColor(state) {
  if (!_chart) return;
  const colors = {
    NORMAL: "#22c55e",
    SWAY: "#f59e0b",
    FALL: "#ef4444",
    BUFFERING: "#64748b",
  };
  _chart.data.datasets[0].borderColor = colors[state] ?? "#64748b";
  _chart.update("none");
}

function _createImuChart(canvasId, series, unit) {
  const ctx = document.getElementById(canvasId).getContext("2d");
  return new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: series.map(({ key, color }) => ({
        label: key,
        data: [],
        borderColor: color,
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.15,
        fill: false,
      })),
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "none" },
      scales: {
        x: { display: false },
        y: {
          title: { display: true, text: unit, color: "#94a3b8", font: { size: 11 } },
          ticks: { color: "#94a3b8" },
          grid: { color: "rgba(148,163,184,0.1)" },
        },
      },
      plugins: {
        legend: {
          labels: { color: "#94a3b8", boxWidth: 20, font: { size: 11 } },
        },
        tooltip: { enabled: false },
      },
    },
  });
}

function initImuCharts() {
  _accelChart = _createImuChart("accel-chart", IMU_SERIES.accel, "g");
  _gyroChart = _createImuChart("gyro-chart", IMU_SERIES.gyro, "rad/s");
}

function _pushChartSamples(chart, series, samples) {
  if (!chart || samples.length === 0) return;
  for (const sample of samples) {
    chart.data.labels.push("");
    series.forEach(({ key }, index) => chart.data.datasets[index].data.push(sample[key]));
  }
  if (chart.data.labels.length > MAX_POINTS) {
    const overflow = chart.data.labels.length - MAX_POINTS;
    chart.data.labels.splice(0, overflow);
    chart.data.datasets.forEach((dataset) => dataset.data.splice(0, overflow));
  }
  chart.update("none");
}

function pushImuSamples(samples) {
  _pushChartSamples(_accelChart, IMU_SERIES.accel, samples);
  _pushChartSamples(_gyroChart, IMU_SERIES.gyro, samples);
}
