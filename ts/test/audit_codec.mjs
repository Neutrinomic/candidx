// Independent, deterministic codec audit. Run after npm run build:
// node test/audit_codec.mjs --rust-probe ../rust/target/debug/examples/probe
// This file is deliberately outside the normal *.test.mjs suite: it reports
// SDK compatibility limitations separately from canonical-byte failures.
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { IDL } from '@icp-sdk/core/candid';
import { Principal } from '@icp-sdk/core/principal';
import { canonicalize as implementation } from '../dist/index.js';
import { encodeCanonical, decodeCompatible } from '../dist/candid.js';

const argv = process.argv.slice(2);
const option = name => { const i = argv.indexOf(name); return i < 0 ? undefined : argv[i + 1]; };
const iterations = Number(option('--iterations') ?? 2500);
let seed = 0xcad1d;
const next = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed; };
const pick = list => list[next() % list.length];
const hex = b => Buffer.from(b).toString('hex');
function canonicalize(input) {
  const before = hex(input);
  try { return implementation(input); }
  finally { assert.equal(hex(input), before, 'canonicalize mutated caller input'); }
}
const leb = (v, signed = false) => {
  v = BigInt(v); const out = [];
  for (;;) {
    const byte = Number(v & 127n); v >>= 7n;
    const done = signed ? (v === 0n && byte < 64) || (v === -1n && byte >= 64) : v === 0n;
    out.push(byte | (done ? 0 : 128)); if (done) return out;
  }
};
const wire = (type, payload) => Uint8Array.from([68, 73, 68, 76, 0, 1, ...leb(type, true), ...payload]);
const cases = [], counts = { typed: 0, schema_extensions: 0, recursive: 0, float_bits: 0, padded_integer: 0, resource_edges: 0, mutations: 0, accepted_mutations: 0, upstream_decoding_losses: 0, sdk_recursive_field_losses: 0 };
const limitations = [], failures = [];
const save = (id, raw, canonical) => cases.push({ id, raw: hex(raw), canonical: hex(canonical) });

// A tiny schema generator drives the existing upstream encoder and decoder.
// No canonical implementation is duplicated here.
const primitive = ['bool', 'nat', 'int', 'nat8', 'nat16', 'nat32', 'nat64', 'int8', 'int16', 'int32', 'int64', 'float32', 'float64', 'text', 'principal'];
const primitiveIDL = Object.fromEntries(primitive.map(k => [k, IDL[k[0].toUpperCase() + k.slice(1)]]));
function schema(depth) {
  if (!depth || next() % 4 === 0) return [pick(primitive)];
  const op = pick(['opt', 'vec', 'record', 'variant']);
  if (op === 'opt' || op === 'vec') return [op, schema(depth - 1)];
  return [op, ...Array.from({ length: 1 + next() % 3 }, (_, i) => [`_${i * 17 + 5}_`, schema(depth - 1)])];
}
function idl(s, extended = false) {
  const [op, child] = s;
  if (op === 'opt') return IDL.Opt(idl(child, extended));
  if (op === 'vec') return IDL.Vec(idl(child, extended));
  if (op === 'record' || op === 'variant') {
    const fields = Object.fromEntries(s.slice(1).map(([k, t]) => [k, idl(t, extended)]));
    if (extended) fields._4294967295_ = op === 'record'
      ? IDL.Opt(IDL.Service({ unused: IDL.Func([IDL.Text], [IDL.Nat], ['query']) }))
      : IDL.Func([IDL.Text], [IDL.Nat], ['query']);
    return op === 'record' ? IDL.Record(fields) : IDL.Variant(fields);
  }
  return primitiveIDL[op];
}
function value(s) {
  const [op, child] = s;
  if (op === 'opt') return next() % 3 === 0 ? [] : [value(child)];
  if (op === 'vec') return Array.from({ length: next() % 6 }, () => value(child));
  if (op === 'record') return Object.fromEntries(s.slice(1).map(([k, t]) => [k, value(t)]));
  if (op === 'variant') { const [k, t] = pick(s.slice(1)); return { [k]: value(t) }; }
  if (op === 'bool') return Boolean(next() & 1);
  if (op === 'nat') return BigInt(next()) << BigInt(next() % 301);
  if (op === 'int') return (BigInt(next()) << BigInt(next() % 301)) * (next() & 1 ? -1n : 1n);
  if (op === 'nat8') return next() & 255;
  if (op === 'nat16') return next() & 65535;
  if (op === 'nat32') return next();
  if (op === 'nat64') return BigInt(next()) << 32n | BigInt(next());
  if (op === 'int8') return (next() & 255) - 128;
  if (op === 'int16') return (next() & 65535) - 32768;
  if (op === 'int32') return next() | 0;
  if (op === 'int64') return BigInt.asIntN(64, BigInt(next()) << 32n | BigInt(next()));
  if (op === 'float32' || op === 'float64') return pick([-0, 0, NaN, Infinity, -Infinity, Number.MIN_VALUE, -Number.MIN_VALUE, 1.25, -123.75]);
  if (op === 'text') return pick(['', 'abc', '\0', '\uFEFFstart', '\uFFFF', 'e\u0301', 'é', '🧪', 'a\0z']);
  if (op === 'principal') return Principal.fromUint8Array(Uint8Array.from({ length: next() % 30 }, () => next() & 255));
  throw new Error(`unhandled ${op}`);
}
function extend(s, v) {
  const [op, child] = s;
  if (op === 'opt') return v.length ? [extend(child, v[0])] : [];
  if (op === 'vec') return v.map(x => extend(child, x));
  if (op === 'record') return { ...Object.fromEntries(s.slice(1).map(([k, t]) => [k, extend(t, v[k])])), _4294967295_: [] };
  if (op === 'variant') { const k = Object.keys(v)[0]; return { [k]: extend(s.slice(1).find(([label]) => label === k)[1], v[k]) }; }
  return v;
}
function checkTyped(id, t, v, recursive = false) {
  const raw = IDL.encode([t], [v]), canonical = canonicalize(raw);
  assert.deepEqual(encodeCanonical([t], [v]), canonical, `${id}: adapter`);
  assert.deepEqual(canonicalize(canonical), canonical, `${id}: idempotence`);
  const ordinaryDecoded = canonicalize(IDL.encode([t], IDL.decode([t], raw)));
  const decoded = canonicalize(IDL.encode([t], IDL.decode([t], canonical)));
  if (hex(ordinaryDecoded) !== hex(canonical)) counts.upstream_decoding_losses++;
  if (recursive && hex(decoded) !== hex(ordinaryDecoded)) counts.sdk_recursive_field_losses++;
  else assert.deepEqual(decoded, ordinaryDecoded, `${id}: canonicalization worsens original decoder recovery`);
  save(id, raw, canonical); counts.typed++;
  return canonical;
}
for (let i = 0; i < iterations; i++) {
  const s = next() & 1 ? ['vec', schema(5)] : schema(5), v = value(s);
  const old = checkTyped(`generated-${i}`, idl(s), v);
  const grown = checkTyped(`extended-${i}`, idl(s, true), extend(s, v));
  assert.deepEqual(grown, old, `generated-${i}: recursive unused schema extension`);
  counts.schema_extensions++;
}

// Actual recursive declarations, with differing finite depths in one vector.
const rec = IDL.Rec(), recExtended = IDL.Rec();
rec.fill(IDL.Opt(IDL.Record({ value: IDL.Nat, next: rec, children: IDL.Vec(rec) })));
recExtended.fill(IDL.Opt(IDL.Record({ value: IDL.Nat, next: recExtended, children: IDL.Vec(recExtended), absent: IDL.Opt(IDL.Text) })));
const tree = depth => !depth || next() % 3 === 0 ? [] : [{ value: BigInt(next()), next: tree(depth - 1), children: Array.from({ length: next() % 3 }, () => tree(depth - 1)) }];
const growTree = v => v.length ? [{ ...v[0], next: growTree(v[0].next), children: v[0].children.map(growTree), absent: [] }] : [];
for (let i = 0; i < 250; i++) {
  const vs = Array.from({ length: next() % 6 }, () => tree(6));
  const old = checkTyped(`recursive-${i}`, IDL.Vec(rec), vs, true);
  const grown = checkTyped(`recursive-extended-${i}`, IDL.Vec(recExtended), vs.map(growTree), true);
  assert.deepEqual(grown, old); counts.recursive++; counts.schema_extensions++;
}

// All float checks use integer bits, independently of JS floating arithmetic.
for (const [op, size, fraction, exponent, canonicalNaN] of [[-13, 4, 23n, 8n, 0x7fc00000n], [-14, 8, 52n, 11n, 0x7ff8000000000000n]]) {
  const fixed = [0n, 1n, (1n << BigInt(size * 8 - 1)), (1n << BigInt(size * 8 - 1)) | 1n, ((1n << exponent) - 1n) << fraction, (((1n << exponent) - 1n) << fraction) | 1n, (1n << BigInt(size * 8)) - 1n];
  const sign = 1n << BigInt(size * 8 - 1), exponentMask = ((1n << exponent) - 1n) << fraction;
  fixed.push(sign | exponentMask, exponentMask | (1n << (fraction - 1n)), sign | exponentMask | 1n, sign | exponentMask | (1n << (fraction - 1n)));
  const random = Array.from({ length: 1000 }, () => size === 4 ? BigInt(next()) : BigInt(next()) << 32n | BigInt(next()));
  for (let i = 0; i < 100; i++) {
    const payload = (BigInt(next()) << 32n | BigInt(next())) & ((1n << fraction) - 1n);
    random.push(exponentMask | (payload || 1n) | (i & 1 ? sign : 0n));
  }
  for (const bits of [...fixed, ...random]) {
    const bytes = n => Array.from({ length: size }, (_, i) => Number(n >> BigInt(i * 8) & 255n));
    const nan = (bits >> fraction & ((1n << exponent) - 1n)) === (1n << exponent) - 1n && (bits & ((1n << fraction) - 1n)) !== 0n;
    const raw = wire(op, bytes(bits)), expected = wire(op, bytes(nan ? canonicalNaN : bits));
    assert.deepEqual(canonicalize(raw), expected); save(`float-${size}-${bits.toString(16)}`, raw, expected); counts.float_bits++;
  }
}

// Both sides of the documented integer byte limit; values are tiny despite
// padding, so acceptance must depend on the encoded integer's byte count.
for (const op of [-3, -4]) {
  const fill = op === -3 ? 128 : 255, last = op === -3 ? 0 : 127;
  for (const length of [4096, 4097]) {
    const raw = wire(op, [...Array(length - 1).fill(fill), last]);
    const id = `integer-limit-${op}-${length}`;
    if (length === 4096) {
      const expected = wire(op, [last]); assert.deepEqual(canonicalize(raw), expected); save(id, raw, expected);
    } else {
      assert.throws(() => canonicalize(raw), /integer length limit/); cases.push({ id, raw: hex(raw), error: true });
    }
    counts.resource_edges++;
  }
}

// Padded unsigned/signed values, including long zero and sign extension.
for (const op of [-3, -4]) for (const n of [0n, 1n, 63n, 64n, 127n, 128n, 129n, (1n << 255n) - 1n, ...(op === -4 ? [-1n, -64n, -65n, -(1n << 255n)] : [])]) {
  for (const padding of [1, 9, 255, 2048]) {
    const minimal = leb(n, op === -4), padded = [...minimal]; padded[padded.length - 1] |= 128;
    padded.push(...Array(padding - 1).fill(n < 0 ? 255 : 128), n < 0 ? 127 : 0);
    const raw = wire(op, padded), expected = wire(op, minimal);
    assert.deepEqual(canonicalize(raw), expected); save(`padded-${op}-${n}-${padding}`, raw, expected); counts.padded_integer++;
  }
}

// Malformed-message mutation stress: returned bytes must themselves be stable.
const typedCases = cases.filter(c => c.id.startsWith('generated-'));
for (let i = 0; i < 5000; i++) {
  const base = Buffer.from(pick(typedCases).raw, 'hex'); let raw;
  switch (next() % 5) {
    case 0: raw = Buffer.from(base); raw[next() % raw.length] ^= 1 << (next() % 8); break;
    case 1: raw = base.subarray(0, next() % base.length); break;
    case 2: { const at = next() % base.length; raw = Buffer.concat([base.subarray(0, at), Buffer.from([next() & 255]), base.subarray(at)]); break; }
    case 3: { const at = next() % base.length; raw = Buffer.concat([base.subarray(0, at), base.subarray(at + 1)]); break; }
    default: raw = Buffer.concat([base, Buffer.from([next() & 255])]);
  }
  counts.mutations++;
  try {
    const canonical = canonicalize(raw);
    assert.deepEqual(canonicalize(canonical), canonical, `mutation-${i}: idempotence`);
    save(`mutation-${i}`, raw, canonical); counts.accepted_mutations++;
  } catch (error) {
    if (error?.name === 'AssertionError' || error instanceof RangeError) throw error;
    cases.push({ id: `mutation-${i}`, raw: hex(raw), error: true });
  }
}

// SDK recovery gaps are an upstream decoding concern. Keep concrete examples
// visible without weakening tests for the canonical representation.
for (const [id, t, v] of [
  ['required-null-record', IDL.Record({ a: IDL.Null }), { a: null }],
  ['required-null-record-under-option', IDL.Opt(IDL.Record({ a: IDL.Null })), [{ a: null }]],
  ['tuple-missing-leading-option', IDL.Tuple(IDL.Opt(IDL.Nat), IDL.Text), [[], 'ok']],
  ['tuple-missing-trailing-option', IDL.Tuple(IDL.Text, IDL.Opt(IDL.Nat)), ['ok', []]],
]) {
  const raw = IDL.encode([t], [v]), canonical = canonicalize(raw); save(id, raw, canonical);
  assert.deepEqual(decodeCompatible([t], canonical), [v], `${id}: compatible decoder`);
  try {
    const decoded = IDL.decode([t], canonical);
    if (hex(canonicalize(IDL.encode([t], decoded))) !== hex(canonical)) limitations.push({ id, canonical: hex(canonical), result: 'typed decoder silently loses present data' });
  } catch (error) { limitations.push({ id, canonical: hex(canonical), result: error.message }); }
}

// An unused signature with many arguments must not hit a VM argument limit.
const argc = 131072;
const signature = Buffer.concat([Buffer.from([68, 73, 68, 76, 1, 0x6a, ...leb(argc)]), Buffer.alloc(argc, 0x7f), Buffer.from([0, 0, 0])]);
const signatureCanonical = Uint8Array.from([68, 73, 68, 76, 0, 0]);
try { assert.deepEqual(canonicalize(signature), signatureCanonical); }
catch (error) { failures.push({ id: 'unused-wide-function', result: `${error.name}: ${error.message}` }); }
save('unused-wide-function', signature, signatureCanonical);

const rust = option('--rust-probe');
if (option('--export-cases')) writeFileSync(option('--export-cases'), JSON.stringify(cases) + '\n');
if (rust) {
  const out = spawnSync(rust, [], { input: cases.map(c => c.raw + '\n').join(''), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  assert.equal(out.status, 0, out.stderr);
  const lines = out.stdout.trimEnd().split('\n'); assert.equal(lines.length, cases.length);
  for (const [i, c] of cases.entries()) {
    if (c.error) assert.ok(lines[i].startsWith('error '), `${c.id}: Rust accepted TS-rejected input`);
    else assert.equal(lines[i], 'ok ' + c.canonical, `${c.id}: Rust/TS disagree`);
  }
}
const result = { seed: '0xcad1d', iterations, counts, rust_differential: Boolean(rust), cases: cases.length, limitations, canonical_failures: failures };
if (option('--report')) writeFileSync(option('--report'), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify(result, null, 2));
if (failures.length) process.exitCode = 1;
