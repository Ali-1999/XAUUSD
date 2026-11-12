"""Replay the bot's MSNR signals on Dukascopy XAU/USD minute candles."""

import argparse
import csv
import json
from datetime import UTC, date, datetime, time, timedelta
from itertools import groupby
from math import isfinite
from pathlib import Path

import dukascopy_python as duka
import pandas as pd
from backtesting import Backtest, Strategy, __version__ as backtesting_version
from dukascopy_python.instruments import I_XAU_USD

from src.msnr.chart import CANDLE_FIELDS, bucket_start
from src.msnr.engine import LOOKBACK, MAX_SIGNAL_AGE, MODEL, plan_trade, risk_budget
from src.msnr.strategy import LEVEL_AGE, M15, analyse, complete_bars, hourly_levels

MINUTE = 60
CHART_FRAMES = ("M15", "H1", "H4", "D1")
FEE_PER_OUNCE = MODEL["spread"] / 2 + MODEL["slippage"]


def utc_midnight(day: date) -> int:
    return int(datetime.combine(day, time.min, UTC).timestamp())


def historical_rows(start: int, end: int, cache: Path | None) -> list[dict]:
    if cache and cache.is_file():
        with cache.open(newline="", encoding="utf-8") as stream:
            rows = [{"time": int(row["time"]), **{key: float(row[key]) for key in CANDLE_FIELDS[1:]}}
                    for row in csv.DictReader(stream)]
    else:
        candles = duka.fetch_candles(
            instrument=I_XAU_USD, interval=1, time_unit=duka.TimeUnit.MINUTE, offer_side=duka.OfferSide.BID,
            start=datetime.fromtimestamp(start, UTC), end=datetime.fromtimestamp(end, UTC),
        )
        rows = [{"time": int(bar.timestamp.timestamp()), **{key: getattr(bar, key) for key in CANDLE_FIELDS[1:]}}
                for bar in candles]
        if cache and rows:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(cache.suffix + ".tmp")
            with temporary.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, CANDLE_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            temporary.replace(cache)
    validated = {}
    for row in rows:
        stamp = row["time"]
        if not start <= stamp < end:
            continue
        if (stamp % MINUTE or any(not isfinite(row[key]) or row[key] <= 0 for key in CANDLE_FIELDS[1:])
                or row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"])):
            raise ValueError(f"Invalid minute candle at {stamp}")
        if stamp in validated and validated[stamp] != row:
            raise ValueError(f"Conflicting minute candles at {stamp}")
        validated[stamp] = row
    return [validated[stamp] for stamp in sorted(validated)]


def minute_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows).rename(columns=str.title)
    frame.index = pd.to_datetime(frame.pop("Time") + MINUTE, unit="s", utc=True)
    return frame


def eligible_signals(index: pd.DatetimeIndex, trace: list[dict]) -> dict[int, dict]:
    signals = {}
    for signal in trace:
        if signal["type"] != "retest":
            continue
        position = index.searchsorted(pd.to_datetime(signal["time"], unit="s", utc=True), side="right")
        if position >= len(index) - 1:
            continue
        stamp = int(index[position].timestamp())
        if 0 < stamp - signal["time"] <= MAX_SIGNAL_AGE:
            signals[stamp] = signal
    return signals


class MSNRReplay(Strategy):
    signals = None

    def init(self):
        self.day = None

    def next(self):
        stamp = int(self.data.index[-1].timestamp())
        day = stamp // 86_400
        if self.day is None or self.day["date"] != day:
            self.day = {"date": day, "base": self.equity, "entries": 0, "halted": False}
        self.day["halted"] |= self.equity <= self.day["base"] * (1 - MODEL["dailyCutoffPercent"] / 100)
        signal = self.signals.get(stamp)
        if signal is None or self.position or self.orders:
            return
        quote = {"time": stamp, "price": float(self.data.Close[-1])}
        trade = plan_trade(signal, quote, self.equity, risk_budget(self.day, self.equity))
        if "reason" in trade:
            return
        enter = self.buy if trade["side"] == "buy" else self.sell
        enter(size=round(trade["lots"] * MODEL["contract"]), sl=trade["stop"], tp=trade["target"])
        self.day["entries"] += 1


def execution_cost(size: int, _price: float) -> float:
    return abs(size) * FEE_PER_OUNCE


def finite(value, digits=2):
    return round(float(value), digits) if value is not None and isfinite(value) else None


def trade_records(ledger: pd.DataFrame) -> list[dict]:
    return [
        {"side": "buy" if row.Size > 0 else "sell", "ounces": abs(int(row.Size)),
         "entryTime": int(row.EntryTime.timestamp()), "exitTime": int(row.ExitTime.timestamp()),
         "entry": finite(row.EntryPrice, 3), "exit": finite(row.ExitPrice, 3),
         "stop": finite(row.SL, 3), "target": finite(row.TP, 3),
         "pnl": finite(row.PnL), "cost": finite(row.Commission)}
        for row in ledger.itertuples(index=False)
    ]


def replay(rows: list[dict], start: int, end: int) -> dict:
    window = [row for row in rows if start <= row["time"] < end]
    if len(window) < 3:
        raise ValueError("The selected UTC range needs at least three observed minute candles")
    trace = []
    warmup = [row for row in rows if start - LOOKBACK <= row["time"] < end]
    m15 = complete_bars(warmup, M15, MINUTE)
    analyse(m15, trace=trace)
    trace = [event for event in trace if start <= event["time"] < end]
    frame = minute_frame(window)
    simulator = Backtest(
        frame, MSNRReplay, cash=MODEL["startingEquity"], margin=1 / MODEL["leverage"],
        commission=execution_cost, trade_on_close=True, finalize_trades=True,
    )
    stats = simulator.run(signals=eligible_signals(frame.index, trace))
    trades = trade_records(stats["_trades"])
    return {
        "start": start, "end": end, "firstMinute": window[0]["time"], "lastMinute": window[-1]["time"],
        "observations": len(window), "signals": sum(event["type"] == "retest" for event in trace),
        "metrics": {"finalEquity": finite(stats["Equity Final [$]"]), "returnPercent": finite(stats["Return [%]"]),
                    "drawdownPercent": finite(abs(stats["Max. Drawdown [%]"])),
                    "trades": len(trades), "winPercent": finite(stats["Win Rate [%]"])},
        "markers": trace, "contextLevels": list(hourly_levels(m15)), "trades": trades,
    }


def aggregate_chart(rows: list[dict], timeframe: str) -> list:
    output = []
    for stamp, group in groupby(rows, key=lambda row: bucket_start(row["time"], timeframe)):
        bars = list(group)
        output.append([stamp, finite(bars[0]["open"], 3), finite(max(bar["high"] for bar in bars), 3),
                       finite(min(bar["low"] for bar in bars), 3), finite(bars[-1]["close"], 3)])
    return output


def build_report(rows: list[dict], starts: list[int], end: int) -> dict:
    visible = [row for row in rows if starts[0] <= row["time"] < end]
    return {
        "schema": 1, "symbol": "XAU/USD", "source": "Dukascopy XAU/USD bid candles",
        "engine": f"Backtesting.py {backtesting_version}", "createdAt": int(datetime.now(UTC).timestamp()),
        "model": {"startingEquity": MODEL["startingEquity"], "spread": MODEL["spread"],
                  "slippagePerSide": MODEL["slippage"], "feePerOuncePerSide": FEE_PER_OUNCE,
                  "riskPercent": MODEL["riskPercent"], "dailyCutoffPercent": MODEL["dailyCutoffPercent"],
                  "maxEntries": MODEL["maxEntries"], "levelAgeSeconds": LEVEL_AGE},
        "candles": {frame: aggregate_chart(visible, frame) for frame in CHART_FRAMES},
        "runs": [replay(rows, start, end) for start in starts],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    ranges = parser.add_mutually_exclusive_group(required=True)
    ranges.add_argument("--start", type=date.fromisoformat, help="Inclusive UTC date, YYYY-MM-DD")
    ranges.add_argument("--days", type=int, nargs="+", help="One or more calendar-day test windows")
    parser.add_argument("--end", type=date.fromisoformat, default=datetime.now(UTC).date(),
                        help="Exclusive UTC date, YYYY-MM-DD (default: today)")
    parser.add_argument("--cache", type=Path, help="Read or write a Dukascopy minute CSV for the requested dates")
    parser.add_argument("--output", type=Path, default=Path(".history/replay.json"))
    args = parser.parse_args()
    if args.days and any(days < 1 for days in args.days):
        parser.error("--days values must be positive")
    starts = [args.start] if args.start else sorted({args.end - timedelta(days=days) for days in args.days})
    if starts[0] >= args.end:
        parser.error("--start must be earlier than --end")
    starts = [utc_midnight(day) for day in starts]
    end = utc_midnight(args.end)
    report = build_report(historical_rows(starts[0] - LOOKBACK, end, args.cache), starts, end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    for run in report["runs"]:
        print(f"{(run['end'] - run['start']) // 86_400} days, {run['observations']:,} minute bars: {run['metrics']}")
    print(args.output)


if __name__ == "__main__":
    main()
