import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

// Minimal DOM for exercising the actual card's rendering and update path.
class Element {
  constructor() { this.children = []; this.attributes = {}; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  toggleAttribute(key, enabled) {
    if (enabled) this.attributes[key] = "";
    else delete this.attributes[key];
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren() { this.children = []; }
}
const defined = new Map();
globalThis.HTMLElement = class {
  attachShadow() {
    const elements = new Map();
    this.shadowRoot = {
      innerHTML: "",
      querySelector: (key) => {
        if (!elements.has(key)) elements.set(key, new Element());
        return elements.get(key);
      },
    };
    return this.shadowRoot;
  }
};
globalThis.window = globalThis;
globalThis.document = { createElementNS: () => new Element() };
globalThis.customElements = {
  get: (name) => defined.get(name),
  define: (name, cls) => defined.set(name, cls),
  whenDefined: () => Promise.resolve(),
};
await import(pathToFileURL(process.argv[2]).href);
await Promise.resolve();
const { sankeyModel, buildView, translate } = window.energyLibDashboard;
const config = {
  solar_entity: "sensor.pv", battery_entity: "sensor.bat",
  grid_entity: "sensor.grid", load_entity: "sensor.load",
  running_entity: "sensor.run", alarm_entity: "sensor.alarm",
};
const state = (value) => ({ state: String(value), attributes: { unit_of_measurement: "kW" } });
const hass = (solar, battery, grid, load) => ({ language: "zh-Hans", states: {
  "sensor.pv": state(solar), "sensor.bat": state(battery),
  "sensor.grid": state(grid), "sensor.load": state(load),
} });
const values = (nodes) => Object.fromEntries(nodes.map((node) => [node.key, node.value]));
let model = sankeyModel(hass(3, -2, 0, 1), config);
assert.deepEqual(values(model.sources), { solar: 3000 });
assert.deepEqual(values(model.sinks), { load: 1000, charging: 2000 });
model = sankeyModel(hass(0, 1, 2, 3), config);
assert.deepEqual(values(model.sources), { discharging: 1000, importing: 2000 });
model = sankeyModel(hass(3, 0, -2, 1), config);
assert.deepEqual(values(model.sinks), { load: 1000, exporting: 2000 });
model = sankeyModel(hass(0, -2, 3, 1), config);
assert.deepEqual(values(model.sources), { importing: 3000 });
assert.deepEqual(values(model.sinks), { load: 1000, charging: 2000 });
for (const [solar, load, side] of [[2, 1, "sinks"], [1, 2, "sources"]]) {
  model = sankeyModel(hass(solar, 0, 0, load), config);
  assert.equal(values(model[side]).difference, 1000);
  assert.equal(model.sources.reduce((s, n) => s + n.value, 0), model.sinks.reduce((s, n) => s + n.value, 0));
}
for (const invalid of ["unknown", "unavailable", "", "NaN"]) {
  assert.equal(sankeyModel(hass(invalid, 0, 0, 0), config).ready, false);
}
assert.equal(sankeyModel(hass(0, 0, 0, -0.1), config).reason, "invalid");
assert.equal(sankeyModel(hass(-1, 0, 0, 0), config).reason, "invalid");
assert.equal(sankeyModel(hass(0, 0, 0, 0), config).total, 0);
const offline = hass(1, 0, 2, 1);
offline.states["sensor.run"] = { state: "off_grid" };
assert.equal(sankeyModel(offline, config).sources.some((n) => n.key === "importing"), false);

const Card = defined.get("energylib-sankey");
assert.ok(Card);
const card = new Card();
card.setConfig(config);
card.hass = hass(3, -2, 0, 1);
const svg = card.shadowRoot.querySelector("svg");
assert.match(svg.attributes["aria-label"], /电池充电/);
assert.equal(svg.children.filter((n) => n.attributes["fill-opacity"]).length, 3);
assert.ok(!JSON.stringify(svg.children).includes("NaN"));
card.hass = hass("unavailable", 0, 0, 0);
assert.equal(svg.children.length, 0, "failed reads must clear the previous ribbons");
assert.ok("hidden" in svg.attributes);
card.hass = { ...hass(0, 1, 2, 3), language: "en" };
assert.ok(!("hidden" in svg.attributes));
assert.match(svg.attributes["aria-label"], /Discharging/);

// Strategy uses this device's renamed entities. With power readings only (no
// counters, no controls) the live cards are spread over the columns: the power
// split heads the second one, above the 24 h power chart.
const registry = { ...hass(0, 0, 0, 0), entities: {}, devices: {} };
for (const [key, id] of Object.entries({ solar_power: "sensor.pv", battery_power: "sensor.bat", grid_power: "sensor.grid", load_power: "sensor.load" })) {
  registry.entities[id] = { platform: "energylib", device_id: "dev", entity_id: id, translation_key: key };
}
const view = buildView(registry, { id: "dev", name: "Inverter" }, translate(registry, "dashboard"), false);
const cards = view.sections[0].cards;
const index = cards.findIndex((c) => c.type === "custom:energylib-sankey");
assert.ok(view.sections[0].cards.some((c) => c.type === "custom:energylib-house-flow"));
// The live power split stays with "now", under the house and today's curve (11-23).
assert.ok(index > cards.findIndex((c) => c.type === "custom:energylib-day-curve"));
assert.equal(cards[index].solar_entity, "sensor.pv");
process.stdout.write(JSON.stringify({ result: "passed" }));
