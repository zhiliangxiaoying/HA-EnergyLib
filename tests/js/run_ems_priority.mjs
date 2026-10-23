import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

globalThis.HTMLElement = class {};
globalThis.window = globalThis;
class NativeCard { _renderEntities(entries) { this.received = entries; return entries; } }
const nativeRender = NativeCard.prototype._renderEntities;
const classes = new Map([["ha-device-entities-card", NativeCard]]);
globalThis.customElements = {
  get: (key) => classes.get(key), define: (key, value) => classes.set(key, value),
  whenDefined: () => Promise.resolve(),
};
const url = pathToFileURL(process.argv[2]).href;
await import(url); await Promise.resolve();
const card = new NativeCard();
const backup = { entity_id: "number.backup", entity_category: "config", platform: "energylib" };
const ems = { entity_id: "select.renamed", entity_category: "config", platform: "energylib", translation_key: "work_mode" };
const grid = { entity_id: "switch.grid", entity_category: "config", platform: "energylib" };
const original = [backup, grid, ems];
assert.equal(NativeCard.prototype._renderEntities, nativeRender);
assert.equal(card._renderEntities(original), original);
assert.deepEqual(original, [backup, grid, ems]); // do not mutate HA's shared order
const other = [backup, { ...ems, platform: "another_brand" }];
assert.equal(card._renderEntities(other), other);
for (const changed of [{ disabled_by: "user" }, { entity_category: "diagnostic" }, { entity_id: "sensor.mode" }]) {
  const list = [backup, { ...ems, ...changed }];
  assert.equal(card._renderEntities(list), list);
}
// Native display entries can use metadata from the HA frontend registry.
card.hass = { entities: { "select.alias": { platform: "energylib", translation_key: "work_mode" } } };
const renamed = { entity_id: "select.alias", entity_category: "config" };
const renamedList = [backup, renamed];
assert.equal(card._renderEntities(renamedList), renamedList);
assert.equal(card._renderEntities(undefined), undefined);
await import(url + "?again"); await Promise.resolve();
assert.equal(NativeCard.prototype._renderEntities, nativeRender);
class UnsupportedCard {}
classes.set("ha-device-entities-card", UnsupportedCard);
await import(url + "?unsupported"); await Promise.resolve();
assert.equal(UnsupportedCard.prototype._renderEntities, undefined);
process.stdout.write(JSON.stringify({ result: "passed" }));
