"use strict";

const el = (id) => document.getElementById(id);
const set = (id, value) => { el(id).textContent = value; };
const date = (time) => new Date(time * 1000).toISOString().slice(0, 10);
const utc = (time) => new Date(time * 1000).toISOString().slice(0, 16).replace("T", " ");
const percent = (value) => Number.isFinite(value) ? value.toFixed(2) + "%" : "—";
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const periods = { M15: 900, H1: 3600, H4: 14400, D1: 86400 };

const chart = marketChart.create(el("chart"));
const series = marketChart.candles(chart);
const overlay = marketChart.overlay(series);
const cursor = el("replay-position");
let report;
let run;
let bars = [];
let events = [];
let selectedTrade = null;

function paintChart(focus) {
  if (!bars.length) return;
  const position = Number(cursor.value);
  const visible = bars.slice(0, position + 1);
  const last = visible.at(-1);
  const through = Math.min(last.time + periods[el("timeframe").value], run.end);
  series.setData(visible);
  const enabled = el("show-analysis").checked;
  const levels = enabled ? (run.contextLevels ?? [])
    .filter((level) => level.knownAt <= through && through - level.knownAt < report.model.levelAgeSeconds)
    .sort((a, b) => Math.abs(a.price - last.close) - Math.abs(b.price - last.close)).slice(0, 2)
    .map((level) => ({ price: level.price, title: "H1 OCL", color: marketChart.colors.structure })) : [];
  if (enabled && selectedTrade && selectedTrade.entryTime <= through) {
    levels.push(...marketChart.tradeLines(selectedTrade, "Replay entry"));
  }
  overlay(visible, enabled ? events : [], levels, through);
  set("replay-date", utc(through) + " UTC");
  cursor.setAttribute("aria-valuetext", utc(through) + " UTC");
  if (focus) chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, position - 115), to: position + 5 });
}

function showTrade(trade, scroll = true) {
  if (!bars.length) return;
  selectedTrade = trade;
  const entry = Math.max(0, marketChart.barIndex(bars, trade.entryTime));
  const exit = Math.max(entry, marketChart.barIndex(bars, trade.exitTime));
  cursor.value = Math.min(bars.length - 1, exit + 10);
  paintChart(false);
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, entry - 12), to: exit + 12 });
  if (scroll) el("chart").scrollIntoView({ block: "center" });
}

function renderTrades() {
  const body = el("trades");
  body.replaceChildren();
  if (!run.trades.length) {
    const cell = body.insertRow().insertCell();
    cell.colSpan = 5;
    cell.textContent = "No trades met the entry rules in this window.";
    return;
  }
  for (const trade of [...run.trades].reverse()) {
    const row = body.insertRow();
    const button = document.createElement("button");
    button.type = "button";
    button.className = "trade-link";
    button.textContent = utc(trade.entryTime);
    button.title = "Show trade on chart";
    button.addEventListener("click", () => showTrade(trade));
    row.insertCell().append(button);
    const side = trade.side === "buy" ? "Buy" : "Sell";
    const fills = dollars.format(trade.entry) + " / " + dollars.format(trade.exit);
    for (const text of [utc(trade.exitTime), `${side} · ${trade.ounces} oz`, fills]) row.insertCell().textContent = text;
    const pnl = row.insertCell();
    pnl.textContent = dollars.format(trade.pnl);
    pnl.className = trade.pnl > 0 ? "up" : trade.pnl < 0 ? "down" : "";
  }
}

function renderTimeframe() {
  if (!run) return;
  const timeframe = el("timeframe").value;
  bars = report.candles[timeframe].filter(([stamp]) => stamp >= run.start && stamp < run.end)
    .map(([time, open, high, low, close]) => ({ time, open, high, low, close }));
  set("replay-caption", `${timeframe} · ${bars.length.toLocaleString()} historical candles · UTC`);
  cursor.max = Math.max(0, bars.length - 1);
  cursor.value = cursor.max;
  cursor.disabled = bars.length < 2;
  chart.timeScale().applyOptions({ timeVisible: timeframe !== "D1" });
  if (bars.length && selectedTrade) showTrade(selectedTrade, false);
  else if (bars.length) paintChart(true);
  else {
    series.setData([]);
    overlay([], [], [], 0);
    set("replay-date", "No candles for this window");
  }
}

function renderRun() {
  run = report.runs[Number(el("run").value)];
  selectedTrade = run.trades.at(-1) ?? null;
  const metrics = run.metrics;
  set("net-return", percent(metrics.returnPercent));
  el("net-return").className = metrics.returnPercent > 0 ? "up" : metrics.returnPercent < 0 ? "down" : "";
  set("drawdown", percent(metrics.drawdownPercent));
  set("trade-count", metrics.trades);
  set("win-rate", percent(metrics.winPercent));
  set("final-equity", "Ending equity " + dollars.format(metrics.finalEquity));
  set("signal-count", `${run.signals} confirmed retests`);
  set("replay-coverage", `${date(run.firstMinute)} to ${date(run.lastMinute)} UTC · ${run.observations.toLocaleString()} observed M1 candles`);
  events = [...run.markers, ...run.trades.flatMap((trade) => [
    { type: "entry", time: trade.entryTime, side: trade.side },
    { type: "exit", time: trade.exitTime, side: trade.side },
  ])];
  renderTimeframe();
  renderTrades();
}

function install(data) {
  if (data?.schema !== 1 || data.symbol !== "XAU/USD" || !data.model || !data.runs?.length
      || !Object.keys(periods).every((frame) => Array.isArray(data.candles?.[frame]))
      || !data.runs.every((item) => item.metrics && Array.isArray(item.markers) && Array.isArray(item.trades))) {
    throw new Error("Choose a replay JSON generated by the XAU/USD backtest runner.");
  }
  report = data;
  set("replay-source", `${data.source} · ${data.engine}`);
  const model = data.model;
  set("replay-model", `USD ${model.feePerOuncePerSide} per ounce per side · ${model.riskPercent}% daily-base risk per entry · select an entry time to inspect the trade`);
  const select = el("run");
  select.replaceChildren();
  data.runs.forEach((item, index) => {
    const label = `${Math.round((item.end - item.start) / 86400)} days · ${date(item.start)} to ${date(item.end - 1)}`;
    select.add(new Option(label, index));
  });
  select.disabled = false;
  renderRun();
}

function error(message) {
  set("replay-error", message);
  el("replay-error").hidden = false;
}

el("run").addEventListener("change", renderRun);
el("timeframe").addEventListener("change", renderTimeframe);
el("show-analysis").addEventListener("change", () => paintChart(false));
cursor.addEventListener("input", () => paintChart(true));
el("report-file").addEventListener("change", async () => {
  const file = el("report-file").files[0];
  if (!file) return;
  try {
    install(JSON.parse(await file.text()));
    el("replay-error").hidden = true;
  } catch (problem) {
    error(problem.message);
  } finally {
    el("report-file").value = "";
  }
});

fetch("/history/replay.json").then((response) => {
  if (!response.ok) throw new Error("The hosted replay is unavailable. Load a replay file to continue.");
  return response.json();
}).then((data) => { if (!report) install(data); }).catch((problem) => { if (!report) error(problem.message); });
