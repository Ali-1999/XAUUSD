import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { acceptQuote, initialState, planTrade, quoteFromProvider, snapshot } from "../worker/engine.mjs";
import { analyse, M15 } from "../worker/strategy.mjs";

const dataset = readFileSync(new URL("./fixtures/sample_m15.csv", import.meta.url), "utf8");
const bars = dataset.trim().split(/\r?\n/).slice(1).map((line) => {
  const [time, open, high, low, close] = line.split(",").map(Number);
  return { time, open, high, low, close };
});
const start = bars[0].time;
const quote = (time, price) => ({ time, price, observedAt: time });

test("H4 A/V and M15 entry require a later completed retest", () => {
  assert.equal(analyse(bars.slice(0, 31)).stage, "warmup");
  assert.equal(analyse(bars.slice(0, 33)).stage, "break");
  assert.equal(analyse(bars.slice(0, 34)).stage, "retest");
  const signal = analyse(bars.slice(0, 35)).lastSignal;
  assert.equal(signal.side, "buy");
  assert.equal(signal.time, bars[34].time + M15);
  assert.equal(analyse(bars.slice(0, 131)).lastSignal.side, "sell");
});

test("a gap in the M15 series cancels the retest", () => {
  const gap = { ...bars[34], time: bars[34].time + M15 };
  assert.equal(analyse([...bars.slice(0, 34), gap]).lastSignal, null);
});

test("only M15 candles with sufficient observations are promoted", () => {
  const state = initialState();
  for (let i = 0; i < 15; i += 1) acceptQuote(state, quote(start + i * 60, 3900 + i / 10), start + i * 60);
  acceptQuote(state, quote(start + M15, 3902), start + M15);
  assert.deepEqual(state.bars, [{ time: start, open: 3900, high: 3901.4, low: 3900, close: 3901.4 }]);
  acceptQuote(state, quote(start + 2 * M15, 3904), start + 2 * M15);
  assert.equal(state.bars.length, 1);
  assert.equal(state.missedBars, 1);
});

test("the paper engine fills a fresh signal once and rolls losses into the next UTC base", () => {
  const state = initialState();
  state.bars = bars.slice(0, 34);
  const retest = bars[34];
  state.candle = { ...retest, first: retest.time, last: retest.time + M15 - 60, count: 15, gap: 60 };
  const time = retest.time + M15;
  acceptQuote(state, quote(time, retest.close), time);
  assert.equal(state.day.entries, 1);
  assert.equal(state.position.side, "buy");
  assert.ok(state.position.riskCash <= 25);
  assert.equal(acceptQuote(state, quote(time, retest.close), time), false);
  const exit = state.position.stop - 0.4;
  acceptQuote(state, quote(time + 60, exit), time + 60);
  assert.equal(state.position, null);
  assert.ok(state.cash < 10_000);
  assert.equal(state.day.base, 10_000);
  acceptQuote(state, quote(start + 86_400, exit), start + 86_400);
  assert.equal(state.day.base, state.cash);
  assert.equal(state.day.entries, 0);
});

test("cash risk and entry-price gates block an ineligible order", () => {
  const signal = { side: "buy", stop: 3896, reference: 3900 };
  assert.match(planTrade(signal, quote(start, 3900), 10000, 0).reason, /Daily risk/);
  assert.match(planTrade(signal, quote(start, 3910), 10000, 25).reason, /entry tolerance/);
  assert.match(planTrade(signal, quote(start, 3900), 10000, 1).reason, /Minimum lot/);
});

test("provider timestamps and currency control quote freshness", () => {
  const payload = { symbol: "XAU", currency: "USD", price: 3900, updatedAt: new Date(start * 1000).toISOString() };
  assert.equal(quoteFromProvider(payload, start + 30).price, 3900);
  assert.throws(() => quoteFromProvider(payload, start + 121), /stale/);
  assert.throws(() => quoteFromProvider({ ...payload, currency: "EUR" }, start), /invalid/);
  const state = initialState();
  acceptQuote(state, quote(start, 3900), start);
  assert.equal(snapshot(state, start + 121).status, "stale");
});
