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
const chart = marketChart.create(el("chart"));
const line = chart.addSeries(charts.LineSeries, { color: "#efc579", lineWidth: 2, priceLineVisible: false });
const candles = marketChart.candles(chart, false);
const candleOverlay = marketChart.overlay(candles);
const spotOverlay = marketChart.overlay(line);
let busy = false;
let selected = el("timeframe").value;
let renderedFrame = null;
let renderedQuoteAt = 0;
let frameRequest = 0;
let lastChart = null;

function chartLevels(analysis, position) {
  const { support, resistance, structure } = marketChart.colors;
  const levels = analysis.levels.map((level) => ({
    price: level.price, color: level.side === "buy" ? support : resistance,
    title: level.side === "buy" ? "H4 V" : "H4 A",
  }));
  for (const level of analysis.contextLevels ?? []) {
    levels.push({ price: level.price, title: "H1 OCL", color: structure });
  }
  if (["break", "retest"].includes(analysis.stage) && number(analysis.structure)) {
    levels.push({ price: analysis.structure, title: "M15 structure", color: structure });
  }
  if (position) levels.push(...marketChart.tradeLines(position, "Paper entry"));
  return levels;
}

function chartData(data) {
  const series = data.chart;
  const quoteAt = data.quote?.time ?? 0;
  if (series?.timeframe !== selected || (renderedFrame === selected && quoteAt < renderedQuoteAt)) return;
  lastChart = data;
  const useCandles = selected !== "spot";
  const visible = useCandles ? series.candles : series.points;
  line.applyOptions({ visible: !useCandles });
  candles.applyOptions({ visible: useCandles });
  line.setData(useCandles ? [] : visible);
  candles.setData(useCandles ? visible : []);
  chart.timeScale().applyOptions({ timeVisible: !["D1", "W1", "MN1"].includes(selected) });
  const showAnalysis = el("show-analysis").checked && visible.length;
  const events = showAnalysis ? [...(data.analysis.markers ?? []), ...data.events] : [];
  const levels = showAnalysis ? chartLevels(data.analysis, data.position) : [];
  candleOverlay(useCandles ? visible : [], useCandles ? events : [], useCandles ? levels : [], quoteAt);
  spotOverlay(useCandles ? [] : visible, useCandles ? [] : events, useCandles ? [] : levels, quoteAt);
  el("empty").hidden = visible.length > 0;
  set("empty", "Waiting for a spot quote");
  set("chart-caption", useCandles ? selected + " candles from Gold API spot samples · UTC" : "Gold API spot samples · UTC");
  const noun = useCandles ? "sampled candle" : "spot sample";
  const count = visible.length + " " + noun + (visible.length === 1 ? "" : "s");
  set("sampling", series.inProgress ? count + " · Latest candle in progress" : count);
  if (renderedFrame !== selected && visible.length) chart.timeScale().fitContent();
  renderedFrame = visible.length ? selected : null;
  renderedQuoteAt = quoteAt;
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
  const hourlyLevel = setup.contextLevels?.[0];
  set("hourly-level", hourlyLevel ? price(hourlyLevel.price) : "—");
  set("structure", ["break", "retest"].includes(setup.stage) ? price(setup.structure) : "—");
  const s = setup.lastSignal;
  set("signal", s ? side(s.side) + " · " + utc(s.time) : "—");
  const pos = data.position;
  set("position", pos ? side(pos.side) + " · " + pos.lots + " lots" : "Flat");
  el("position").title = pos ? "SL " + price(pos.stop) + " · TP " + price(pos.target) : "";
  chartData(data);
  renderJournal(data.events);
}

async function loadState(path, method, timeframe) {
  const query = new URLSearchParams({ timeframe });
  const response = await fetch(path + "?" + query, { method, cache: "no-store", signal: AbortSignal.timeout(12_000) });
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.json();
}

async function refresh() {
  if (busy) return;
  busy = true;
  el("refresh").disabled = true;
  try {
    render(await loadState("/api/refresh", "POST", selected));
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

el("timeframe").addEventListener("change", async () => {
  selected = el("timeframe").value;
  const request = ++frameRequest;
  renderedFrame = null;
  line.setData([]);
  candles.setData([]);
  candleOverlay([], [], [], 0);
  spotOverlay([], [], [], 0);
  set("chart-caption", "Gold API spot samples · UTC");
  set("sampling", "");
  set("empty", "Loading " + (selected === "spot" ? "spot samples" : selected + " candles"));
  el("empty").hidden = false;
  try {
    const data = await loadState("/api/state", "GET", selected);
    if (request === frameRequest) chartData(data);
  } catch (error) {
    if (request === frameRequest && renderedFrame !== selected) set("empty", "Chart unavailable (" + error.message + ")");
  }
});
el("show-analysis").addEventListener("change", () => { if (lastChart) chartData(lastChart); });
el("refresh").addEventListener("click", refresh);
refresh();
setInterval(refresh, 20_000);
