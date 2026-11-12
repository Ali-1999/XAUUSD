import asyncio
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

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
