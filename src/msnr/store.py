import json
from time import time
from typing import Awaitable, Callable

from .chart import chart_snapshot, record_quote
from .engine import QuoteError, accept_quote, initial_state, snapshot

POLL_SECONDS = 55
LEASE_SECONDS = 25

ACQUIRE = """
    INSERT INTO msnr_runtime (id, revision, next_at, lease_until)
    VALUES (1, 1, ?1, ?2)
    ON CONFLICT(id) DO UPDATE SET
        revision = msnr_runtime.revision + 1, next_at = excluded.next_at, lease_until = excluded.lease_until
    WHERE msnr_runtime.next_at <= ?3 AND msnr_runtime.lease_until <= ?3
    RETURNING revision, payload
"""

UPDATE = "UPDATE msnr_runtime SET payload = ?, lease_until = 0 WHERE id = 1 AND revision = ?"
SELECT = "SELECT payload FROM msnr_runtime WHERE id = 1"


def read_state(payload: str | None) -> dict:
    state = json.loads(payload) if payload else None
    if state is None:
        return initial_state()
    if not isinstance(state, dict) or state.get("schema") != 1:
        raise ValueError("Unsupported MSNR state schema")
    return state


def response_snapshot(state: dict, now: int, timeframe: str | None) -> dict:
    result = snapshot(state, now)
    if timeframe is not None:
        result["chart"] = chart_snapshot(state, timeframe, now)
    return result


async def persisted(db, now: int, timeframe: str | None = None) -> dict:
    row = await db.prepare(SELECT).first()
    return response_snapshot(read_state(row.payload if row else None), now, timeframe)


async def refresh(
    db, trigger: str, get_quote: Callable[[int], Awaitable[dict]], *,
    now: int | None = None, timeframe: str | None = None,
) -> dict:
    now = int(time()) if now is None else now
    reservation = await db.prepare(ACQUIRE).bind(now + POLL_SECONDS, now + LEASE_SECONDS, now).first()
    if not reservation:
        return await persisted(db, now, timeframe)
    state = read_state(reservation.payload)
    state["attemptedAt"] = now
    state["runtime"] = "python"
    if trigger == "background":
        state["backgroundAt"] = now
    try:
        quote = await get_quote(now)
    except QuoteError as error:
        state["error"] = str(error)
    else:
        if accept_quote(state, quote, now):
            record_quote(state, quote)
        state["error"] = None
    payload = json.dumps(state, allow_nan=False, separators=(",", ":"))
    updated = await db.prepare(UPDATE).bind(payload, reservation.revision).run()
    if updated.meta.changes:
        return response_snapshot(state, now, timeframe)
    return await persisted(db, now, timeframe)
