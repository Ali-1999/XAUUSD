import { DurableObject } from "cloudflare:workers";
import dashboard, { refresh } from "./index.mjs";

const INTERVAL_MS = 60_000;

export class PaperBotClock extends DurableObject {
  async fetch() {
    if (await this.ctx.storage.getAlarm() === null) {
      await this.ctx.storage.setAlarm(Date.now() + INTERVAL_MS);
    }
    return new Response(null, { status: 204 });
  }

  async alarm() {
    // Install the next alarm before attempting the upstream request.
    await this.ctx.storage.setAlarm(Date.now() + INTERVAL_MS);
    await refresh(this.env.DB, "background");
  }
}

export default {
  fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (path === "/api/refresh" && request.method === "POST") {
      const clock = env.CLOCK.getByName("xauusd-msnr");
      ctx.waitUntil(clock.fetch("https://clock.internal/start", { method: "POST" }));
    }
    return dashboard.fetch(request, env, ctx);
  },
};
