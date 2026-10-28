import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Exercise the shipped loading callbacks with controlled frame/network completion order.
const source = readFileSync(process.argv[2], 'utf8');
const loading = source.slice(source.indexOf('  const flowRoutes ='), source.indexOf('  // ---------- Scene'));
const models = source.slice(source.indexOf('  function loadModels()'), source.indexOf('  // ---------- Grid pole'));
const base = ['home-interior.glb', 'home-energy-system.glb', 'solar-ev-system.glb', 'garden-site.glb'];

function harness() {
  const requests = new Map(), frames = [], timers = [], events = [];
  const elements = {'twin-loading': {hidden: false}, 'twin-loading-message': {}, 'twin-loading-retry': {hidden: true}};
  class Vector3 {}
  class Box3 {
    constructor() { this.min = {y: 0}; }
    setFromObject() { return this; }
    getSize() { return {x: 2, y: 1, z: 4}; }
    getCenter() { return {x: 0, y: 0, z: 0}; }
  }
  const context = vm.createContext({
    THREE: {Vector3, Box3, GLTFLoader: class {
      setMeshoptDecoder() {}
      load(url, success, progress, failure) {
        const name = url.split('/').pop().split('?')[0];
        assert.ok(!requests.has(name), `Duplicate request for ${name}`);
        events.push(`request:${name}`); requests.set(name, {success, failure});
      }
    }},
    window: {MeshoptDecoder: {}}, VERSION: 'test', t: key => key,
    ENERGY_HUB: {x: 0, y: 0, z: 0}, CAR: {x: 0, y: 0, z: 0, length: 4.49},
    scene: {add(root) { events.push(`add:${root.name}`); }},
    controls: {update() {}}, renderFrame() { events.push('render'); },
    shade() {}, placeBackboard() {},
    document: {body: {classList: {remove(name) { events.push(`reveal:${name}`); }}}},
    $: id => elements[id],
    requestAnimationFrame: fn => frames.push(fn), setTimeout: fn => timers.push(fn),
    console: {error() {}},
  });
  vm.runInContext(loading + models + '\nloadModels();', context);
  function succeed(name) {
    requests.get(name).success({scene: {
      name: '', getObjectByName() { return null; },
      position: {set() {}}, rotation: {}, scale: {setScalar() {}},
    }});
  }
  function frame() { frames.splice(0).forEach(fn => fn()); }
  return {requests, frames, timers, elements, events, context, succeed, frame};
}

// All base requests start together; even the last base model must finish before reveal.
const h = harness();
assert.deepEqual([...h.requests.keys()], base);
for (const name of [...base].reverse().slice(0, 3)) h.succeed(name);
assert.equal(h.frames.length, 0);
assert.equal(h.elements['twin-loading'].hidden, false);
h.succeed(base[0]);
assert.equal(h.frames.length, 1);
assert.equal(h.requests.has('sports-car.glb'), false);
h.frame();
assert.equal(h.elements['twin-loading'].hidden, true);
assert.equal(h.requests.has('sports-car.glb'), false);
assert.deepEqual(h.events.slice(-2), ['render', 'reveal:twin-booting']);
h.frame();
assert.equal(h.requests.has('sports-car.glb'), false);
h.timers.splice(0).forEach(fn => fn());
assert.equal(h.requests.has('sports-car.glb'), true);
h.succeed('sports-car.glb');
assert.equal(h.events.at(-1), 'add:Sports_Car');
assert.equal(h.frames.length, 0); // Car completion cannot trigger another reveal/load.

// Failure of the optional car cannot restore the blocking loading/error screen.
const failedCar = harness();
base.forEach(failedCar.succeed);
failedCar.frame(); failedCar.frame(); failedCar.timers.shift()();
failedCar.requests.get('sports-car.glb').failure(new Error('network unavailable'));
assert.equal(failedCar.elements['twin-loading'].hidden, true);
assert.equal(failedCar.elements['twin-loading-retry'].hidden, true);
assert.equal(vm.runInContext('loadFailed', failedCar.context), false);

// Required-model failures retain the retry UI and never schedule the car.
const failedBase = harness();
failedBase.requests.get(base[0]).failure(new Error('invalid model'));
base.slice(1).forEach(failedBase.succeed);
assert.equal(failedBase.elements['twin-loading'].hidden, false);
assert.equal(failedBase.elements['twin-loading-retry'].hidden, false);
assert.equal(failedBase.frames.length, 0);
assert.equal(failedBase.requests.has('sports-car.glb'), false);
console.log(JSON.stringify({result: 'passed', scenarios: 3}));
