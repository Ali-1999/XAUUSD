import csv
import unittest
from datetime import UTC, datetime
from pathlib import Path

from src.msnr.engine import QuoteError, accept_quote, initial_state, plan_trade, quote_from_provider, snapshot
from src.msnr.strategy import M15, analyse


with Path(__file__).with_name("fixtures").joinpath("sample_m15.csv").open(newline="") as source:
    BARS = [
        {"time": int(row["time"]), **{key: float(row[key]) for key in ("open", "high", "low", "close")}}
        for row in csv.DictReader(source)
    ]
START = BARS[0]["time"]


def quote(time, price):
    return {"time": time, "price": price, "observedAt": time}


class EngineTests(unittest.TestCase):
    def test_completed_h4_and_later_m15_retest(self):
        self.assertEqual(analyse(BARS[:31])["stage"], "warmup")
        self.assertEqual(analyse(BARS[:33])["stage"], "break")
        self.assertEqual(analyse(BARS[:34])["stage"], "retest")
        signal = analyse(BARS[:35])["lastSignal"]
        self.assertEqual((signal["side"], signal["time"]), ("buy", BARS[34]["time"] + M15))
        self.assertEqual(analyse(BARS[:131])["lastSignal"]["side"], "sell")

    def test_gap_cancels_setup(self):
        gap = {**BARS[34], "time": BARS[34]["time"] + M15}
        self.assertIsNone(analyse([*BARS[:34], gap])["lastSignal"])

    def test_candle_requires_sufficient_observations(self):
        state = initial_state()
        for index in range(15):
            time = START + index * 60
            accept_quote(state, quote(time, 3900 + index / 10), time)
        accept_quote(state, quote(START + M15, 3902), START + M15)
        self.assertEqual(state["bars"], [{
            "time": START, "open": 3900, "high": 3901.4, "low": 3900, "close": 3901.4,
        }])
        accept_quote(state, quote(START + 2 * M15, 3904), START + 2 * M15)
        self.assertEqual((len(state["bars"]), state["missedBars"]), (1, 1))

    def test_entry_deduplication_and_daily_equity_rollover(self):
        state = initial_state()
        state["bars"] = BARS[:34]
        retest = BARS[34]
        time = retest["time"] + M15
        state["candle"] = {**retest, "first": retest["time"], "last": time - 60, "count": 15, "gap": 60}
        self.assertTrue(accept_quote(state, quote(time, retest["close"]), time))
        self.assertEqual(state["day"]["entries"], 1)
        self.assertEqual(state["position"]["side"], "buy")
        self.assertLessEqual(state["position"]["riskCash"], 25)
        self.assertFalse(accept_quote(state, quote(time, retest["close"]), time))
        stop = state["position"]["stop"] - 0.4
        accept_quote(state, quote(time + 60, stop), time + 60)
        self.assertIsNone(state["position"])
        self.assertLess(state["cash"], 10_000)
        self.assertEqual(state["day"]["base"], 10_000)
        next_day = START + 86_400
        accept_quote(state, quote(next_day, stop), next_day)
        self.assertEqual(state["day"]["base"], state["cash"])
        self.assertEqual(state["day"]["entries"], 0)

    def test_entry_price_and_risk_gates(self):
        signal = {"side": "buy", "stop": 3896, "reference": 3900}
        self.assertIn("Daily risk", plan_trade(signal, quote(START, 3900), 10_000, 0)["reason"])
        self.assertIn("entry tolerance", plan_trade(signal, quote(START, 3910), 10_000, 25)["reason"])
        self.assertIn("Minimum lot", plan_trade(signal, quote(START, 3900), 10_000, 1)["reason"])

    def test_quote_freshness_and_currency(self):
        data = {"symbol": "XAU", "currency": "USD", "price": 3900,
                "updatedAt": datetime.fromtimestamp(START, UTC).isoformat()}
        self.assertEqual(quote_from_provider(data, START + 30)["price"], 3900)
        with self.assertRaisesRegex(QuoteError, "stale"):
            quote_from_provider(data, START + 121)
        with self.assertRaisesRegex(QuoteError, "invalid"):
            quote_from_provider({**data, "currency": "EUR"}, START)
        state = initial_state()
        accept_quote(state, quote(START, 3900), START)
        self.assertEqual(snapshot(state, START + 121)["status"], "stale")
