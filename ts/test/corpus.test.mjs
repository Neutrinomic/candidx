import { readFileSync } from 'node:fs';
import { strictEqual, deepStrictEqual, throws, notDeepStrictEqual } from 'node:assert';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { IDL } from '@icp-sdk/core/candid';
import { canonicalize } from '../dist/index.js';
import { encodeCanonical } from '../dist/candid.js';
const corpus = JSON.parse(readFileSync(new URL('../../tests/vectors.json', import.meta.url)));
const bytes = hex => Uint8Array.from(Buffer.from(hex, 'hex'));

test('shared typed corpus, external digest, and idempotence', () => {
  for (const entry of corpus.vectors) {
    const expected = bytes(entry.canonical);
    for (const raw of Object.values(entry.raw)) deepStrictEqual(canonicalize(bytes(raw)), expected, entry.id);
    deepStrictEqual(canonicalize(expected), expected, entry.id);
    strictEqual(createHash('sha256').update(expected).digest('hex'), entry.application_sha256);
  }
});
test('positive edges and malformed/unsupported input', () => {
  for (const entry of corpus.positive_edges) deepStrictEqual(canonicalize(bytes(entry.raw)), bytes(entry.canonical), entry.id);
  for (const entry of corpus.negative_cases) throws(() => canonicalize(bytes(entry.raw)), undefined, entry.id);
});
test('normalization never mutates Buffer inputs or their underlying storage', () => {
  for (const [raw, expected] of [
    ['4449444c00017d8000', '4449444c00017d00'],
    ['4449444c00017cffff7f', '4449444c00017c7f'],
    ['4449444c00017cc000', '4449444c00017cc000'],
  ]) {
    const storage = Buffer.from('aa' + raw + 'bb', 'hex');
    const before = Buffer.from(storage);
    const view = storage.subarray(1, storage.length - 1);
    deepStrictEqual(canonicalize(view), bytes(expected));
    deepStrictEqual(storage, before);
  }
});
test('record growth and verifying before projection', () => {
  const oldType = IDL.Record({ a: IDL.Text });
  const newType = IDL.Record({ a: IDL.Text, b: IDL.Opt(IDL.Text) });
  const old = encodeCanonical([oldType], [{ a: 'abc' }]);
  deepStrictEqual(encodeCanonical([newType], [{ a: 'abc', b: [] }]), old);
  deepStrictEqual(IDL.decode([newType], old), [{ a: 'abc', b: [] }]);
  const present = encodeCanonical([newType], [{ a: 'abc', b: ['new data'] }]);
  notDeepStrictEqual(present, old);
  deepStrictEqual(encodeCanonical([oldType], IDL.decode([oldType], present)), old);
});
