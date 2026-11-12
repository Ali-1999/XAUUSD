import { analyse, LEVEL_AGE, M15 } from "./strategy.mjs";

export const QUOTE_MAX_AGE = 120;
export const SAMPLE_GAP = 180;
const MIN_SAMPLES = 9;
const MAX_SIGNAL_AGE = 120;
const LOOKBACK = 8 * 86_400;
const POINT = 0.01;
const LOT_STEP = 0.01;
const MAX_BARS = 780;
const MAX_POINTS = 180;

export const MODEL = Object.freeze({
  startingEquity: 10_000,
  riskPercent: 0.25,
  dailyCutoffPercent: 1.0,
  maxEntries: 3,
  spread: 0.35,
  slippage: 0.20,
  contract: 100,
  leverage: 20,
  rewardRisk: 2,
  maxStop: 20,
  maxEntryGap: 1.75,
});

export function initialState() {
  return {
    schema: 1, quote: null, attemptedAt: null, backgroundAt: null, error: null,
    candle: null, bars: [], points: [], missedBars: 0, analysis: analyse([]),
    cash: MODEL.startingEquity, position: null, day: null, days: [],
    events: [], closed: 0, wins: 0,
  };
}

export function quoteFromProvider(data, now) {
  const time = Math.floor(Date.parse(data?.updatedAt) / 1000);
  if (data?.symbol !== "XAU" || data?.currency !== "USD" || typeof data.price !== "number"
      || !Number.isFinite(data.price) || data.price <= 0 || !Number.isFinite(time) || time > now + 10) {
    throw new Error("The provider returned an invalid XAU/USD quote.");
  }
  if (now - time > QUOTE_MAX_AGE) throw new Error("The provider timestamp is stale.");
  return { time, price: data.price, observedAt: now };
}

function sampledBar(state, quote) {
  const time = Math.floor(quote.time / M15) * M15;
  const price = quote.price;
  const current = state.candle;
  if (!current || current.time !== time) {
    state.candle = { time, open: price, high: price, low: price, close: price,
      first: quote.time, last: quote.time, count: 1, gap: 0 };
    if (!current) return null;
    state.missedBars += Math.max(0, (time - current.time) / M15 - 1);
    if (current.count < MIN_SAMPLES || current.first - current.time > SAMPLE_GAP
        || current.time + M15 - current.last > SAMPLE_GAP || current.gap > SAMPLE_GAP) {
      state.missedBars += 1;
      return null;
    }
    const { open, high, low, close } = current;
    return { time: current.time, open, high, low, close };
  }
  current.high = Math.max(current.high, price);
  current.low = Math.min(current.low, price);
  current.close = price;
  current.gap = Math.max(current.gap, quote.time - current.last);
  current.last = quote.time;
  current.count += 1;
  return null;
}

const dayOf = (time) => new Date(time * 1000).toISOString().slice(0, 10);
const roundGrid = (value, step, up) => {
  const units = value / step;
  return Number(((up ? Math.ceil(units - 1e-9) : Math.floor(units + 1e-9)) * step).toFixed(2));
};
const direction = (side) => side === "buy" ? 1 : -1;
const closeQuote = (price, side) => price - direction(side) * MODEL.spread / 2;
const positionPnl = (position, exit) =>
  direction(position.side) * (exit - position.entry) * position.lots * MODEL.contract;

export function equityAt(state, price) {
  return state.cash + (state.position && Number.isFinite(price)
    ? positionPnl(state.position, closeQuote(price, state.position.side)) : 0);
}

export function riskBudget(day, equity) {
  if (!day || day.halted || day.entries >= MODEL.maxEntries || day.base <= 0 || equity <= 0) return 0;
  const floor = day.base * (1 - MODEL.dailyCutoffPercent / 100);
  return Math.max(0, Math.min(day.base * MODEL.riskPercent / 100, equity - floor));
}

function rollDay(state, quote, now) {
  const label = dayOf(now);
  if (state.day?.date === label) return;
  if (state.day) state.days = [...state.days.slice(-12), state.day];
  const equity = equityAt(state, quote.price);
  state.day = { date: label, base: equity, equity, entries: 0, halted: false };
}

function updateDay(state, price) {
  const day = state.day;
  day.equity = equityAt(state, price);
  day.halted ||= day.equity <= day.base * (1 - MODEL.dailyCutoffPercent / 100);
}

function log(state, event) {
  state.events = [event, ...state.events.slice(0, 49)];
}

function closePosition(state, quote) {
  const pos = state.position;
  if (!pos) return;
  const dir = direction(pos.side);
  const executable = closeQuote(quote.price, pos.side);
  let exit;
  let type;
  if (dir * (executable - pos.stop) <= 0) {
    exit = executable - dir * MODEL.slippage;
    type = "stop";
  } else if (dir * (executable - pos.target) >= 0) {
    exit = pos.target;
    type = "target";
  } else {
    return;
  }
  const pnl = positionPnl(pos, exit);
  state.cash += pnl;
  state.closed += 1;
  if (pnl > 0) state.wins += 1;
  state.position = null;
  log(state, { time: quote.time, type, side: pos.side, lots: pos.lots, price: exit, pnl });
}

export function planTrade(signal, quote, equity, budget) {
  if (budget <= 0) return { reason: "Daily risk or entry limit reached" };
  if (Math.abs(quote.price - signal.reference) > MODEL.maxEntryGap) {
    return { reason: "Spot price moved beyond the entry tolerance" };
  }
  const dir = direction(signal.side);
  const entry = roundGrid(quote.price + dir * (MODEL.spread / 2 + MODEL.slippage), POINT, dir > 0);
  const stop = roundGrid(signal.stop, POINT, dir < 0);
  const exitPrice = closeQuote(quote.price, signal.side);
  const stopDistance = dir * (entry - stop);
  if (dir * (exitPrice - stop) < POINT - 1e-9 || stopDistance > MODEL.maxStop) {
    return { reason: "Structure stop is outside the modeled range" };
  }
  const target = roundGrid(entry + dir * MODEL.rewardRisk * stopDistance, POINT, dir > 0);
  const perLot = (stopDistance + MODEL.slippage) * MODEL.contract;
  const marginPerLot = entry * MODEL.contract / MODEL.leverage;
  const lots = roundGrid(Math.min(budget / perLot, 0.5 * equity / marginPerLot), LOT_STEP, false);
  if (lots < LOT_STEP || lots * perLot > budget + 1e-7) {
    return { reason: "Minimum lot exceeds the modeled risk or margin budget" };
  }
  return { side: signal.side, entry, stop, target, lots, riskCash: perLot * lots, time: quote.time };
}

export function acceptQuote(state, quote, now) {
  if (state.quote && quote.time <= state.quote.time) return false;
  const previous = state.quote;
  state.quote = { ...quote, change: previous ? quote.price - previous.price : null };
  state.points = [...state.points.slice(-(MAX_POINTS - 1)), { time: quote.time, value: quote.price }];
  rollDay(state, quote, now);
  closePosition(state, quote);
  updateDay(state, quote.price);
  const bar = sampledBar(state, quote);
  if (!bar) return true;
  state.bars = [...state.bars, bar].filter((item) => item.time >= quote.time - LOOKBACK).slice(-MAX_BARS);
  state.analysis = analyse(state.bars);
  const signal = state.analysis.lastSignal;
  if (!signal || signal.time !== bar.time + M15) return true;
  const age = quote.time - signal.time;
  const plan = age > MAX_SIGNAL_AGE ? { reason: "Retest signal expired before the next quote" }
    : state.position ? { reason: "A paper position is already open" }
    : planTrade(signal, quote, state.day.equity, riskBudget(state.day, state.day.equity));
  if (plan.reason) {
    log(state, { time: quote.time, type: "skip", side: signal.side, detail: plan.reason });
    return true;
  }
  state.position = plan;
  state.day.entries += 1;
  updateDay(state, quote.price);
  log(state, { time: quote.time, type: "entry", ...plan });
  return true;
}

export function snapshot(state, now) {
  const quoteAge = state.quote ? Math.max(0, now - state.quote.time) : null;
  const equity = state.day?.equity ?? MODEL.startingEquity;
  const last = state.bars.at(-1);
  const hasGap = last && now - (last.time + M15) > M15 + SAMPLE_GAP;
  const analysis = {
    ...state.analysis,
    stage: hasGap && state.analysis.stage !== "warmup" ? "gap" : state.analysis.stage,
    levels: state.analysis.levels.filter((level) => now - level.knownAt < LEVEL_AGE),
  };
  return {
    schema: 1, symbol: "XAU/USD", source: "Gold API", mode: "paper",
    status: state.error ? "degraded" : quoteAge === null ? "starting" : quoteAge <= QUOTE_MAX_AGE ? "live" : "stale",
    asOf: now, quoteAge, quote: state.quote, attemptedAt: state.attemptedAt,
    backgroundAt: state.backgroundAt ?? state.cronAt ?? null, message: state.error, missedBars: state.missedBars,
    bars: state.bars.slice(-192), points: state.points, sampleCount: state.candle?.count ?? 0,
    latestCandle: last?.time ?? null, barCount: state.bars.length, analysis,
    account: {
      equity, balance: state.cash, base: state.day?.base ?? null, date: state.day?.date ?? null,
      riskBudget: riskBudget(state.day, equity), entries: state.day?.entries ?? 0, halted: state.day?.halted ?? false,
      dailyChange: state.day ? equity - state.day.base : null, closed: state.closed, wins: state.wins,
    },
    model: MODEL, position: state.position, events: state.events.slice(0, 25),
  };
}
