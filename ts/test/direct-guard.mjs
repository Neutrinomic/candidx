import { register } from 'node:module';
import assert from 'node:assert/strict';

register(new URL('./direct-guard-loader.mjs', import.meta.url));
const { IDL } = await import('@icp-sdk/core/candid');
const { encodeCanonical } = await import('../dist/candid.js');
const { canonicalize } = await import('../dist/index.js');
const unavailable = () => { throw new Error('SDK serialization method is unavailable'); };
for (const value of Object.values(IDL)) if (typeof value === 'function' && value.prototype) {
  for (const key of ['encodeValue', 'encodeType', 'buildTypeTable', '_buildTypeTableImpl']) {
    if (key in value.prototype) value.prototype[key] = unavailable;
  }
}
assert.throws(() => IDL.encode([IDL.Nat], [1n]), /ordinary IDL.encode is unavailable/);
assert.throws(() => canonicalize(new Uint8Array()), /canonicalize is unavailable/);
const hex = bytes => Buffer.from(bytes).toString('hex');
const old = IDL.Record({ a: IDL.Text });
const extension = IDL.Record({ a: IDL.Text, b: IDL.Opt(IDL.Text) });
assert.equal(hex(encodeCanonical([old], [{ a: 'abc' }])), '4449444c016c016171010003616263');
assert.equal(hex(encodeCanonical([extension], [{ a: 'abc', b: [] }])), '4449444c016c016171010003616263');
assert.equal(hex(encodeCanonical([IDL.Opt(IDL.Opt(IDL.Nat))], [[[]]])), '4449444c016e7f010001');
assert.equal(hex(encodeCanonical([IDL.Vec(IDL.Nat8)], [new Uint8Array()])), '4449444c016d7b010000');
const rec = IDL.Rec(); rec.fill(IDL.Opt(IDL.Record({ a: IDL.Text, next: rec })));
assert.equal(hex(encodeCanonical([rec], [[{ a: 'abc', next: [] }]])), '4449444c026e016c01617101000103616263');
assert.equal(hex(encodeCanonical([], [])), '4449444c0000');
console.log('direct-path guard passed: IDL.encode, encodeValue, encodeType, buildTypeTable, and canonicalize unavailable');
