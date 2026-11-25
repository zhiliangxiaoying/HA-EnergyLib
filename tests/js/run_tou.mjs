// The schedule card: the plan from the sensor, the early checks, and that the whole
// edited plan is written once on "Save to device" through energylib.set_tou_schedule
// (the backend still checks and reads back).
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

class Root {
  constructor() { this.html = ""; }
  set innerHTML(html) { this.html = html; }
  get innerHTML() { return this.html; }
  getElementById() { return null; }
  addEventListener() {}
}
globalThis.HTMLElement = class { attachShadow() { return this.shadowRoot = new Root(); } };
globalThis.window = globalThis;
const classes = new Map();
globalThis.customElements = { get: (key) => classes.get(key), define: (key, value) => classes.set(key, value), whenDefined: () => Promise.resolve() };
globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
await import(pathToFileURL(process.argv[2]).href); await Promise.resolve();
const Card = classes.get("energylib-tou");

// The remote target power is disabled (no state): the export limit gives the rated power.
const config = { device_id: "dev", entity: "sensor.tou", max_power_entities: ["number.remote_power", "number.export_limit"] };
const calls = [];
const hass = {
  language: "zh-Hans",
  states: {
    "sensor.tou": { state: "inactive", attributes: {
      periods: [{ type: "charge", start: "02:00", end: "05:00", power: 3 }],
      weekdays: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], outside_mode: "self_consumption",
      valid_from: "2026-01-01T00:00:00+00:00",
    } },
    "number.export_limit": { state: "2", attributes: { max: 5 } },
  },
  localize: (key) => ({ self_consumption: "自发自用", backup: "电池备电" })[key.split(".").pop()] || "",
  formatEntityState: () => "未启用",
  async callService(domain, service, data) { calls.push([domain, service, data]); },
};

// Early checks.
const plan = Card.plan(hass, config);
const p = (type, start, end, power) => ({ type, start, end, power });
assert.equal(Card.problem(plan, p("discharge", "18:00", "21:00", 2), -1, 5), null);
assert.equal(Card.problem(plan, p("charge", "23:00", "01:00", 2), -1, 5), "errTime"); // across midnight
assert.equal(Card.problem(plan, p("charge", "06:00", "06:00", 2), -1, 5), "errTime");
assert.equal(Card.problem(plan, p("charge", "06:00", "07:00", 6), -1, 5), "errPower");
assert.equal(Card.problem(plan, p("discharge", "04:00", "06:00", 2), -1, 5), "errOverlap");
assert.equal(Card.problem(plan, p("charge", "01:00", "06:00", 2), 0, 5), null); // editing itself

const card = new Card(); card.setConfig(config); card.hass = hass;
let html = card.shadowRoot.innerHTML;
const click = (data) => card.onClick({ composedPath: () => [{ dataset: data }] });
// Not in use (another EMS mode): only the title line, a tap opens the plan.
assert.match(html, /class="folded"/);
assert.match(html, /aria-expanded="false"/);
assert.doesNotMatch(html, /02:00–05:00/);
click({ action: "fold" });
html = card.shadowRoot.innerHTML;
assert.match(html, /aria-expanded="true"/);
assert.match(html, /02:00–05:00/);
assert.match(html, /\+ 添加时段/);
assert.match(html, /<option value="self_consumption" selected>自发自用/);
assert.match(html, /class="actions" hidden/); // nothing changed: no Save button

// Power above the rated 5 kW is refused on the page.
assert.equal(card.maxPower, 5);
click({ action: "add" });
Object.assign(card.form, { type: "charge", start: "06:00", end: "07:00", power: "999" });
click({ action: "ok" });
assert.match(card.shadowRoot.innerHTML, /功率要在 0～5 kW 之间/);
click({ action: "close" });

// Add a period: a mistake is explained and nothing is written.
click({ action: "add" });
Object.assign(card.form, { type: "discharge", start: "04:00", end: "06:00", power: "2" });
click({ action: "ok" });
assert.match(card.shadowRoot.innerHTML, /和已有时段重叠/);
assert.equal(calls.length, 0);
// Corrected, then more changes: still a draft, nothing written.
click({ action: "type", type: "discharge" });
Object.assign(card.form, { start: "18:00", end: "21:00", power: "2" });
click({ action: "ok" });
click({ day: "sat" }); click({ day: "sun" });
card.onInput({ type: "change", composedPath: () => [{ dataset: { outside: "" }, value: "backup" }] });
click({ action: "delete", index: "0" });
assert.equal(calls.length, 0);
html = card.shadowRoot.innerHTML;
assert.match(html, /18:00–21:00/);
assert.doesNotMatch(html, /02:00–05:00/);
assert.match(html, /保存到设备/);
assert.match(html, /撤销修改/);

// Undo: back to the device's plan.
click({ action: "cancel" });
assert.match(card.shadowRoot.innerHTML, /02:00–05:00/);
assert.match(card.shadowRoot.innerHTML, /class="actions" hidden/);

// Edit again and save: one write with the whole plan; the backend keeps the
// device's validity dates.
click({ action: "add" });
Object.assign(card.form, { type: "discharge", start: "18:00", end: "21:00", power: "2" });
click({ action: "ok" });
click({ day: "sat" }); click({ day: "sun" });
click({ action: "save" });
await card.queue;
assert.deepEqual(calls, [["energylib", "set_tou_schedule", {
  device_id: "dev",
  periods: [{ type: "charge", start: "02:00", end: "05:00", power: 3 }, { type: "discharge", start: "18:00", end: "21:00", power: 2 }],
  weekdays: ["mon", "tue", "wed", "thu", "fri"],
  outside_mode: "self_consumption",
}]]);
// The saved plan shows until the sensor reads it back.
assert.match(card.shadowRoot.innerHTML, /18:00–21:00/);
assert.match(card.shadowRoot.innerHTML, /已写入设备/);
assert.match(card.shadowRoot.innerHTML, /class="actions" hidden/);
hass.states["sensor.tou"] = { state: "inactive", attributes: { ...hass.states["sensor.tou"].attributes, periods: calls[0][2].periods, weekdays: calls[0][2].weekdays } };
card.hass = { ...hass };
assert.equal(card.saved, null);

// The device may normalise a value (2.55 kW kept as 2.6): the next report from
// the sensor replaces the saved plan, equal or not.
click({ action: "add" });
Object.assign(card.form, { type: "charge", start: "22:00", end: "23:00", power: "2.55" });
click({ action: "ok" });
click({ action: "save" });
await card.queue;
assert.match(card.shadowRoot.innerHTML, /2\.5|2\.6/);
const normalised = calls.at(-1)[2].periods.map((p) => (p.start === "22:00" ? { ...p, power: 2.6 } : p));
hass.states["sensor.tou"] = { state: "inactive", attributes: { ...hass.states["sensor.tou"].attributes, periods: normalised } };
card.hass = { ...hass, states: { ...hass.states } };
assert.equal(card.saved, null);
assert.match(card.shadowRoot.innerHTML, /2\.6 kW/);

// A failed save keeps the draft (to correct and save again), with the reason.
calls.length = 0;
hass.callService = async (domain, service, data) => { calls.push([domain, service, data]); throw new Error("Stop remote control before editing TOU"); };
card.hass = { ...hass, states: { ...hass.states } };
click({ action: "delete", index: "0" });
click({ action: "save" });
await card.queue;
assert.equal(calls.length, 1);
assert.equal(calls[0][2].periods.length, 2); // three periods, one deleted
html = card.shadowRoot.innerHTML;
assert.doesNotMatch(html, /02:00–05:00/);
assert.match(html, /msg err[^>]*>Stop remote control/);
assert.match(html, /保存到设备/);

// Manual control running: nothing can be changed, and the card says why.
hass.states["sensor.tou"] = { ...hass.states["sensor.tou"], state: "remote" };
card.draft = null; card.message = ""; card.hass = { ...hass, states: { ...hass.states } };
html = card.shadowRoot.innerHTML;
assert.match(html, /手动控制进行中/);
assert.match(html, /class="add" data-action="add" disabled/);

// A model whose periods have no power and no weekdays (Shenggerui, 3 per kind).
const plain = new Card(); plain.setConfig(config);
const plainHass = { ...hass, states: { ...hass.states, "sensor.tou": { state: "outside_period", attributes: {
  periods: [{ type: "charge", start: "01:00", end: "05:00", power: 0 }],
  weekdays: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
  power_supported: false, weekdays_supported: false, max_per_type: 3, cross_midnight: true,
} } } };
plain.hass = plainHass;
html = plain.shadowRoot.innerHTML;
assert.doesNotMatch(html, /kW/);
assert.doesNotMatch(html, /class="days"/);
const plainClick = (data) => plain.onClick({ composedPath: () => [{ dataset: data }] });
plainClick({ action: "add" });
assert.doesNotMatch(plain.shadowRoot.innerHTML, /data-field="power"/);
Object.assign(plain.form, { type: "charge", start: "06:00", end: "07:00" });
plainClick({ action: "ok" });
assert.equal(plain.formError, null);
assert.equal(plain.draft.periods[1].power, 0);
for (const [start, end] of [["08:00", "09:00"], ["10:00", "11:00"]]) {
  plainClick({ action: "add" }); Object.assign(plain.form, { type: "charge", start, end }); plainClick({ action: "ok" });
}
assert.equal(plain.formError, "errCount"); // a fourth charge period
assert.equal(Card.problem(plain.shown, { type: "discharge", start: "22:00", end: "06:00", power: 0 }, -1, NaN, 3, true), "errOverlap");
assert.equal(Card.problem({ periods: [] }, { type: "discharge", start: "22:00", end: "06:00", power: 0 }, -1, NaN, 3, true), null);
assert.equal(Card.problem({ periods: [] }, { type: "discharge", start: "22:00", end: "06:00", power: 0 }, -1, NaN, 3), "errTime");
assert.match(plain.shadowRoot.innerHTML, /各最多 3 段/);
// This model runs a period across midnight; it must not overlap one in the morning.
plainClick({ action: "close" });

process.stdout.write(JSON.stringify({ result: "passed" }));
