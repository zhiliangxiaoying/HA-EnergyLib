import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

globalThis.HTMLElement = class {
  replaceChildren(node) { this.firstChild = node; }
};
globalThis.window = globalThis;
const classes = new Map();
globalThis.customElements = { get: (k) => classes.get(k), define: (k, v) => classes.set(k, v), whenDefined: () => Promise.resolve() };
const created = [];
globalThis.document = { createElement: (name) => {
  const node = { name, style: {}, setConfig(config) { this.config = config; } };
  created.push(node);
  return node;
} };
await import(pathToFileURL(process.argv[2]).href); await Promise.resolve();
const Guard = classes.get("energylib-flow");
const guard = new Guard();
guard.setConfig({ load_entity: "sensor.load", warning: "negative", unavailable: "missing", card: { type: "custom:power-flow-card-plus" } });
const hass = { states: { "sensor.load": { state: "-0.045" } } };
guard.hass = hass;
assert.equal(guard.firstChild.name, "ha-card");
assert.equal(guard.firstChild.textContent, "negative");
assert.equal(guard.flow.hass, undefined);
assert.equal(hass.states["sensor.load"].state, "-0.045");
for (const raw of ["0", "-0.0", "0.5"]) {
  hass.states["sensor.load"].state = raw; guard.hass = hass;
  assert.equal(guard.firstChild, guard.flow);
  assert.equal(guard.flow.hass, hass);
}
for (const raw of ["unknown", "unavailable", "", "NaN"]) {
  hass.states["sensor.load"].state = raw; guard.hass = hass;
  assert.equal(guard.firstChild, guard.notice);
  assert.equal(guard.notice.textContent, "missing");
}
hass.states["sensor.load"].state = "-1"; guard.hass = hass;
assert.equal(guard.notice.textContent, "negative");
hass.states["sensor.load"].state = "1"; guard.hass = hass;
assert.equal(guard.firstChild, guard.flow);
assert.equal(created.length, 2);
process.stdout.write(JSON.stringify({ result: "passed" }));
