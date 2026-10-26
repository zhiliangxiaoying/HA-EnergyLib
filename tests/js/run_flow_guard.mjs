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
guard.setConfig({ load_entity: "sensor.load", warning: "negative {value}", unavailable: "missing", card: { type: "custom:power-flow-card-plus" } });
const hass = { states: { "sensor.load": { state: "-0.045" } } };
guard.hass = hass;
// Below the tolerance: the card stays, the home is fed as 0 W, one quiet hint line.
assert.equal(guard.firstChild, guard.flow);
assert.equal(guard.flow.hass.states["sensor.load"].state, "0");
assert.equal(guard.shown, "flow+hint");
assert.equal(guard.hint.textContent, "negative -45 W");
assert.equal(hass.states["sensor.load"].state, "-0.045"); // raw state untouched
for (const raw of ["0", "-0.0", "0.5"]) {
  hass.states["sensor.load"].state = raw; guard.hass = hass;
  assert.equal(guard.firstChild, guard.flow);
  assert.equal(guard.shown, "flow");
  assert.equal(guard.flow.hass, hass);
}
// Meter noise (-20 W) is just 0 W: no hint.
hass.states["sensor.load"].state = "-0.02"; guard.hass = hass;
assert.equal(guard.shown, "flow");
assert.equal(guard.flow.hass.states["sensor.load"].state, "0");
for (const raw of ["unknown", "unavailable", "", "NaN"]) {
  hass.states["sensor.load"].state = raw; guard.hass = hass;
  assert.equal(guard.firstChild, guard.notice);
  assert.equal(guard.notice.textContent, "missing");
}
hass.states["sensor.load"].state = "-1"; guard.hass = hass;
assert.equal(guard.hint.textContent, "negative -1000 W");
hass.states["sensor.load"].state = "1"; guard.hass = hass;
assert.equal(guard.firstChild, guard.flow);
assert.equal(created.length, 3);

// A few watts of mismatch must not animate a PV-to-home flow the device does not report.
const balanced = new Guard();
created.length = 0;
balanced.setConfig({
  load_entity: "sensor.load", solar_entity: "sensor.pv", battery_entity: "sensor.bat", grid_entity: "sensor.grid",
  warning: "negative", unavailable: "missing", card: { type: "custom:power-flow-card-plus" },
});
const kw = (state) => ({ state, attributes: { unit_of_measurement: "kW" } });
const live = { states: { "sensor.load": kw("0.0"), "sensor.pv": kw("1.58"), "sensor.bat": kw("-1.57"), "sensor.grid": kw("-0.0") } };
balanced.hass = live;
const fed = balanced.flow.hass;
assert.notEqual(fed, live);
assert.ok(Math.abs(Number(fed.states["sensor.pv"].state) - 1.57) < 1e-9); // 10 W absorbed
assert.equal(live.states["sensor.pv"].state, "1.58"); // raw state untouched
assert.equal(fed.states["sensor.bat"], live.states["sensor.bat"]);
// A real flow above the tolerance is passed through unchanged.
live.states["sensor.load"] = kw("0.5"); live.states["sensor.pv"] = kw("2.2"); live.states["sensor.bat"] = kw("-1.7");
balanced.hass = live;
assert.equal(balanced.flow.hass, live);
live.states["sensor.load"] = kw("0"); live.states["sensor.pv"] = kw("1.65"); live.states["sensor.bat"] = kw("-1.57");
balanced.hass = live; // 80 W unexplained: shown as the device reports it
assert.equal(balanced.flow.hass, live);
// Night: no PV, 12 W of grid import against a 0 W load is not a grid-to-home flow.
live.states["sensor.pv"] = kw("0"); live.states["sensor.bat"] = kw("0"); live.states["sensor.grid"] = kw("0.012");
balanced.hass = live;
assert.ok(Math.abs(Number(balanced.flow.hass.states["sensor.grid"].state)) < 1e-9);
assert.equal(balanced.flow.hass.states["sensor.pv"], live.states["sensor.pv"]);
// Hardware case: PV 869 W, battery charging 865 W, 22 W of "export", load -22 W.
live.states["sensor.pv"] = kw("0.869"); live.states["sensor.bat"] = kw("-0.865");
live.states["sensor.grid"] = kw("-0.022"); live.states["sensor.load"] = kw("-0.022");
balanced.hass = live;
const hw = balanced.flow.hass.states;
assert.equal(Number(hw["sensor.grid"].state), 0); // export noise is not a battery-to-grid flow
assert.ok(Math.abs(Number(hw["sensor.pv"].state) - 0.865) < 1e-9); // PV exactly into the battery
assert.equal(hw["sensor.load"].state, "0");
assert.equal(balanced.shown, "flow"); // -22 W is within the tolerance: no hint
live.states["sensor.load"] = kw("0"); live.states["sensor.grid"] = kw("0");
live.states["sensor.pv"] = kw("unavailable");
balanced.hass = live;
assert.equal(balanced.flow.hass, live);
process.stdout.write(JSON.stringify({ result: "passed" }));
