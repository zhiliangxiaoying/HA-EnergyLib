import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// The shipped clamp: a negative home load never drives a reversed flow.
const source = readFileSync(process.argv[2], 'utf8');
const line = source.split(/\r?\n/).find(l => l.includes('const displayLoad ='));
assert.ok(line, 'displayLoad missing');
const context = vm.createContext({});
vm.runInContext(line.replace('const ', 'globalThis.'), context);
const f = context.displayLoad;
assert.equal(f(-45), 0);
assert.equal(f(0), 0);
assert.equal(f(1200), 1200);
assert.equal(f(null), null);
// Run the shipped HA refresh logic with DOM/3D route doubles. This checks the
// visible readings and both animation paths together, without a real inverter.
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    style: {}, attributes: {}, classes: {}, textContent: '', hidden: false,
    setAttribute(name, value) { this.attributes[name] = value; },
    classList: {toggle(name, value) { element(id).classes[name] = value; }},
  });
  return elements.get(id);
};
const routes = ['solar', 'grid', 'home', 'battery'].map(id => ({
  id, length: 10, mesh: {material: {}}, glow: {material: {}}, pulse: {},
}));
const keys = ['solar_power', 'grid_power', 'battery_power', 'load_power', 'battery_total_soc', 'running_state'];
const device = {id: 'fixture', name: 'Test inverter', entities: Object.fromEntries(keys.map(k => [k, 'sensor.renamed_' + k]))};
const states = Object.fromEntries(keys.map(key => ['sensor.renamed_' + key, {
  state: key === 'running_state' ? 'on_grid' : key === 'battery_total_soc' ? '50' : '0',
  attributes: {unit_of_measurement: key === 'battery_total_soc' ? '%' : 'kW'},
  last_updated: '2026-10-22T12:00:00Z',
}]));
let parent = {states};
const binding = vm.createContext({
  hass: null, device: null, devices: [], KEYS: keys, flowRoutes: routes,
  $: element, findHass: () => parent, findDevices: () => [device],
});
vm.runInContext(
  source.split(/\r?\n/).find(l => l.includes('const fmt =')) +
  '\nfunction syncDeviceSelect() { device = devices[0] || null; }\n' +
  source.slice(source.indexOf('  function stateOf('), source.indexOf('  // ---------- Panel controls')),
  binding,
);
function set(values) {
  for (const [key, value] of Object.entries(values)) states[device.entities[key]].state = String(value);
  vm.runInContext('refresh();', binding);
}
function assertPaused() {
  for (const route of routes) {
    assert.equal(route.active, false);
    assert.equal(route.pulse.visible, false);
    assert.equal(element('svg-' + route.id).attributes['marker-end'], 'none');
    assert.equal(element('svg-' + route.id).classes.inactive, true);
  }
}
// Beyond the 30 W tolerance: the home is drawn as 0 W, flows keep running and one
// hint names the raw reading; the sensor state is untouched.
set({load_power: -0.045, grid_power: -0.045});
assert.equal(element('metric-load').textContent, '0 W');
assert.equal(element('energy-badge').textContent, '5/5 项可用');
assert.equal(element('energy-notice').hidden, false);
assert.match(element('energy-notice').textContent, /-45 W/);
assert.equal(routes.find(r => r.id === 'home').active, false);
assert.equal(routes.find(r => r.id === 'grid').active, true);
assert.equal(states[device.entities.load_power].state, '-0.045');
// Meter noise within the tolerance is simply 0 W, without a hint.
set({load_power: -0.02, grid_power: 0});
assert.equal(element('energy-notice').hidden, true);
assert.equal(element('metric-load').textContent, '0 W');
assertPaused(); // all four powers are within ±20 W: nothing to animate
set({solar_power: 2, grid_power: -1, battery_power: 0, load_power: 1});
assert.equal(element('energy-notice').hidden, true);
assert.equal(element('metric-load').textContent, '1.00 kW');
assert.equal(routes.find(r => r.id === 'grid').direction, -1);
assert.equal(routes.find(r => r.id === 'battery').active, false);
set({solar_power: 0, grid_power: 2, battery_power: -1, load_power: 1});
assert.equal(routes.find(r => r.id === 'grid').direction, 1);
assert.equal(routes.find(r => r.id === 'battery').direction, -1);
assert.equal(element('metric-battery-note').textContent, '充电');
set({grid_power: 0, battery_power: 1});
assert.equal(routes.find(r => r.id === 'battery').direction, 1);
assert.equal(element('metric-battery-note').textContent, '放电');
set({solar_power: 'unavailable'});
assertPaused();
assert.equal(element('metric-solar').textContent, '—');
assert.equal(element('energy-badge').textContent, '数据不完整');
set({solar_power: 0, grid_power: 0, battery_power: 0, load_power: 0});
assert.equal(element('energy-notice').hidden, true);
assertPaused(); // valid zero power has no animated branch
set({battery_total_soc: 'unknown', battery_power: 1, load_power: 1});
assert.equal(element('energy-notice').hidden, true); // SOC is not required for power flows
assert.equal(routes.find(r => r.id === 'battery').active, true);
parent = null;
vm.runInContext('refresh();', binding);
assertPaused();
assert.match(element('energy-notice').textContent, /Home Assistant/);
assert.equal(element('metric-load').textContent, '—');
console.log(JSON.stringify({result: 'passed'}));
