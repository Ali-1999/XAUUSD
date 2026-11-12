from collections import deque
from dataclasses import dataclass
from itertools import groupby

M15 = 900
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


def four_hour_bars(bars: list[dict]) -> list[dict]:
    result = []
    for time, group in groupby(bars, key=lambda bar: bar["time"] // H4 * H4):
        block = list(group)
        if len(block) != 16 or any(bar["time"] != time + index * M15 for index, bar in enumerate(block)):
            continue
        result.append({
            "time": time, "open": block[0]["open"], "close": block[-1]["close"],
            "high": max(bar["high"] for bar in block), "low": min(bar["low"] for bar in block),
        })
    return result


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


def analyse(bars: list[dict]) -> dict:
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
            setup, signal = advance(setup, bar)
        if signal:
            last_signal = signal
        elif not setup and touched and len(recent) == STRUCTURE_BARS:
            setup = rejection(touched, recent, bar)
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
