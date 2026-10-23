// Exercise the shipped card's event handlers; HA protocol behavior is tested separately.
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

class Root {
  set innerHTML(html) {
    this.nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
      value: "", hidden: false, textContent: "", disabled: false,
      setAttribute(key, value) { this[key] = value; }, classList: { toggle() {} },
    }]));
  }
  getElementById(id) { return this.nodes.get(id); }
}
globalThis.HTMLElement = class { attachShadow() { return this.shadowRoot = new Root(); } };
globalThis.window = globalThis;
const classes = new Map();
globalThis.customElements = { get: (key) => classes.get(key), define: (key, value) => classes.set(key, value), whenDefined: () => Promise.resolve() };
// Timers are manually driven in this harness. No background work survives the test.
globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
await import(pathToFileURL(process.argv[2]).href); await Promise.resolve();
const Card = classes.get("energylib-dispatch");
const calls = [];
const config = { device_id: "test-device", power_entity: "number.power", duration_entity: "number.minutes", status_entity: "sensor.task", work_mode_entity: "select.mode", control_status_entity: "sensor.mode" };
const hass = {
  language: "zh-Hans",
  states: {
    "number.power": { state: "0", attributes: { max: 5 } },
    "number.minutes": { state: "5", attributes: {} },
    "sensor.task": { state: "idle", attributes: { remote_access_enabled: false } },
    "select.mode": { state: "self_consumption", attributes: {} },
    "sensor.mode": { state: "self_consumption", attributes: { control_access_enabled: true } },
  },
  async callService(domain, action, data) { calls.push({ domain, action, data }); },
};
const card = new Card(); card.setConfig(config); card.hass = hass;
assert.equal(card.el("operation").hidden, true);
assert.equal(card.el("access").textContent, "已关闭");
await card.start(); card.saveOnly(); await card.queue;
assert.equal(calls.length, 0);
await card.toggleAccess();
assert.deepEqual(calls, [{ domain: "energylib", action: "set_remote_access", data: { device_id: "test-device", enabled: true } }]);
assert.equal(card.el("operation").hidden, true); // wait for actual backend state
hass.states["sensor.task"].attributes.remote_access_enabled = true; card.hass = hass; calls.length = 0;
assert.equal(card.el("operation").hidden, false);
assert.equal(card.el("access").textContent, "已开启");
assert.equal(card.el("start").disabled, true);
assert.equal(calls.length, 0);
assert.equal(card.el("title").textContent, "电池强制充放电");
assert.equal(card.el("stop").hidden, true);
assert.equal(card.el("end").textContent, "预计结束时间: 暂无任务");
card.el("power").value = "2"; card.el("duration").value = "15";
hass.states["sensor.task"].attributes.entities_ready = false; card.hass = hass;
assert.equal(card.el("start").disabled, true);
assert.match(card.el("access-note").textContent, /正在启用操作实体/);
calls.length = 0; await card.start(); assert.equal(calls.length, 0);
hass.states["sensor.task"].attributes.entities_ready = true; card.hass = hass;
card.el("power").oninput(); card.saveOnly(); await card.queue;
assert.deepEqual(calls, [{ domain: "energylib", action: "set_remote_parameters", data: { device_id: "test-device", power_kw: 2, duration_minutes: 15 } }]);
assert.equal(card.el("start").textContent, "开始充电");
calls.length = 0; await card.start();
assert.deepEqual(calls.at(-1), { domain: "energylib", action: "set_battery_power", data: { device_id: "test-device", power_kw: -2, duration: 900 } });
card.el("discharge").onclick(); await card.start();
assert.equal(calls.at(-1).data.power_kw, 2);

for (const [power, minutes] of [["0", "5"], ["-1", "5"], ["6", "5"], ["2", "1.5"], ["2", "1441"]]) {
  calls.length = 0; card.el("power").value = power; card.el("duration").value = minutes;
  await card.start(); assert.equal(calls.length, 0); assert.equal(card.el("error").hidden, false);
}
card.el("power").value = "2"; card.el("duration").value = "5";
hass.states["sensor.task"] = { state: "charging", attributes: { remote_access_enabled: true, target_power_kw: -2, actual_power_kw: -1.8, end_time: new Date(Date.now() + 60000).toISOString() } };
card.hass = hass; assert.equal(card.el("stop").hidden, false);
assert.match(card.el("actual").textContent, /1\.80 kW/);
assert.match(card.el("remaining").textContent, /1:00|0:59/);
assert.match(card.el("end").textContent, /^预计结束时间: /);
assert.doesNotMatch(card.el("end").textContent, /暂无任务|待确认/);
const activeEnd = hass.states["sensor.task"].attributes.end_time;
hass.states["sensor.task"].state = "unconfirmed";
delete hass.states["sensor.task"].attributes.end_time; card.hass = hass;
assert.equal(card.el("end").textContent, "预计结束时间: 待确认");
hass.states["sensor.task"].state = "unavailable"; card.hass = hass;
assert.equal(card.el("end").textContent, "预计结束时间: 状态暂不可用");
hass.states["sensor.task"].state = "charging";
hass.states["sensor.task"].attributes.end_time = activeEnd; card.hass = hass;
calls.length = 0; await card.stop();
assert.deepEqual(calls, [{ domain: "energylib", action: "stop_battery_power", data: { device_id: "test-device" } }]);
hass.states["select.mode"].state = "time_of_use"; card.hass = hass;
assert.equal(card.el("start").disabled, true); assert.match(card.el("warning").textContent, /定时充放电/);
assert.equal(card.el("leave-tou").hidden, false);
calls.length = 0; await card.leaveTou();
assert.deepEqual(calls, [{ domain: "select", action: "select_option", data: { entity_id: "select.mode", option: "self_consumption" } }]);
hass.states["select.mode"].state = "self_consumption";

// A failure stays a visible error, never an optimistic success state.
hass.callService = async (domain) => { if (domain === "energylib") throw new Error("Readback failed"); };
hass.states["sensor.task"] = { state: "idle", attributes: { remote_access_enabled: true } }; card.hass = hass;
await card.start(); assert.equal(card.el("status").textContent, "无手动任务");
assert.equal(card.el("error").textContent, "Readback failed"); assert.equal(card.busy, false);
hass.language = "en"; card.hass = hass;
assert.equal(card.el("title").textContent, "Forced battery charge / discharge");
assert.equal(card.el("duration-label").textContent, "Duration");
assert.equal(card.el("end").textContent, "Estimated end time: No active task");
// The combined form works with all optional native inputs disabled/absent.
delete hass.states["number.power"]; delete hass.states["number.minutes"];
hass.states["sensor.task"].attributes.settings = { power_kw: 2, duration_minutes: 5, max_power_kw: 5 };
hass.callService = async (domain, action, data) => calls.push({ domain, action, data });
card.initialized = false; card.hass = hass;
assert.equal(card.el("power").value, 2); assert.equal(card.el("duration").value, 5);
assert.equal(card.el("start").disabled, false);
calls.length = 0; await card.start();
assert.deepEqual(calls.map((c) => c.action), ["set_remote_parameters", "set_battery_power"]);
// Failed disabling stays visible and does not hide controls optimistically.
hass.callService = async () => { throw new Error("Exit unconfirmed"); };
await card.toggleAccess();
assert.equal(card.el("access-error").textContent, "Exit unconfirmed");
assert.equal(card.el("operation").hidden, false);
hass.states["sensor.task"].attributes.remote_access_enabled = false; card.hass = hass;
assert.equal(card.el("operation").hidden, true);
card.connectedCallback(); card.disconnectedCallback(); assert.equal(card.timer, null);
delete hass.states["sensor.task"].attributes.remote_access_enabled; card.hass = hass;
assert.equal(card.el("operation").hidden, true); assert.equal(card.el("access").disabled, true);
// Disabling integration controls is distinct from lost communication. Even a
// stale remote permission must not show enabled controls or issue a start.
hass.states["sensor.mode"].attributes.control_access_enabled = false;
hass.states["sensor.task"].attributes.remote_access_enabled = true;
card.hass = hass;
assert.equal(card.el("operation").hidden, true);
assert.equal(card.el("access").disabled, true);
assert.match(card.el("access-note").textContent, /Control access is off/);
calls.length = 0; await card.toggleAccess(); await card.start();
assert.equal(calls.length, 0);
hass.language = "zh-Hans"; card.hass = hass;
assert.match(card.el("access-note").textContent, /尚未开启控制权限/);
hass.states["sensor.mode"].attributes.control_access_enabled = true;
hass.states["sensor.task"].state = "unavailable";
card.hass = hass;
assert.match(card.el("access-note").textContent, /检查连接/);
process.stdout.write(JSON.stringify({ result: "passed", checks: ["parameter edits never dispatch", "signed start snapshot and minutes conversion", "input boundaries", "stop and countdown", "TOU guard", "failure feedback", "Chinese and English", "timer cleanup"] }));
