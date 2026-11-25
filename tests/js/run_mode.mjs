// The EMS mode card: which modes it offers; a mode is written on tap. The
// battery/grid settings are HA's own tiles (not part of this card).
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

class Root {
  constructor() { this.listeners = {}; this.html = ""; }
  set innerHTML(html) { this.html = html; }
  get innerHTML() { return this.html; }
  getElementById() { return null; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
}
globalThis.HTMLElement = class { attachShadow() { return this.shadowRoot = new Root(); } };
globalThis.window = globalThis;
const classes = new Map();
globalThis.customElements = { get: (key) => classes.get(key), define: (key, value) => classes.set(key, value), whenDefined: () => Promise.resolve() };
globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
globalThis.fetch = undefined;
await import(pathToFileURL(process.argv[2]).href); await Promise.resolve();
const Card = classes.get("energylib-mode");

const NAMES = { self_consumption: "自发自用", time_of_use: "定时充放电", backup: "电池备电", pv_charge_priority: "光伏优先", feed_in_priority: "卖电优先", peak_shaving: "削峰填谷", remote: "手动控制" };
const config = { device_id: "dev", mode_entity: "sensor.mode", select_entity: "select.ems" };
const calls = [];
const hass = {
  language: "zh-Hans",
  states: {
    "sensor.mode": { state: "self_consumption", attributes: {} },
    // The device offers five of the six modes.
    "select.ems": { state: "自发自用", attributes: { options: ["自发自用", "定时充放电", "电池备电", "光伏优先", "卖电优先"] } },
  },
  // The selector's own words come from its translation; the sensor says "...模式".
  localize: (key) => (key.startsWith("component.energylib.entity.select.home_mode.state.") ? NAMES[key.split(".").pop()] ?? "" : ""),
  formatEntityState: (state, value) => `${NAMES[value ?? state.state] ?? value}模式`,
  async callService(domain, service, data) { calls.push([domain, service, data]); },
};

assert.deepEqual(Card.modes(hass, config).map((m) => m.id), ["self_consumption", "time_of_use", "backup", "pv_charge_priority", "feed_in_priority"]);

const card = new Card(); card.setConfig(config); card.hass = hass;
let html = card.shadowRoot.innerHTML;
assert.match(html, /data-mode="backup"/);
assert.doesNotMatch(html, /data-mode="peak_shaving"/);
assert.doesNotMatch(html, /number\.|switch\.|type="range"/); // settings are tiles, not here
assert.doesNotMatch(html, /data-action="apply"/); // no Apply button: a tap writes

const click = (data) => card.onClick({ composedPath: () => [{ dataset: data }] });
// A mode: written at once.
click({ mode: "backup" });
assert.match(card.shadowRoot.innerHTML, /一直把电池充满/); // the picked mode's line
await card.queue;
assert.deepEqual(calls, [["energylib", "set_ems_mode", { device_id: "dev", mode: "backup" }]]);
assert.equal(card.picked, null);
assert.match(card.shadowRoot.innerHTML, /已写入设备/);
hass.states["sensor.mode"] = { state: "backup", attributes: {} };

// The mode the device is already in: nothing is written.
calls.length = 0;
click({ mode: "backup" });
await card.queue;
assert.equal(calls.length, 0);

// A failed write shows the device's mode again, with the reason.
hass.callService = async (domain, service, data) => { calls.push([domain, service, data]); throw new Error("读回不一致"); };
click({ mode: "self_consumption" });
await card.queue;
assert.equal(calls.length, 1);
assert.match(card.shadowRoot.innerHTML, /msg err[^>]*>读回不一致/);
assert.match(card.shadowRoot.innerHTML, /class="mode picked" data-mode="backup"/);

// Another profile (a cloud connection) may offer a mode with no standard key:
// it is shown with the plain icon, no line, and written by its word.
calls.length = 0;
hass.callService = async (domain, service, data) => { calls.push([domain, service, data]); };
hass.states["select.ems"] = { state: "自发自用", attributes: { options: ["自发自用", "AI 智能"] } };
card.hass = { ...hass };
assert.deepEqual(Card.modes(hass, config).map((m) => m.id), ["self_consumption", "option:AI 智能"]);
html = card.shadowRoot.innerHTML;
assert.match(html, /data-mode="option:AI 智能"[\s\S]*?mdi:cog-outline[\s\S]*?AI 智能/);
assert.doesNotMatch(html, /data-mode="backup"/); // only what the device offers
click({ mode: "option:AI 智能" });
assert.equal(card.shadowRoot.innerHTML.match(/id="desc">([^<]*)</)[1], "");
await card.queue;
assert.deepEqual(calls, [["select", "select_option", { entity_id: "select.ems", option: "AI 智能" }]]);
// The device is in the cloud mode now: marked by the selector's word.
hass.states["sensor.mode"] = { state: "other", attributes: {} };
hass.states["select.ems"] = { state: "AI 智能", attributes: { options: ["自发自用", "AI 智能"] } };
assert.equal(Card.currentId(hass, config, Card.modes(hass, config)), "option:AI 智能");

// A mode set by another function locks the choice.
hass.states["select.ems"] = { state: "自发自用", attributes: { options: ["自发自用", "定时充放电", "电池备电", "光伏优先", "卖电优先"] } };
hass.states["sensor.mode"] = { state: "remote", attributes: {} };
card.hass = { ...hass };
html = card.shadowRoot.innerHTML;
assert.match(html, /当前由“手动控制”控制/);
assert.match(html, /data-mode="backup"\s+disabled/);

// A device with controls but no EMS mode yet: the card keeps its place, pending.
const pending = new Card(); pending.setConfig({ device_id: "dev", pending: true }); pending.hass = hass;
html = pending.shadowRoot.innerHTML;
assert.match(html, /运行模式[\s\S]*?class="now">待定</);
assert.match(html, /这款逆变器的模式切换还在增加中/);
assert.doesNotMatch(html, /data-mode=/);

process.stdout.write(JSON.stringify({ result: "passed" }));
