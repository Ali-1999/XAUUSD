"use strict";

const el = (id) => document.getElementById(id);
const set = (id, text) => { el(id).textContent = text; };
const number = (n) => typeof n === "number" && Number.isFinite(n);
const price = (n) => number(n) ? n.toFixed(2) : "—";
const usd = (n, signed = false) => number(n) ? new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", signDisplay: signed ? "exceptZero" : "auto",
}).format(n) : "—";
const utc = (time) => number(time) ? new Date(time * 1000).toISOString().slice(5, 19).replace("T", " ") : "—";
const side = (value) => value === "buy" ? "Buy" : "Sell";

const charts = window.LightweightCharts;
const chart = charts.createChart(el("chart"), {
  autoSize: true,
  layout: { background: { color: "#111d2a" }, textColor: "#a1afbf", attributionLogo: true },
  grid: { vertLines: { color: "#1b2c3b" }, horzLines: { color: "#1b2c3b" } },
  timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#283747" },
  rightPriceScale: { borderColor: "#283747" },
});
const line = chart.addSeries(charts.LineSeries, { color: "#efc579", lineWidth: 2, priceLineVisible: false });
const candles = chart.addSeries(charts.CandlestickSeries, {
  visible: false, upColor: "#64d5ae", downColor: "#ec8a8d", wickUpColor: "#64d5ae", wickDownColor: "#ec8a8d",
  borderVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.01 },
});
let priceLines = [];
let seriesName = "samples";
let current = null;
let busy = false;
let renderedTime = null;

function chartData(data, reset = false) {
  el("candles").disabled = data.bars.length === 0;
  const useCandles = seriesName === "candles";
  line.applyOptions({ visible: !useCandles });
  candles.applyOptions({ visible: useCandles });
  line.setData(data.points);
  candles.setData(data.bars);
  for (const level of priceLines) candles.removePriceLine(level);
  priceLines = data.analysis.levels.map((level) => candles.createPriceLine({
    price: level.price, color: level.side === "buy" ? "#64d5ae" : "#efc579",
    lineWidth: 1, lineStyle: charts.LineStyle.Dashed, axisLabelVisible: true,
    title: level.side === "buy" ? "V" : "A",
  }));
  const visible = useCandles ? data.bars : data.points;
  el("empty").hidden = visible.length > 0;
  set("chart-caption", useCandles ? "Completed M15 candles from spot samples · UTC" : "Gold API spot samples · UTC");
  const last = visible.at(-1)?.time;
  if (last !== renderedTime || reset) chart.timeScale().fitContent();
  renderedTime = last;
}

function renderJournal(events) {
  const body = el("events");
  body.replaceChildren();
  if (!events.length) {
    const cell = body.insertRow().insertCell();
    cell.colSpan = 3;
    cell.textContent = "Waiting for the first qualifying setup";
    return;
  }
  for (const event of events) {
    const row = body.insertRow();
    const state = { entry: "Opened", target: "Target", stop: "Stop", skip: "Skipped" }[event.type] ?? event.type;
    const detail = event.detail ?? (event.type === "entry"
      ? event.lots + " lots at " + price(event.entry) + " · SL " + price(event.stop) + " · TP " + price(event.target)
      : "Exit " + price(event.price) + " · " + usd(event.pnl, true));
    for (const text of [utc(event.time), state + " · " + side(event.side), detail]) row.insertCell().textContent = text;
  }
}

function render(data) {
  current = data;
  const q = data.quote;
  const a = data.account;
  const setup = data.analysis;
  set("price", usd(q?.price));
  set("change", number(q?.change) ? usd(q.change, true) : "");
  el("change").className = q?.change > 0 ? "up" : q?.change < 0 ? "down" : "";
  const states = { live: "Live spot", degraded: "Feed issue", stale: "Stale quote", starting: "Waiting for data" };
  set("status", states[data.status] ?? "Waiting for data");
  el("status").className = "status " + data.status;
  set("timestamp", q ? utc(q.time) + " UTC · " + data.quoteAge + "s old" : "Waiting for provider timestamp");
  const backgroundAt = data.backgroundAt ?? data.cronAt;
  const background = number(backgroundAt) && data.asOf - backgroundAt < 150;
  set("heartbeat", background ? "Background checks active" : "Refreshes while open");
  el("alert").hidden = !data.message;
  set("alert", data.message ?? "");
  set("equity", usd(a.equity));
  set("daily-change", number(a.dailyChange) ? usd(a.dailyChange, true) + " this UTC day" : "UTC day change");
  set("base", usd(a.base));
  set("utc-day", a.date ? a.date + " UTC · first observed equity" : "First observed equity · UTC");
  set("budget", usd(a.riskBudget));
  set("entries", a.entries + " / " + data.model.maxEntries);
  set("closed", a.halted ? "Daily cutoff reached" : a.closed + " closed simulated trades");

  const notes = {
    warmup: "Collecting H4 history · " + Math.min(setup.h4Count, 2) + " / 2 complete H4 candles",
    watch: "Watching for a first visit to a fresh H4 level",
    break: side(setup.activeSide) + " rejection confirmed · waiting for a break",
    retest: side(setup.activeSide) + " break confirmed · waiting for a retest",
    gap: "Waiting for contiguous M15 candle coverage",
  };
  set("analysis-text", notes[setup.stage] ?? notes.warmup);
  el("stage-watch").className = setup.stage === "watch" ? "active" : ["break", "retest"].includes(setup.stage) ? "done" : "";
  el("stage-break").className = setup.stage === "break" ? "active" : setup.stage === "retest" ? "done" : "";
  el("stage-retest").className = setup.stage === "retest" ? "active" : "";
  const level = setup.levels[0];
  set("level", level ? (level.side === "buy" ? "V " : "A ") + price(level.price) : "—");
  set("structure", ["break", "retest"].includes(setup.stage) ? price(setup.structure) : "—");
  const s = setup.lastSignal;
  set("signal", s ? side(s.side) + " · " + utc(s.time) : "—");
  const pos = data.position;
  set("position", pos ? side(pos.side) + " · " + pos.lots + " lots" : "Flat");
  el("position").title = pos ? "SL " + price(pos.stop) + " · TP " + price(pos.target) : "";
  set("sampling", data.barCount + " complete M15 · " + data.sampleCount + " samples this bar");
  chartData(data);
  renderJournal(data.events);
}

async function refresh() {
  if (busy) return;
  busy = true;
  el("refresh").disabled = true;
  try {
    const response = await fetch("/api/refresh", { method: "POST", cache: "no-store", signal: AbortSignal.timeout(12_000) });
    if (!response.ok) throw new Error("HTTP " + response.status);
    render(await response.json());
  } catch (error) {
    set("status", "Connection issue");
    el("status").className = "status";
    set("alert", "Unable to refresh the cloud bot (" + error.message + ").");
    el("alert").hidden = false;
  } finally {
    busy = false;
    el("refresh").disabled = false;
  }
}

for (const name of ["samples", "candles"]) {
  el(name).addEventListener("click", () => {
    seriesName = name;
    el("samples").setAttribute("aria-pressed", String(name === "samples"));
    el("candles").setAttribute("aria-pressed", String(name === "candles"));
    if (current) chartData(current, true);
  });
}
el("refresh").addEventListener("click", refresh);
refresh();
setInterval(refresh, 20_000);
