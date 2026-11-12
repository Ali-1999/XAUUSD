import asyncio
import json
import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from src.msnr.chart import MAX_CANDLES, TIMEFRAMES, bucket_start, chart_snapshot, record_quote
from src.msnr.engine import QuoteError, accept_quote, initial_state
from src.msnr.store import persisted, refresh

NOW = 1_794_492_428


class Statement:
    def __init__(self, db, sql, params=()):
        self.db, self.sql, self.params = db, sql, params

    def bind(self, *params):
        return Statement(self.db, self.sql, params)

    async def first(self):
        row = self.db.execute(self.sql, self.params).fetchone()
        return SimpleNamespace(**dict(row)) if row else None

    async def run(self):
        changes = self.db.execute(self.sql, self.params).rowcount
        return SimpleNamespace(meta=SimpleNamespace(changes=changes))


class Database:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        migration = Path(__file__).resolve().parents[1] / "migrations" / "0001_engine.sql"
        self.db.executescript(migration.read_text())

    def prepare(self, sql):
        return Statement(self.db, sql)


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeframe_history_upgrades_existing_state_and_updates_with_quotes(self):
        db = Database()
        self.addCleanup(db.db.close)
        start = bucket_start(NOW, "M15")
        state = initial_state()
        state["bars"] = [{"time": start - 900, "open": 3895, "high": 3905, "low": 3890, "close": 3898}]
        accept_quote(state, {"time": start + 10, "price": 3900, "observedAt": start + 10}, start + 10)
        db.db.execute("INSERT INTO msnr_runtime VALUES (1, 1, 0, 0, ?)", (json.dumps(state),))

        old = await persisted(db, start + 10, "M15")
        self.assertEqual([candle["time"] for candle in old["chart"]["candles"]], [start - 900, start])

        async def provider(now):
            return {"time": now, "price": 3902, "observedAt": now}

        updated = await refresh(db, "background", provider, now=start + 70, timeframe="M5")
        self.assertEqual(updated["chart"], {"timeframe": "M5", "inProgress": True, "candles": [
            {"time": start, "open": 3900, "high": 3902, "low": 3900, "close": 3902},
        ]})
        for frame in TIMEFRAMES:
            current = await persisted(db, start + 70, frame)
            with self.subTest(frame=frame):
                self.assertEqual(current["chart"]["timeframe"], frame)
                self.assertEqual(current["chart"]["candles"][-1]["close"], 3902)
                self.assertTrue(current["chart"]["inProgress"])
        self.assertNotIn("chart", await persisted(db, start + 70))

    async def test_existing_state_survives_and_concurrent_refresh_fetches_once(self):
        db = Database()
        self.addCleanup(db.db.close)
        state = initial_state()
        state["cash"] = 10_400
        accept_quote(state, {"time": NOW - 60, "price": 3900, "observedAt": NOW - 60}, NOW - 60)
        db.db.execute("INSERT INTO msnr_runtime VALUES (1, 1, 0, 0, ?)", (json.dumps(state),))
        calls = 0

        async def provider(now):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return {"time": now, "price": 3901, "observedAt": now}

        await asyncio.gather(refresh(db, "background", provider, now=NOW), refresh(db, "request", provider, now=NOW))
        result = await persisted(db, NOW)
        self.assertEqual(calls, 1)
        self.assertEqual(result["runtime"], "python")
        self.assertEqual(result["account"]["base"], 10_400)
        self.assertEqual(result["account"]["riskBudget"], 26)
        self.assertEqual(result["backgroundAt"], NOW)
        self.assertEqual(len(result["points"]), 2)

    async def test_failed_quote_retains_last_observation(self):
        db = Database()
        self.addCleanup(db.db.close)

        async def provider(now):
            return {"time": now, "price": 3900, "observedAt": now}

        async def stale(_now):
            raise QuoteError("The provider timestamp is stale.")

        await refresh(db, "request", provider, now=NOW)
        result = await refresh(db, "background", stale, now=NOW + 60)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["quote"]["time"], NOW)
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["events"], [])


class ChartTests(unittest.TestCase):
    def test_candle_boundaries_follow_utc_calendar(self):
        def utc(year, month, day, hour=0, minute=0):
            return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())

        sunday = utc(2026, 11, 1, 23, 59)
        monday = utc(2026, 11, 2)
        self.assertEqual(bucket_start(sunday, "M20"), utc(2026, 11, 1, 23, 40))
        self.assertEqual(bucket_start(sunday, "H3"), utc(2026, 11, 1, 21))
        self.assertEqual(bucket_start(sunday, "W1"), utc(2026, 10, 26))
        self.assertEqual(bucket_start(monday, "W1"), monday)
        self.assertEqual(bucket_start(utc(2026, 12, 1), "MN1"), utc(2026, 12, 1))
        self.assertEqual(bucket_start(utc(2027, 1, 31, 23, 59), "MN1"), utc(2027, 1, 1))

    def test_history_retains_only_latest_candles_and_leaves_sampling_gaps(self):
        state = initial_state()
        start = bucket_start(NOW, "M1")
        for index in range(MAX_CANDLES + 3):
            quote = {"time": start + index * 120, "price": 3900 + index, "observedAt": start + index * 120}
            accept_quote(state, quote, quote["time"])
            record_quote(state, quote)
        chart = chart_snapshot(state, "M1", quote["time"] + 60)
        self.assertEqual(len(chart["candles"]), MAX_CANDLES)
        self.assertEqual(chart["candles"][0]["time"], start + 360)
        self.assertEqual(chart["candles"][1]["time"], start + 480)
        self.assertFalse(chart["inProgress"])
