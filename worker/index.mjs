import { acceptQuote, initialState, quoteFromProvider, snapshot } from "./engine.mjs";
import { assets } from "./assets.generated.mjs";

const QUOTE_URL = "https://api.gold-api.com/price/XAU/USD";
const POLL_SECONDS = 55;
const LEASE_SECONDS = 25;
const JSON_HEADERS = { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" };
const seconds = () => Math.floor(Date.now() / 1000);

const ACQUIRE = `
  INSERT INTO msnr_runtime (id, revision, next_at, lease_until)
  VALUES (1, 1, ?1, ?2)
  ON CONFLICT(id) DO UPDATE SET
    revision = msnr_runtime.revision + 1, next_at = excluded.next_at, lease_until = excluded.lease_until
  WHERE msnr_runtime.next_at <= ?3 AND msnr_runtime.lease_until <= ?3
  RETURNING revision, payload
`;

function readState(payload) {
  const state = JSON.parse(payload ?? "null");
  return state?.schema === 1 ? state : initialState();
}

async function persisted(db, now) {
  const row = await db.prepare("SELECT payload FROM msnr_runtime WHERE id = 1").first();
  return snapshot(readState(row?.payload), now);
}

export async function refresh(db, trigger, now = seconds(), get = fetch) {
  const reservation = await db.prepare(ACQUIRE).bind(now + POLL_SECONDS, now + LEASE_SECONDS, now).first();
  if (!reservation) return persisted(db, seconds());
  const state = readState(reservation.payload);
  state.attemptedAt = now;
  if (trigger === "background") state.backgroundAt = now;
  try {
    const response = await get(QUOTE_URL, { signal: AbortSignal.timeout(8_000) });
    if (!response.ok) throw new Error("Gold API returned HTTP " + response.status + ".");
    const quote = quoteFromProvider(await response.json(), now);
    acceptQuote(state, quote, now);
    state.error = null;
  } catch (error) {
    state.error = error.name === "TimeoutError" ? "Gold API timed out."
      : error.message.startsWith("Gold API returned HTTP") || error.message.startsWith("The provider")
        ? error.message : "Unable to refresh the Gold API quote.";
  }
  const updated = await db.prepare("UPDATE msnr_runtime SET payload = ?, lease_until = 0 WHERE id = 1 AND revision = ?")
    .bind(JSON.stringify(state), reservation.revision).run();
  return updated.meta.changes ? snapshot(state, now) : persisted(db, now);
}

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/state" || url.pathname === "/api/refresh") {
      const method = url.pathname === "/api/state" ? "GET" : "POST";
      if (request.method !== method) return json({ error: "Method not allowed" }, 405);
      const origin = request.headers.get("Origin");
      if (method === "POST" && origin && origin !== url.origin) return json({ error: "Origin denied" }, 403);
      try {
        const result = method === "POST" ? await refresh(env.DB, "request") : await persisted(env.DB, seconds());
        return json(result);
      } catch (error) {
        console.error("MSNR database operation failed", error.message);
        return json({ error: "Paper account storage is unavailable." }, 503);
      }
    }

    if (!["GET", "HEAD"].includes(request.method)) return new Response("Method not allowed", { status: 405 });
    const name = url.pathname === "/" ? "/index.html" : url.pathname;
    const asset = Object.hasOwn(assets, name) ? assets[name] : null;
    if (!asset) return new Response("Not found", { status: 404 });
    return new Response(request.method === "HEAD" ? null : asset.content, {
      headers: {
        "Content-Type": asset.type, "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=0, must-revalidate",
        "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'",
        "Referrer-Policy": "no-referrer",
      },
    });
  },
};
