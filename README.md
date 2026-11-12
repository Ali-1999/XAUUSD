# XAU/USD MSNR Live

Python Cloudflare Worker, D1 state and a browser dashboard for sampled XAU/USD spot data, closed-candle MSNR analysis and a paper account. Python handles quote ingestion, strategy, execution, persistence and the background timer. The dashboard uses the free Gold API spot feed and TradingView Lightweight Charts. [Gold API](https://gold-api.com/docs) · [Lightweight Charts](https://github.com/tradingview/lightweight-charts)

## What runs

- The Worker accepts a fresh XAU/USD provider timestamp and records at most one new observation per polling interval. The dashboard shows the feed timestamp, age, and whether native background checks are active.
- The chart selector covers the spot sample line and all 21 standard MetaTrader 5 candle periods from M1 to MN1. Python builds sampled OHLC bars in UTC, with weeks starting Monday and months starting on the first. It retains up to 120 candles per period and marks the current candle as in progress. Longer periods build history as quotes arrive. The chart can show partial sample coverage, including for the initial candle.
- M15 candles need at least nine observations with no gap over three minutes and adequate coverage near both ends. H4 candles require 16 consecutive completed M15 candles. Warm-up requires at least eight hours of adequate samples, plus alignment to a UTC H4 boundary. The spot series starts with the first observation.
- The first visit to a fresh H4 candle-body A or V level consumes that level. A qualifying rejection must be followed by a closed M15 structure break and a later retest. A gap clears an in-progress setup. These are the same setup stages used in the MT5 demo baseline.
- **MSNR overlays** draws H4 A/V and M15 structure levels, closed-candle setup markers, and paper entry, stop and target levels directly on the live chart. The H1 open-close overlay uses the previous close of a run of three contiguous, same-direction H1 candles. It becomes visible when the third candle closes. This overlay is visual context. Its pattern is described by [MSNR Alchemist](https://github.com/Gesh0825/msnr-alchemist).
- The simulated account starts at USD 10,000. Its first observed equity each UTC day sets the daily base. Risk starts at 0.25% per entry, capped by remaining room above a 1% daily equity cutoff. The engine allows up to three entries per UTC day, one open position and a 2R target. It rounds size down to the modeled lot step.
- The public spot feed is indicative. Paper fills use a USD 0.35 modeled spread, USD 0.20 adverse slippage, a 100-ounce lot and 20:1 modeled leverage. Stops and targets are evaluated on observed samples, so intermediate prices and broker execution can differ. The Windows MT5 demo package is the broker execution path.

## Local run

Install Python 3.13, uv, and Node.js 22 or newer for the Wrangler CLI.

```sh
uv sync
uv run python -m unittest discover -s test -v
uv run pywrangler d1 migrations apply DB --local
uv run pywrangler dev
```

Open the localhost URL printed by Wrangler. The preview uses the free quote endpoint. Test fixtures are used only by the test runner.

## Historical replay

Open **Backtest** to inspect the bundled 30-day and 90-day XAU/USD runs through 11 November 2026 UTC. Choose M15, H1, H4 or D1, drag the replay position, or select a trade's entry time to inspect its entry, stop, target and exit on the chart. The metrics describe the selected test window.

Generate a replay for your own dates with Python. The end date is exclusive. Open the resulting JSON through **Load a replay file** in the Backtest view. The browser reads the report locally.

```sh
uv run --group backtest python -m backtest.run --start 2026-08-14 --end 2026-11-12
uv run --group backtest python -m backtest.run --days 30 90 --end 2026-11-12 --output public/history/replay.json
```

The runner downloads XAU/USD bid M1 candles from Dukascopy and fetches eight days of prior data for signal warm-up. `--cache .history/xauusd-bid-m1.csv` reuses a local CSV for the same dates. The report contains derived chart candles at M15 and above, setup markers, and simulated results. [Dukascopy Python](https://github.com/Eghosa-Osayande/dukascopy) · [Dukascopy historical data](https://www.dukascopy.com/swiss/english/marketwatch/historical/)

Backtesting.py submits the same MSNR signals and uses the bot's daily equity base, trade sizing and entry limits. The entry model uses the first observed M1 close after a confirmed retest within two minutes. It applies USD 0.375 per ounce on each side, comprising half of the modelled spread plus slippage, and uses minute OHLC for stop and target execution. Any position still open at the end is closed for the report. Gold API spot samples power the live view. [Backtesting.py](https://github.com/kernc/backtesting.py)

## Cloudflare deployment

Sign in to the Cloudflare account where the Worker should live. Create the D1 database and copy its returned database ID into the existing `DB` entry in `wrangler.jsonc` as `database_id`.

```sh
uv sync
uv run pywrangler login
uv run pywrangler d1 create xauusd-msnr-live
uv run pywrangler d1 migrations apply DB --remote
uv run pywrangler deploy
```

If you have already deployed or claimed this Worker, use its existing D1 database ID and apply pending migrations there. Open the deployed dashboard once to start the Durable Object alarm. It schedules the next check every minute and shares the refresh lease with the browser. Confirm `backgroundAt` advances in `GET /api/state` while the dashboard is closed. The UI shows whether recent background checks were observed. [Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/)

For a GitHub-connected Cloudflare build, make uv and Python 3.13 available and run `uv sync` followed by `uv run pywrangler deploy`. Apply the D1 migration before the first production build. [Python Workers](https://developers.cloudflare.com/workers/languages/python/) · [Cloudflare GitHub integration](https://developers.cloudflare.com/workers/ci-cd/builds/git-integration/github-integration/)

## API

| Endpoint | Behaviour |
| --- | --- |
| `GET /api/state` | Reads the persisted quote, analysis, paper account and journal. The `runtime` field identifies the Python backend. |
| `POST /api/refresh` | Runs the throttled update and returns current state. |

Both endpoints accept an optional `timeframe` query parameter, such as `M5`, `H4`, `D1` or `spot`, to include the selected `chart` series. The chart selector uses a read-only request to switch periods. The strategy continues to use its validated M15 candles and H4 levels.

The worker enforces one database-backed refresh lease across requests and scheduled runs. The dashboard keeps polling while open. The Cloudflare Durable Object alarm continues with the browser closed. If the upstream response is invalid, late or unavailable, the dashboard shows the feed issue and the bot waits for a fresh observation before evaluating an entry.
