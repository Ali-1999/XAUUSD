import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const wrangler = new URL("../node_modules/wrangler/bin/wrangler.js", import.meta.url);
const worker = spawn(process.execPath, [
  fileURLToPath(wrangler), "dev", "--local",
  "--ip", "127.0.0.1", "--port", process.env.PORT ?? "4173",
], { stdio: "inherit", env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => worker.kill(signal));
worker.on("exit", (code) => { process.exitCode = code ?? 0; });
