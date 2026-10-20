// Run the dashboard strategy outside a browser: read scenarios (JSON) on stdin,
// print {scenario name: generated dashboard config} on stdout.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [, , strategyPath] = process.argv;
const { scenarios } = JSON.parse(readFileSync(0, "utf8"));

let flowInstalled = false;
const defined = new Map();
globalThis.window = globalThis;
globalThis.HTMLElement = class {};
globalThis.customElements = {
  get: (name) => (name === "power-flow-card-plus" ? (flowInstalled ? class {} : undefined) : defined.get(name)),
  define: (name, cls) => defined.set(name, cls),
  whenDefined: () => new Promise(() => {}),
};
// Strategy waits up to 2 s for an optional card; do not wait in tests.
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...args) => realSetTimeout(fn, Math.min(ms, 10), ...args);

await import(pathToFileURL(strategyPath).href);
const strategy = defined.get("ll-strategy-dashboard-energylib");
const out = { registered: window.customStrategies };
for (const s of scenarios) {
  flowInstalled = s.flow;
  out[s.name] = await strategy.generate({ type: "custom:energylib" }, s.hass);
}
process.stdout.write(JSON.stringify(out));
