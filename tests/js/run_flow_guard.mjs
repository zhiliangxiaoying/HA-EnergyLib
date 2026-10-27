// Built-in flow card model: tolerance, negative load, missing grid/battery.
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

globalThis.HTMLElement = class {};
globalThis.window = globalThis;
globalThis.customElements = { get: () => undefined, define: () => {}, whenDefined: () => new Promise(() => {}) };
await import(pathToFileURL(process.argv[2]).href);
const { flowModel, balanceWatts, flowSplit } = window.energyLibDashboard;

const config = {
  solar_entity: "sensor.pv", battery_entity: "sensor.bat", grid_entity: "sensor.grid", load_entity: "sensor.load",
  soc_entity: "sensor.soc", running_entity: "sensor.run", alarm_entity: "sensor.alarm",
};
const kw = (state) => ({ state, attributes: { unit_of_measurement: "kW" } });
const hass = (pv, bat, grid, load, extra = {}) => ({
  states: {
    "sensor.pv": kw(pv), "sensor.bat": kw(bat), "sensor.grid": kw(grid), "sensor.load": kw(load),
    "sensor.soc": { state: "52" }, "sensor.run": { state: "on_grid" }, "sensor.alarm": { state: "normal", attributes: {} },
    ...extra,
  },
});
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);

// 10 W of mismatch must not draw a PV-to-home flow the device does not report.
let m = flowModel(hass("1.58", "-1.57", "-0.0", "0.0"), config);
assert.equal(m.ready, true);
near(m.watts.solar, 1570);
assert.deepEqual(m.flows, { solar: "in", grid: null, battery: "out", home: null });
assert.equal(m.soc, 52);
// A real flow above the tolerance is kept as reported.
m = flowModel(hass("2.2", "-1.7", "0", "0.5"), config);
assert.deepEqual(m.watts, { solar: 2200, battery: -1700, grid: 0, load: 500 });
assert.deepEqual(m.flows, { solar: "in", grid: null, battery: "out", home: "out" });
// 80 W unexplained: shown as the device reports it.
near(flowModel(hass("1.65", "-1.57", "0", "0"), config).watts.solar, 1650);
// Night: 12 W of grid import against a 0 W load is noise, not a grid flow.
m = flowModel(hass("0", "0", "0.012", "0"), config);
assert.equal(m.watts.grid, 0);
assert.equal(m.flows.grid, null);
// Hardware case: PV 869 W, battery charging 865 W, 22 W "export", load -22 W.
m = flowModel(hass("0.869", "-0.865", "-0.022", "-0.022"), config);
assert.equal(m.watts.grid, 0);
near(m.watts.solar, 865);
assert.equal(m.watts.load, 0);
assert.equal(m.negativeLoad, null); // within the tolerance: no hint
// A larger negative load is drawn as 0 W and named in the hint.
m = flowModel(hass("0", "0", "0", "-0.045"), config);
assert.equal(m.watts.load, 0);
near(m.negativeLoad, -45);
// Import flows towards the inverter, export away from it.
assert.equal(flowModel(hass("0", "0", "1.2", "1.2"), config).flows.grid, "in");
assert.equal(flowModel(hass("2", "0", "-1.5", "0.5"), config).flows.grid, "out");
assert.equal(flowModel(hass("0", "0.8", "0", "0.8"), config).flows.battery, "in");
// Any missing power value: no drawing, the card shows a notice instead.
for (const raw of ["unknown", "unavailable", "", "NaN"]) {
  assert.equal(flowModel(hass(raw, "0", "0", "0"), config).ready, false);
}
// Off-grid running state: grid missing (red cross), no grid flow.
m = flowModel(hass("1", "-0.4", "0.01", "0.6", { "sensor.run": { state: "off_grid" } }), config);
assert.equal(m.missing.grid, true);
assert.equal(m.offGrid, true);
assert.equal(m.watts.grid, 0);
assert.equal(m.flows.grid, null);
// Alarm-derived link flags from the integration.
const alarm = (attributes) => ({ "sensor.alarm": { state: "abnormal", attributes } });
m = flowModel(hass("0.5", "0", "0", "0.5", alarm({ battery_disconnected: true, grid_disconnected: false })), config);
assert.deepEqual(m.missing, { grid: false, battery: true });
assert.equal(m.flows.battery, null);
m = flowModel(hass("0", "0", "0", "0", alarm({ grid_disconnected: true })), config);
assert.equal(m.missing.grid, true);
assert.equal(m.offGrid, false);
// Unknown link state (incomplete alarm sources) is not shown as missing.
m = flowModel(hass("0", "0", "0", "0", alarm({ grid_disconnected: null, battery_disconnected: null })), config);
assert.deepEqual(m.missing, { grid: false, battery: false });
// The balancing helper alone.
assert.deepEqual(balanceWatts({ solar: 1000, battery: 0, grid: -20, load: 1000 }), { solar: 1000, battery: 0, grid: 0, load: 1000 });
// Node-to-node split: PV serves the home first, then the battery, then export.
const none = { solarHome: 0, solarBattery: 0, solarGrid: 0, batteryHome: 0, gridHome: 0, batteryGrid: 0, gridBattery: 0 };
assert.deepEqual(flowSplit({ solar: 2620, battery: -1640, grid: 0, load: 980 }), { ...none, solarHome: 980, solarBattery: 1640 });
assert.deepEqual(flowSplit({ solar: 0, battery: 800, grid: 300, load: 1100 }), { ...none, batteryHome: 800, gridHome: 300 });
assert.deepEqual(flowSplit({ solar: 3000, battery: 500, grid: -2500, load: 1000 }), { ...none, solarHome: 1000, solarGrid: 2000, batteryGrid: 500 });
assert.deepEqual(flowSplit({ solar: 0, battery: -2000, grid: 2500, load: 500 }), { ...none, gridHome: 500, gridBattery: 2000 });
// A residual under the tolerance is not drawn as a path.
assert.deepEqual(flowSplit({ solar: 1010, battery: 0, grid: 0, load: 1000 }), { ...none, solarHome: 1000 });
process.stdout.write(JSON.stringify({ result: "passed" }));
