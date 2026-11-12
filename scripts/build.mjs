import { mkdir, readFile, writeFile } from "node:fs/promises";
import { build } from "esbuild";

const files = {
  "/index.html": ["public/index.html", "text/html; charset=utf-8"],
  "/style.css": ["public/style.css", "text/css; charset=utf-8"],
  "/app.js": ["public/app.js", "application/javascript; charset=utf-8"],
  "/vendor/lightweight-charts.js": [
    "node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js",
    "application/javascript; charset=utf-8",
  ],
  "/vendor/NOTICE": ["public/vendor/NOTICE", "text/plain; charset=utf-8"],
  "/vendor/LICENSE": ["node_modules/lightweight-charts/LICENSE", "text/plain; charset=utf-8"],
};
const assets = {};
for (const [route, [path, type]] of Object.entries(files)) {
  assets[route] = { type, content: await readFile(path, "utf8") };
}
await writeFile("worker/assets.generated.mjs", "export const assets = " + JSON.stringify(assets) + ";\n");
await mkdir("dist/server", { recursive: true });
await build({
  entryPoints: ["worker/index.mjs"],
  outfile: "dist/server/index.js",
  bundle: true, format: "esm", platform: "browser", target: "es2022",
});
await build({
  entryPoints: ["worker/cloudflare.mjs"],
  outfile: "dist/cloudflare/index.js",
  bundle: true, format: "esm", platform: "browser", target: "es2022",
  external: ["cloudflare:workers"],
});
