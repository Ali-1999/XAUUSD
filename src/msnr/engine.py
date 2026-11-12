from datetime import UTC, datetime
from math import ceil, floor, isfinite

from .strategy import LEVEL_AGE, M15, analyse

QUOTE_MAX_AGE = 120
SAMPLE_GAP = 180
MIN_SAMPLES = 9
MAX_SIGNAL_AGE = 120
LOOKBACK = 8 * 86_400
POINT = 0.01
LOT_STEP = 0.01
MAX_BARS = 780
MAX_POINTS = 180

MODEL = {
    "startingEquity": 10_000, "riskPercent": 0.25, "dailyCutoffPercent": 1.0,
    "maxEntries": 3, "spread": 0.35, "slippage": 0.20, "contract": 100,
    "leverage": 20, "rewardRisk": 2, "maxStop": 20, "maxEntryGap": 1.75,
}


class QuoteError(ValueError):
    pass


def initial_state() -> dict:
    return {
        "schema": 1, "quote": None, "attemptedAt": None, "backgroundAt": None, "error": None,
        "candle": None, "bars": [], "points": [], "missedBars": 0, "analysis": analyse([]),
        "cash": MODEL["startingEquity"], "position": None, "day": None, "days": [],
        "events": [], "closed": 0, "wins": 0,
    }


def quote_from_provider(data, now: int) -> dict:
    invalid = "The provider returned an invalid XAU/USD quote."
    if not isinstance(data, dict) or data.get("symbol") != "XAU" or data.get("currency") != "USD":
        raise QuoteError(invalid)
    price = data.get("price")
    if type(price) not in (int, float) or not isfinite(price) or price <= 0:
        raise QuoteError(invalid)
    try:
        stamp = datetime.fromisoformat(data["updatedAt"])
        if stamp.tzinfo is None:
            raise ValueError("Missing UTC offset")
        time = floor(stamp.timestamp())
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise QuoteError(invalid) from error
    if time > now + 10:
        raise QuoteError(invalid)
    if now - time > QUOTE_MAX_AGE:
        raise QuoteError("The provider timestamp is stale.")
    return {"time": time, "price": price, "observedAt": now}


def sampled_bar(state: dict, quote: dict) -> dict | None:
    time = quote["time"] // M15 * M15
    price = quote["price"]
    current = state["candle"]
    if current and current["time"] == time:
        current["high"] = max(current["high"], price)
        current["low"] = min(current["low"], price)
        current["close"] = price
        current["gap"] = max(current["gap"], quote["time"] - current["last"])
        current["last"] = quote["time"]
        current["count"] += 1
        return None
    state["candle"] = {
        "time": time, "open": price, "high": price, "low": price, "close": price,
        "first": quote["time"], "last": quote["time"], "count": 1, "gap": 0,
    }
    if not current:
        return None
    state["missedBars"] += max(0, (time - current["time"]) // M15 - 1)
    if (
        current["count"] < MIN_SAMPLES or current["first"] - current["time"] > SAMPLE_GAP
        or current["time"] + M15 - current["last"] > SAMPLE_GAP or current["gap"] > SAMPLE_GAP
    ):
        state["missedBars"] += 1
        return None
    return {key: current[key] for key in ("time", "open", "high", "low", "close")}


def grid(value: float, step: float, up: bool) -> float:
    units = value / step
    count = ceil(units - 1e-9) if up else floor(units + 1e-9)
    return round(count * step, 2)


def direction(side: str) -> int:
    return 1 if side == "buy" else -1


def close_quote(price: float, side: str) -> float:
    return price - direction(side) * MODEL["spread"] / 2


def position_pnl(position: dict, exit_price: float) -> float:
    return direction(position["side"]) * (exit_price - position["entry"]) * position["lots"] * MODEL["contract"]


def equity_at(state: dict, price: float) -> float:
    position = state["position"]
    return state["cash"] + (position_pnl(position, close_quote(price, position["side"])) if position else 0)


def risk_budget(day: dict | None, equity: float) -> float:
    if not day or day["halted"] or day["entries"] >= MODEL["maxEntries"] or day["base"] <= 0 or equity <= 0:
        return 0
    loss_floor = day["base"] * (1 - MODEL["dailyCutoffPercent"] / 100)
    return max(0, min(day["base"] * MODEL["riskPercent"] / 100, equity - loss_floor))


def roll_day(state: dict, quote: dict, now: int) -> None:
    day = state["day"]
    label = datetime.fromtimestamp(now, UTC).date().isoformat()
    if day and day["date"] == label:
        return
    if day:
        state["days"] = [*state["days"][-12:], day]
    equity = equity_at(state, quote["price"])
    state["day"] = {"date": label, "base": equity, "equity": equity, "entries": 0, "halted": False}


def update_day(state: dict, price: float) -> None:
    day = state["day"]
    day["equity"] = equity_at(state, price)
    day["halted"] |= day["equity"] <= day["base"] * (1 - MODEL["dailyCutoffPercent"] / 100)


def log(state: dict, event: dict) -> None:
    state["events"] = [event, *state["events"][:49]]


def close_position(state: dict, quote: dict) -> None:
    pos = state["position"]
    if not pos:
        return
    sign = direction(pos["side"])
    executable = close_quote(quote["price"], pos["side"])
    if sign * (executable - pos["stop"]) <= 0:
        exit_price, reason = executable - sign * MODEL["slippage"], "stop"
    elif sign * (executable - pos["target"]) >= 0:
        exit_price, reason = pos["target"], "target"
    else:
        return
    pnl = position_pnl(pos, exit_price)
    state["cash"] += pnl
    state["closed"] += 1
    state["wins"] += int(pnl > 0)
    state["position"] = None
    log(state, {"time": quote["time"], "type": reason, "side": pos["side"],
                "lots": pos["lots"], "price": exit_price, "pnl": pnl})


def plan_trade(signal: dict, quote: dict, equity: float, budget: float) -> dict:
    if budget <= 0:
        return {"reason": "Daily risk or entry limit reached"}
    if abs(quote["price"] - signal["reference"]) > MODEL["maxEntryGap"]:
        return {"reason": "Spot price moved beyond the entry tolerance"}
    sign = direction(signal["side"])
    entry = grid(quote["price"] + sign * (MODEL["spread"] / 2 + MODEL["slippage"]), POINT, sign > 0)
    stop = grid(signal["stop"], POINT, sign < 0)
    executable = close_quote(quote["price"], signal["side"])
    stop_distance = sign * (entry - stop)
    if sign * (executable - stop) < POINT - 1e-9 or stop_distance > MODEL["maxStop"]:
        return {"reason": "Structure stop is outside the modeled range"}
    target = grid(entry + sign * MODEL["rewardRisk"] * stop_distance, POINT, sign > 0)
    per_lot = (stop_distance + MODEL["slippage"]) * MODEL["contract"]
    margin_per_lot = entry * MODEL["contract"] / MODEL["leverage"]
    lots = grid(min(budget / per_lot, 0.5 * equity / margin_per_lot), LOT_STEP, False)
    if lots < LOT_STEP or lots * per_lot > budget + 1e-7:
        return {"reason": "Minimum lot exceeds the modeled risk or margin budget"}
    return {"side": signal["side"], "entry": entry, "stop": stop, "target": target, "lots": lots,
            "riskCash": per_lot * lots, "time": quote["time"]}


def accept_quote(state: dict, quote: dict, now: int) -> bool:
    previous = state["quote"]
    if previous and quote["time"] <= previous["time"]:
        return False
    state["quote"] = {**quote, "change": quote["price"] - previous["price"] if previous else None}
    state["points"] = [*state["points"][-(MAX_POINTS - 1):], {"time": quote["time"], "value": quote["price"]}]
    roll_day(state, quote, now)
    close_position(state, quote)
    update_day(state, quote["price"])
    bar = sampled_bar(state, quote)
    if not bar:
        return True
    cutoff = quote["time"] - LOOKBACK
    state["bars"] = [item for item in (*state["bars"], bar) if item["time"] >= cutoff][-MAX_BARS:]
    state["analysis"] = analyse(state["bars"])
    signal = state["analysis"]["lastSignal"]
    if not signal or signal["time"] != bar["time"] + M15:
        return True
    if quote["time"] - signal["time"] > MAX_SIGNAL_AGE:
        plan = {"reason": "Retest signal expired before the next quote"}
    elif state["position"]:
        plan = {"reason": "A paper position is already open"}
    else:
        day = state["day"]
        plan = plan_trade(signal, quote, day["equity"], risk_budget(day, day["equity"]))
    if "reason" in plan:
        log(state, {"time": quote["time"], "type": "skip", "side": signal["side"], "detail": plan["reason"]})
        return True
    state["position"] = plan
    state["day"]["entries"] += 1
    update_day(state, quote["price"])
    log(state, {"type": "entry", **plan})
    return True


def snapshot(state: dict, now: int) -> dict:
    quote, day = state["quote"], state["day"]
    age = max(0, now - quote["time"]) if quote else None
    equity = day["equity"] if day else MODEL["startingEquity"]
    last = state["bars"][-1] if state["bars"] else None
    analysis = state["analysis"].copy()
    if last and now - (last["time"] + M15) > M15 + SAMPLE_GAP and analysis["stage"] != "warmup":
        analysis["stage"] = "gap"
    analysis["levels"] = [level for level in analysis["levels"] if now - level["knownAt"] < LEVEL_AGE]
    status = "degraded" if state["error"] else "starting"
    if not state["error"] and age is not None:
        status = "live" if age <= QUOTE_MAX_AGE else "stale"
    return {
        "schema": 1, "symbol": "XAU/USD", "source": "Gold API", "mode": "paper", "runtime": "python",
        "status": status, "asOf": now, "quoteAge": age, "quote": quote, "attemptedAt": state["attemptedAt"],
        "backgroundAt": state.get("backgroundAt") or state.get("cronAt"),
        "message": state["error"], "missedBars": state["missedBars"],
        "bars": state["bars"][-192:], "points": state["points"], "sampleCount": (state["candle"] or {}).get("count", 0),
        "latestCandle": last["time"] if last else None, "barCount": len(state["bars"]), "analysis": analysis,
        "account": {
            "equity": equity, "balance": state["cash"], "base": day["base"] if day else None,
            "date": day["date"] if day else None, "riskBudget": risk_budget(day, equity),
            "entries": day["entries"] if day else 0, "halted": day["halted"] if day else False,
            "dailyChange": equity - day["base"] if day else None, "closed": state["closed"], "wins": state["wins"],
        },
        "model": MODEL, "position": state["position"], "events": state["events"][:25],
    }
