import json
import logging
from time import time
from urllib.parse import parse_qs, urlsplit

from js import AbortSignal
from workers import DurableObject, Response, WorkerEntrypoint, fetch

from msnr.chart import TIMEFRAMES
from msnr.engine import QuoteError, quote_from_provider
from msnr.store import persisted, refresh

QUOTE_URL = "https://api.gold-api.com/price/XAU/USD"
INTERVAL_MS = 60_000
JSON_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
LOGGER = logging.getLogger(__name__)


async def get_quote(now: int) -> dict:
    try:
        response = await fetch(QUOTE_URL, signal=AbortSignal.timeout(8_000))
        if not response.ok:
            raise QuoteError(f"Gold API returned HTTP {response.status}.")
        data = json.loads(await response.text())
    except QuoteError:
        raise
    except Exception as error:
        message = "Gold API timed out." if getattr(error, "name", "") == "TimeoutError" else "Unable to refresh the Gold API quote."
        raise QuoteError(message) from error
    return quote_from_provider(data, now)


def json_response(data: dict, status: int = 200):
    return Response.json(data, status=status, headers=JSON_HEADERS)


class PaperBotClock(DurableObject):
    async def fetch(self, request):
        if await self.ctx.storage.getAlarm() is None:
            await self.ctx.storage.setAlarm(int(time() * 1000) + INTERVAL_MS)
        return Response(None, status=204)

    async def alarm(self):
        await self.ctx.storage.setAlarm(int(time() * 1000) + INTERVAL_MS)
        await refresh(self.env.DB, "background", get_quote)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlsplit(request.url)
        methods = {"/api/state": "GET", "/api/refresh": "POST"}
        method = methods.get(url.path)
        if method is None:
            return Response("Not found", status=404)
        if request.method != method:
            return json_response({"error": "Method not allowed"}, 405)
        frames = parse_qs(url.query, keep_blank_values=True).get("timeframe")
        timeframe = frames[0] if frames else None
        if frames and (len(frames) != 1 or (timeframe != "spot" and timeframe not in TIMEFRAMES)):
            return json_response({"error": "Unknown chart timeframe"}, 400)
        origin = request.headers.get("Origin")
        if method == "POST" and origin and origin != f"{url.scheme}://{url.netloc}":
            return json_response({"error": "Origin denied"}, 403)
        try:
            if method == "POST":
                clock = self.env.CLOCK.getByName("xauusd-msnr")
                await clock.fetch("https://clock.internal/start")
                result = await refresh(self.env.DB, "request", get_quote, timeframe=timeframe)
            else:
                result = await persisted(self.env.DB, int(time()), timeframe)
            return json_response(result)
        except Exception:
            LOGGER.exception("MSNR storage or scheduling operation failed")
            return json_response({"error": "Paper account storage is unavailable."}, 503)
