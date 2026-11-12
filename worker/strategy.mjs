export const M15 = 900;
export const H4 = 14_400;
export const LEVEL_AGE = 7 * 86_400;
const ZONE = 0.35;
const BREAK_BUFFER = 0.10;
const STOP_BUFFER = 0.25;
const STRUCTURE_BARS = 4;
const BREAK_WINDOW = 8 * M15;
const RETEST_WINDOW = 6 * M15;

export function fourHourBars(bars) {
  const result = [];
  let block = null;
  for (const bar of bars) {
    const time = Math.floor(bar.time / H4) * H4;
    if (block?.time !== time) {
      block = { ...bar, time, count: 0, contiguous: true };
    }
    block.contiguous &&= bar.time === time + block.count * M15;
    block.high = Math.max(block.high, bar.high);
    block.low = Math.min(block.low, bar.low);
    block.close = bar.close;
    block.count += 1;
    if (block.contiguous && block.count === 16) {
      const { open, high, low, close } = block;
      result.push({ time, open, high, low, close });
    }
  }
  return result;
}

function bodyLevels(h4, start) {
  const levels = [];
  for (let i = 1; i < h4.length; i += 1) {
    const first = h4[i - 1];
    const second = h4[i];
    const knownAt = second.time + H4;
    if (knownAt < start || second.time - first.time !== H4 || Math.abs(first.close - second.open) > ZONE) {
      continue;
    }
    const price = (first.close + second.open) / 2;
    const firstUp = first.close > first.open;
    const firstDown = first.close < first.open;
    if (firstUp && second.close < second.open) levels.push({ side: "sell", price, knownAt });
    if (firstDown && second.close > second.open) levels.push({ side: "buy", price, knownAt });
  }
  return levels;
}

function reached(level, bar) {
  return level.side === "buy" ? bar.low <= level.price + ZONE : bar.high >= level.price - ZONE;
}

function rejection(levels, recent, bar) {
  const previous = recent.at(-1);
  const distance = (level) => Math.abs(previous.close - level.price);
  levels.sort((a, b) => distance(a) - distance(b) || b.knownAt - a.knownAt);
  for (const level of levels) {
    const buy = level.side === "buy";
    const lower = level.price - ZONE;
    const upper = level.price + ZONE;
    if (bar.high < lower || bar.low > upper) continue;
    if (buy && Math.min(previous.close, bar.close) > upper && bar.close > bar.open) {
      return { level, origin: bar, structure: Math.max(bar.high, ...recent.map((x) => x.high)) };
    }
    if (!buy && Math.max(previous.close, bar.close) < lower && bar.close < bar.open) {
      return { level, origin: bar, structure: Math.min(bar.low, ...recent.map((x) => x.low)) };
    }
  }
  return null;
}

function advance(setup, bar) {
  const buy = setup.level.side === "buy";
  const direction = buy ? 1 : -1;
  const distance = direction * (bar.close - setup.structure);
  const breach = buy ? bar.low < setup.origin.low : bar.high > setup.origin.high;
  if (breach) return { setup: null };
  if (!setup.breakout) {
    if (bar.time - setup.origin.time > BREAK_WINDOW) return { setup: null };
    return { setup: distance > BREAK_BUFFER ? { ...setup, breakout: bar } : setup };
  }
  if (bar.time - setup.breakout.time > RETEST_WINDOW) return { setup: null };
  if (bar.low > setup.structure + ZONE || bar.high < setup.structure - ZONE) {
    return { setup: distance < -ZONE ? null : setup };
  }
  if (distance <= 0 || direction * (bar.close - bar.open) <= 0) return { setup: null };
  const anchor = buy ? Math.min(setup.origin.low, bar.low) : Math.max(setup.origin.high, bar.high);
  return {
    setup: null,
    signal: {
      time: bar.time + M15, side: setup.level.side, level: setup.level.price,
      structure: setup.structure, reference: bar.close, stop: anchor - direction * STOP_BUFFER,
    },
  };
}

export function analyse(bars) {
  const h4 = fourHourBars(bars);
  const first = bars[0]?.time ?? 0;
  const levels = bodyLevels(h4, first);
  let next = 0;
  let fresh = [];
  let setup = null;
  let lastSignal = null;
  const recent = [];
  for (const bar of bars) {
    const previous = recent.at(-1);
    if (previous && bar.time !== previous.time + M15) {
      recent.length = 0;
      setup = null;
    }
    while (next < levels.length && levels[next].knownAt <= bar.time) {
      fresh.push(levels[next]);
      next += 1;
    }
    const touched = [];
    fresh = fresh.filter((level) => {
      if (bar.time - level.knownAt >= LEVEL_AGE) return false;
      if (!reached(level, bar)) return true;
      touched.push(level);
      return false;
    });
    const advanced = setup ? advance(setup, bar) : { setup };
    setup = advanced.setup;
    if (advanced.signal) {
      lastSignal = advanced.signal;
    } else if (!setup && recent.length === STRUCTURE_BARS) {
      setup = rejection(touched, recent, bar);
    }
    recent.push(bar);
    if (recent.length > STRUCTURE_BARS) recent.shift();
  }
  const last = bars.at(-1);
  // A level confirmed exactly at the latest close can be watched on the next bar.
  while (last && next < levels.length && levels[next].knownAt <= last.time + M15) {
    fresh.push(levels[next]);
    next += 1;
  }
  const price = last?.close ?? 0;
  const nearest = fresh.sort((a, b) => Math.abs(a.price - price) - Math.abs(b.price - price)).slice(0, 6);
  return {
    h4Count: h4.length,
    stage: h4.length < 2 ? "warmup" : setup?.breakout ? "retest" : setup ? "break" : "watch",
    activeSide: setup?.level.side ?? null,
    origin: setup?.level.price ?? null,
    structure: setup?.structure ?? null,
    levels: setup ? [setup.level, ...nearest.slice(0, 5)] : nearest,
    lastSignal,
  };
}
