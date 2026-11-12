# XAU/USD MSNR Live

Cloudflare Worker, D1 state and a responsive dashboard for sampled XAU/USD spot data, closed-candle MSNR analysis and a paper account. It uses the free Gold API spot endpoint and the open-source TradingView Lightweight Charts package. [Gold API](https://gold-api.com/docs) · [Lightweight Charts](https://github.com/tradingview/lightweight-charts)

## What runs

- The Worker accepts a fresh XAU/USD provider timestamp and records at most one new observation per polling interval. The dashboard shows the feed timestamp, age, and whether native background checks are active.
- M15 candles need at least nine observations with no gap over three minutes and adequate coverage near both ends. H4 candles require 16 consecutive completed M15 candles. Warm-up requires at least eight hours of adequate samples, plus alignment to a UTC H4 boundary. The spot series starts with the first observation.
- The first visit to a fresh H4 candle-body A or V level consumes that level. A qualifying rejection must be followed by a closed M15 structure break and a later retest. A gap clears an in-progress setup. These are the same setup stages used in the MT5 demo baseline.
- The simulated account starts at USD 10,000. Its first observed equity each UTC day sets the daily base. Risk starts at 0.25% per entry, capped by remaining room above a 1% daily equity cutoff. The engine allows up to three entries per UTC day, one open position and a 2R target. It rounds size down to the modeled lot step.
- The public spot feed is indicative. Paper fills use a USD 0.35 modeled spread, USD 0.20 adverse slippage, a 100-ounce lot and 20:1 modeled leverage. Stops and targets are evaluated on observed samples, so intermediate prices and broker execution can differ. The Windows MT5 demo package is the broker execution path.

## Local run

Use Node.js 22 or newer.

```sh
npm ci
npm test
npm run dev
```

Open the localhost URL printed by Wrangler. The preview applies the D1 migration and uses the free quote endpoint. Test fixtures are used only by the test runner.

## Cloudflare deployment

Sign in to the Cloudflare account where the Worker should live. Create the D1 database and copy its returned database ID into the existing `DB` entry in `wrangler.jsonc` as `database_id`.

```sh
npm ci
npx wrangler login
npx wrangler d1 create xauusd-msnr-live
npx wrangler d1 migrations apply DB --remote
npm run deploy
```

If you have already deployed or claimed this Worker, use its existing D1 database ID and apply pending migrations there. Open the deployed dashboard once to start the Durable Object alarm. It schedules the next check every minute and shares the refresh lease with the browser. Confirm `backgroundAt` advances in `GET /api/state` while the dashboard is closed. The UI shows whether recent background checks were observed. [Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/)

For GitHub, commit this source without local Wrangler state or credentials. Push it to your chosen repository. In Cloudflare Workers & Pages, you can link the GitHub repository to deploy later commits. Use `npm ci && npm run build` for the build and `npx wrangler deploy` for deployment. Apply the remote D1 migration before the first production build. [Cloudflare GitHub integration](https://developers.cloudflare.com/workers/ci-cd/builds/git-integration/github-integration/)

## API

| Endpoint | Behaviour |
| --- | --- |
| `GET /api/state` | Reads the persisted quote, analysis, paper account and journal. |
| `POST /api/refresh` | Runs the throttled update and returns current state. |

The worker enforces one database-backed refresh lease across requests and scheduled runs. The dashboard keeps polling while open. The Cloudflare Durable Object alarm continues with the browser closed. If the upstream response is invalid, late or unavailable, the dashboard shows the feed issue and the bot waits for a fresh observation before evaluating an entry.
