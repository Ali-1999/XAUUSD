from collections import deque
from dataclasses import dataclass
from itertools import groupby

M15 = 900
H1 = 3_600
H4 = 14_400
LEVEL_AGE = 7 * 86_400
ZONE = 0.35
BREAK_BUFFER = 0.10
STOP_BUFFER = 0.25
STRUCTURE_BARS = 4
BREAK_WINDOW = 8 * M15
RETEST_WINDOW = 6 * M15


@dataclass(slots=True)
class Setup:
    level: dict
    origin: dict
    structure: float
    breakout: int | None = None


def complete_bars(bars: list[dict], period: int, source: int = M15) -> list[dict]:
    result = []
    for time, group in groupby(bars, key=lambda bar: bar["time"] // period * period):
        block = list(group)
        if len(block) != period // source or any(bar["time"] != time + index * source for index, bar in enumerate(block)):
            continue
        result.append({
            "time": time, "open": block[0]["open"], "close": block[-1]["close"],
            "high": max(bar["high"] for bar in block), "low": min(bar["low"] for bar in block),
        })
    return result


def four_hour_bars(bars: list[dict]) -> list[dict]:
    return complete_bars(bars, H4)


def hourly_levels(bars: list[dict]):
    """H1 open-close levels confirmed by three contiguous candles."""
    hours = complete_bars(bars, H1)
    for first, middle, last in zip(hours, hours[1:], hours[2:]):
        if last["time"] - first["time"] != 2 * H1:
            continue
        moves = [bar["close"] - bar["open"] for bar in (first, middle, last)]
        if min(moves) > 0 or max(moves) < 0:
            yield {"side": "buy" if moves[0] > 0 else "sell", "price": middle["close"], "knownAt": last["time"] + H1}


def hourly_context(bars: list[dict]) -> list[dict]:
    if not bars:
        return []
    latest = bars[-1]["time"] + M15
    levels = (level for level in hourly_levels(bars) if latest - level["knownAt"] < LEVEL_AGE)
    return sorted(levels, key=lambda level: abs(level["price"] - bars[-1]["close"]))[:2]


def body_levels(h4: list[dict], start: int):
    for first, second in zip(h4, h4[1:]):
        known_at = second["time"] + H4
        if known_at < start or second["time"] - first["time"] != H4:
            continue
        if abs(first["close"] - second["open"]) > ZONE:
            continue
        price = (first["close"] + second["open"]) / 2
        if first["close"] < first["open"] and second["close"] > second["open"]:
            yield {"side": "buy", "price": price, "knownAt": known_at}
        elif first["close"] > first["open"] and second["close"] < second["open"]:
            yield {"side": "sell", "price": price, "knownAt": known_at}


def reached(level: dict, bar: dict) -> bool:
    if level["side"] == "buy":
        return bar["low"] <= level["price"] + ZONE
    return bar["high"] >= level["price"] - ZONE


def rejection(levels: list[dict], recent: deque, bar: dict) -> Setup | None:
    previous = recent[-1]["close"]
    for level in sorted(levels, key=lambda item: (abs(previous - item["price"]), -item["knownAt"])):
        upper, lower = level["price"] + ZONE, level["price"] - ZONE
        if bar["high"] < lower or bar["low"] > upper:
            continue
        if level["side"] == "buy" and min(previous, bar["close"]) > upper and bar["close"] > bar["open"]:
            return Setup(level, bar, max(bar["high"], *(item["high"] for item in recent)))
        if level["side"] == "sell" and max(previous, bar["close"]) < lower and bar["close"] < bar["open"]:
            return Setup(level, bar, min(bar["low"], *(item["low"] for item in recent)))
    return None


def advance(setup: Setup, bar: dict) -> tuple[Setup | None, dict | None]:
    buy = setup.level["side"] == "buy"
    direction = 1 if buy else -1
    distance = direction * (bar["close"] - setup.structure)
    breach = bar["low"] < setup.origin["low"] if buy else bar["high"] > setup.origin["high"]
    if breach:
        return None, None
    if setup.breakout is None:
        if bar["time"] - setup.origin["time"] > BREAK_WINDOW:
            return None, None
        if distance > BREAK_BUFFER:
            setup.breakout = bar["time"]
        return setup, None
    if bar["time"] - setup.breakout > RETEST_WINDOW:
        return None, None
    if bar["low"] > setup.structure + ZONE or bar["high"] < setup.structure - ZONE:
        return (None if distance < -ZONE else setup), None
    if distance <= 0 or direction * (bar["close"] - bar["open"]) <= 0:
        return None, None
    anchor = min(setup.origin["low"], bar["low"]) if buy else max(setup.origin["high"], bar["high"])
    return None, {
        "time": bar["time"] + M15, "side": setup.level["side"], "level": setup.level["price"],
        "structure": setup.structure, "reference": bar["close"], "stop": anchor - direction * STOP_BUFFER,
    }


def analyse(bars: list[dict], *, trace: list[dict] | None = None) -> dict:
    h4 = four_hour_bars(bars)
    pending = deque(body_levels(h4, bars[0]["time"] if bars else 0))
    recent = deque(maxlen=STRUCTURE_BARS)
    fresh = []
    setup = None
    last_signal = None
    for bar in bars:
        time = bar["time"]
        if recent and time != recent[-1]["time"] + M15:
            recent.clear()
            setup = None
        while pending and pending[0]["knownAt"] <= time:
            fresh.append(pending.popleft())
        active, touched = [], []
        for level in fresh:
            if time - level["knownAt"] < LEVEL_AGE:
                (touched if reached(level, bar) else active).append(level)
        fresh = active
        signal = None
        if setup:
            prior_break = setup.breakout
            setup, signal = advance(setup, bar)
            if trace is not None:
                if signal:
                    trace.append({"type": "retest", "barTime": time, **signal})
                elif setup and prior_break is None and setup.breakout is not None:
                    trace.append({"type": "break", "time": time + M15, "barTime": time,
                                  "side": setup.level["side"], "structure": setup.structure})
        if signal:
            last_signal = signal
        elif not setup and touched and len(recent) == STRUCTURE_BARS:
            setup = rejection(touched, recent, bar)
            if setup and trace is not None:
                trace.append({"type": "rejection", "time": time + M15, "barTime": time,
                              "side": setup.level["side"], "level": setup.level["price"]})
        recent.append(bar)

    last = bars[-1] if bars else None
    while last and pending and pending[0]["knownAt"] <= last["time"] + M15:
        fresh.append(pending.popleft())
    price = last["close"] if last else 0
    nearest = sorted(fresh, key=lambda item: abs(item["price"] - price))[:6]
    stage = "warmup" if len(h4) < 2 else "watch"
    if stage != "warmup" and setup:
        stage = "retest" if setup.breakout is not None else "break"
    return {
        "h4Count": len(h4), "stage": stage,
        "activeSide": setup.level["side"] if setup else None,
        "origin": setup.level["price"] if setup else None,
        "structure": setup.structure if setup else None,
        "levels": [setup.level, *nearest[:5]] if setup else nearest,
        "lastSignal": last_signal,
    }
