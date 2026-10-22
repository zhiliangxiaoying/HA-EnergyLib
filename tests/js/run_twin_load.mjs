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
console.log(JSON.stringify({result: 'passed'}));
