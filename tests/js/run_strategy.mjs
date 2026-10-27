// Run the dashboard strategy outside a browser: read scenarios (JSON) on stdin,
// print {scenario name: generated dashboard config} on stdout.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [, , strategyPath] = process.argv;
const { scenarios, version = "strategy-test-1" } = JSON.parse(readFileSync(0, "utf8"));

const defined = new Map();
globalThis.window = globalThis;
globalThis.HTMLElement = class {};
globalThis.customElements = {
  get: (name) => defined.get(name),
  define: (name, cls) => defined.set(name, cls),
  // The HA app shell is already defined; optional cards never arrive.
  whenDefined: (name) => (name === "home-assistant" ? Promise.resolve() : new Promise(() => {})),
};
// Strategy waits up to 2 s for an optional card; do not wait in tests.
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...args) => realSetTimeout(fn, Math.min(ms, 10), ...args);

const moduleUrl = pathToFileURL(strategyPath);
moduleUrl.searchParams.set("v", version);
await import(moduleUrl.href);
await Promise.resolve(); // let the deferred definition run
const strategy = defined.get("ll-strategy-dashboard-energylib");
const twinStrategy = defined.get("ll-strategy-view-energylib-twin");
const out = { registered: window.customStrategies, standaloneTwin: await twinStrategy.generate() };
for (const s of scenarios) {
  out[s.name] = await strategy.generate({ type: "custom:energylib" }, s.hass);
}
process.stdout.write(JSON.stringify(out));
