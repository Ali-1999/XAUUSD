from datetime import UTC, datetime

from .strategy import M15

DAY = 86_400
WEEK = 7 * DAY
MONDAY_OFFSET = 3 * DAY
MAX_CANDLES = 120
TIMEFRAMES = {
    **{f"M{minutes}": minutes * 60 for minutes in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)},
    **{f"H{hours}": hours * 3600 for hours in (1, 2, 3, 4, 6, 8, 12)},
    "D1": DAY, "W1": WEEK, "MN1": None,
}
CANDLE_FIELDS = ("time", "open", "high", "low", "close")


def bucket_start(time: int, timeframe: str) -> int:
    duration = TIMEFRAMES[timeframe]
    if duration is None:
        utc = datetime.fromtimestamp(time, UTC)
        return int(datetime(utc.year, utc.month, 1, tzinfo=UTC).timestamp())
    offset = MONDAY_OFFSET if timeframe == "W1" else 0
    return (time + offset) // duration * duration - offset


def append_candle(series: list, timeframe: str, observation: list) -> None:
    time, opened, high, low, closed = observation
    bucket = bucket_start(time, timeframe)
    if series and series[-1][0] == bucket:
        candle = series[-1]
        candle[2] = max(candle[2], high)
        candle[3] = min(candle[3], low)
        candle[4] = closed
    else:
        series.append([bucket, opened, high, low, closed])
        if len(series) > MAX_CANDLES:
            del series[0]


def point_candle(time: int, price: float) -> list:
    return [time, price, price, price, price]


def seed_history(state: dict, timeframes) -> dict:
    points = [point_candle(point["time"], point["value"]) for point in state["points"]]
    m15 = [[bar[key] for key in CANDLE_FIELDS] for bar in state["bars"]]
    completed = {bar[0] for bar in m15}
    combined = sorted([*m15, *(point for point in points if point[0] // M15 * M15 not in completed)])
    history = {}
    for frame in timeframes:
        duration = TIMEFRAMES[frame]
        source = combined if duration is None or duration % M15 == 0 else points
        series = history[frame] = []
        for observation in source:
            append_candle(series, frame, observation)
    return history


def record_quote(state: dict, quote: dict) -> None:
    if "chartHistory" not in state:
        state["chartHistory"] = seed_history(state, TIMEFRAMES)
        return  # The accepted quote is already in the spot points used to seed history.
    observation = point_candle(quote["time"], quote["price"])
    for frame, series in state["chartHistory"].items():
        append_candle(series, frame, observation)


def chart_snapshot(state: dict, timeframe: str, now: int) -> dict:
    if timeframe == "spot":
        return {"timeframe": timeframe, "points": state["points"]}
    history = state.get("chartHistory")
    series = history[timeframe] if history else seed_history(state, (timeframe,))[timeframe]
    return {
        "timeframe": timeframe,
        "candles": [dict(zip(CANDLE_FIELDS, candle, strict=True)) for candle in series],
        "inProgress": bool(series and series[-1][0] == bucket_start(now, timeframe)),
    }
