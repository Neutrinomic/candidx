import { IDL } from '@icp-sdk/core/candid';
import { Principal } from '@icp-sdk/core/principal';
import { performance } from 'node:perf_hooks';
import { deepStrictEqual } from 'node:assert';
import { canonicalize } from '../dist/index.js';
import { encodeCanonical, decodeCompatible } from '../dist/candid.js';

const Tx = IDL.Record({ owner: IDL.Principal, amount: IDL.Nat, memo: IDL.Opt(IDL.Vec(IDL.Nat8)) });
const Block = IDL.Record({ timestamp: IDL.Nat64, parentHash: IDL.Opt(IDL.Vec(IDL.Nat8)), tx: IDL.Vec(Tx), note: IDL.Opt(IDL.Text) });
const specs = [['small', 4, 32], ['records-128', 128, 32], ['blob-64k', 1, 65536], ['records-1024', 1024, 0]];
let checksum = 0;
function sample(fn) {
  for (let i = 0; i < 3; i++) checksum ^= fn();
  let start = performance.now(); checksum ^= fn();
  const elapsed = Math.max(performance.now() - start, 0.01);
  const iterations = Math.max(2, Math.min(1000, Math.floor(25 / elapsed)));
  const times = [];
  for (let trial = 0; trial < 7; trial++) {
    if (global.gc) global.gc();
    start = performance.now();
    for (let i = 0; i < iterations; i++) checksum ^= fn();
    times.push((performance.now() - start) * 1000 / iterations);
  }
  times.sort((a, b) => a - b);
  return { median_us: times[3], min_us: times[0], max_us: times[6], iterations_per_trial: iterations, trials: 7 };
}
const result = [];
for (const [name, count, memoSize] of specs) {
  const memo = Uint8Array.from({ length: memoSize }, (_, i) => i % 256);
  const tx = Array.from({ length: count }, (_, i) => ({ owner: Principal.fromUint8Array(new Uint8Array([1, 2])), amount: 2n ** 200n + BigInt(i), memo: i % 3 === 0 || memoSize === 65536 ? [memo] : [] }));
  const block = { timestamp: 1234567890n, parentHash: [new Uint8Array(32).fill(7)], tx, note: [] };
  const raw = IDL.encode([Block], [block]), canonical = encodeCanonical([Block], [block]);
  // Decode once outside timing and require both outputs reconstruct the value.
  deepStrictEqual(IDL.decode([Block], raw), [block]);
  deepStrictEqual(IDL.decode([Block], canonical), [block]);
  result.push({ name, transactions: count, memo_bytes: memoSize, ordinary_bytes: raw.length, canonical_bytes: canonical.length, raw_hex: Buffer.from(raw).toString('hex'), canonical_hex: Buffer.from(canonical).toString('hex'),
    ordinary_encode: sample(() => IDL.encode([Block], [block]).byteLength),
    canonicalize_preencoded: sample(() => canonicalize(raw).byteLength),
    canonical_encode_direct: sample(() => encodeCanonical([Block], [block]).byteLength),
    canonical_encode_pipeline: sample(() => canonicalize(IDL.encode([Block], [block])).byteLength),
    ordinary_decode: sample(() => IDL.decode([Block], raw)[0].tx.length),
    canonical_decode: sample(() => IDL.decode([Block], canonical)[0].tx.length),
    compatible_decode: sample(() => decodeCompatible([Block], canonical)[0].tx.length) });
}
console.log(JSON.stringify({ language: 'TypeScript', checksum, cases: result }));
