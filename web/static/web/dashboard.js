// Organizer dashboard charts (Chart.js 4). Data comes from the page via json_script.
// Mark specs: bars <= 24px with a 4px rounded data end (square at the baseline),
// 2px lines, hairline recessive gridlines, one y-axis per chart, tooltips on hover.
(function () {
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  const css = getComputedStyle(document.documentElement);
  const token = (name) => css.getPropertyValue(name).trim();
  const SERIES_1 = token("--ff-series-1");
  const SERIES_2 = token("--ff-series-2");
  const GRID = token("--ff-grid");
  const AXIS = token("--ff-axis");
  const MUTED = token("--ff-muted");
  const INK = token("--ff-ink");

  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.color = MUTED;
  Chart.defaults.plugins.legend.display = false; // legends are drawn in HTML beside the title
  Chart.defaults.plugins.tooltip.backgroundColor = INK;
  Chart.defaults.maintainAspectRatio = false;

  const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
  const rupees = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

  function valueAxis(extra = {}) {
    return {
      beginAtZero: true,
      grid: { color: GRID, lineWidth: 1, drawTicks: false },
      border: { display: false },
      ticks: { padding: 6, callback: (v) => compact.format(v) },
      ...extra,
    };
  }
  function categoryAxis(extra = {}) {
    return { grid: { display: false }, border: { color: AXIS }, ticks: { padding: 4 }, ...extra };
  }
  function bars(values, label) {
    return {
      label, data: values, backgroundColor: SERIES_1,
      maxBarThickness: 24, borderRadius: 4, borderSkipped: "start",
    };
  }

  // Tickets per day (columns) + 3-day moving average (line) — same unit, one axis.
  new Chart(document.getElementById("chart-daily"), {
    data: {
      labels: data.dailySales.labels,
      datasets: [
        { type: "line", label: "3-day moving average", data: data.dailySales.movingAvg,
          borderColor: SERIES_2, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
          pointHoverBorderColor: "#fcfcfb", pointHoverBorderWidth: 2, tension: 0.25, order: 0 },
        { type: "bar", ...bars(data.dailySales.tickets, "Tickets that day"), order: 1 },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: { x: categoryAxis({ ticks: { maxTicksLimit: 12, autoSkip: true } }), y: valueAxis() },
    },
  });

  // Revenue by event: horizontal bars (long names read better on the y-axis).
  new Chart(document.getElementById("chart-revenue"), {
    type: "bar",
    data: { labels: data.revenueByEvent.labels, datasets: [bars(data.revenueByEvent.values, "Revenue")] },
    options: {
      indexAxis: "y",
      scales: {
        x: valueAxis({ ticks: { callback: (v) => "₹" + compact.format(v) } }),
        y: categoryAxis({ ticks: { autoSkip: false } }),
      },
      plugins: { tooltip: { callbacks: { label: (c) => rupees.format(c.parsed.x) } } },
    },
  });

  // Payment share: horizontal bars of % (part-to-whole for a handful of modes).
  new Chart(document.getElementById("chart-payments"), {
    type: "bar",
    data: { labels: data.paymentShare.labels, datasets: [bars(data.paymentShare.values, "Share of revenue")] },
    options: {
      indexAxis: "y",
      scales: { x: valueAxis({ max: 100, ticks: { callback: (v) => v + "%" } }), y: categoryAxis() },
      plugins: { tooltip: { callbacks: { label: (c) => c.parsed.x + "% of revenue" } } },
    },
  });

  // Check-ins per hour.
  new Chart(document.getElementById("chart-checkins"), {
    type: "bar",
    data: { labels: data.checkinsByHour.labels, datasets: [bars(data.checkinsByHour.values, "Check-ins")] },
    options: { scales: { x: categoryAxis({ ticks: { maxRotation: 60, autoSkip: true } }), y: valueAxis() } },
  });

  // Lead-time buckets.
  new Chart(document.getElementById("chart-lead"), {
    type: "bar",
    data: { labels: data.leadTime.labels, datasets: [bars(data.leadTime.values, "Bookings")] },
    options: { scales: { x: categoryAxis(), y: valueAxis() } },
  });
})();
