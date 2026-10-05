import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { IDL } from '@icp-sdk/core/candid';
import { Principal } from '@icp-sdk/core/principal';
import { encodeCanonical, decodeCompatible } from '../dist/candid.js';
import { canonicalize } from '../dist/index.js';

function agree(types, values) {
  const expected = canonicalize(IDL.encode(types, values));
  const actual = encodeCanonical(types, values);
  assert.deepEqual(actual, expected);
  assert.deepEqual(canonicalize(actual), actual);
  return actual;
}

test('direct encoding works while ordinary serialization and canonicalize are unavailable', () => {
  const result = spawnSync(process.execPath, [new URL('./direct-guard.mjs', import.meta.url).pathname], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /direct-path guard passed/);
});

test('direct scalar kinds, boundaries, NaNs, signed zero and exact UTF-8', () => {
  const scalarCases = [
    [IDL.Null, null], [IDL.Reserved, { ignored: true }], [IDL.Bool, false], [IDL.Bool, true],
    [IDL.Nat, (1n << 2000n) + 1n], [IDL.Int, -(1n << 2000n) - 1n],
    [IDL.Nat8, 255], [IDL.Nat16, 65535], [IDL.Nat32, 4294967295], [IDL.Nat64, (1n << 64n) - 1n],
    [IDL.Int8, -128], [IDL.Int16, -32768], [IDL.Int32, -2147483648], [IDL.Int64, -(1n << 63n)],
    [IDL.Text, '\uFEFF\0é𝄞\uFFFD'], [IDL.Principal, Principal.fromUint8Array(new Uint8Array(29).fill(1))],
  ];
  for (const type of [IDL.Float32, IDL.Float64])
    for (const value of [NaN, -0, 0, Infinity, -Infinity, 1.401298464324817e-45, 5e-324]) scalarCases.push([type, value]);
  agree(scalarCases.map(([type]) => type), scalarCases.map(([, value]) => value));
  assert.notDeepEqual(encodeCanonical([IDL.Null], [null]), encodeCanonical([IDL.Reserved], [null]));
  assert.notDeepEqual(encodeCanonical([IDL.Float64], [0]), encodeCanonical([IDL.Float64], [-0]));
});

test('native Text preserves Unicode scalar values and rejects unpaired UTF-16 surrogates', () => {
  const values = ['\uD800\uDC00', '\uDBFF\uDFFF', '\uFFFD', 'a\uD800\uDC00b', '\uFEFF\0é𝄞'];
  const schema = IDL.Record({ texts: IDL.Vec(IDL.Text), nested: IDL.Opt(IDL.Variant({ text: IDL.Text })) });
  const nested = { texts: values, nested: [{ text: '\uDBFF\uDFFF\uFFFD' }] };
  for (const text of values) {
    const bytes = agree([IDL.Text], [text]);
    assert.deepEqual(decodeCompatible([IDL.Text], bytes), [text]);
  }
  const bytes = agree([schema], [nested]);
  assert.deepEqual(decodeCompatible([schema], bytes), [nested]);
  assert.equal(Buffer.from(encodeCanonical([IDL.Text], ['\uFFFD'])).toString('hex'), '4449444c00017103efbfbd');
  for (const text of ['\uD800', '\uDBFF', '\uDC00', '\uDFFF', '\uD800x', '\uD800\uD800', 'x\uDC00', '\uDC00\uDFFF']) {
    assert.throws(() => encodeCanonical([IDL.Text], [text]), /unpaired UTF-16 surrogate/);
    assert.throws(() => encodeCanonical([schema], [{ texts: ['valid'], nested: [{ text }] }]), /unpaired UTF-16 surrogate/);
  }
  for (const [first, second] of [['valid', '\uD800'], ['\uDC00', 'valid']]) {
    let reads = 0;
    const value = { get text() { return ++reads === 1 ? first : second; } };
    assert.throws(() => encodeCanonical([IDL.Record({ text: IDL.Text })], [value]), /unpaired UTF-16 surrogate/);
  }
});

test('native Text counts a boundary surrogate pair as four UTF-8 bytes', () => {
  const text = 'x'.repeat(1_999_996) + '\uDBFF\uDFFF';
  const bytes = encodeCanonical([IDL.Text], [text]);
  assert.deepEqual(canonicalize(bytes), bytes);
  assert.deepEqual(decodeCompatible([IDL.Text], bytes), [text]);
  assert.throws(() => encodeCanonical([IDL.Text], [text + 'x']), /text byte length limit/);
  assert.throws(() => encodeCanonical([IDL.Text], ['x'.repeat(1_999_996) + '\uD800']), /unpaired UTF-16 surrogate/);
});

test('direct binary and empty nonbinary vector kinds and integer typed arrays', () => {
  for (const type of [IDL.Vec(IDL.Nat), IDL.Vec(IDL.Text), IDL.Vec(IDL.Empty), IDL.Vec(IDL.Func([], [], []))])
    assert.deepEqual(encodeCanonical([type], [[]]), encodeCanonical([IDL.Vec(IDL.Empty)], [[]]));
  const binary = IDL.Vec(IDL.Nat8);
  assert.notDeepEqual(encodeCanonical([binary], [[]]), encodeCanonical([IDL.Vec(IDL.Nat)], [[]]));
  for (const value of [[], [0, 128, 255], Uint8Array.of(0, 128, 255), Buffer.from([0, 128, 255])]) agree([binary], [value]);
  for (const [type, value] of [
    [IDL.Nat8, Uint8Array.of(0, 255)], [IDL.Int8, Int8Array.of(-128, 127)],
    [IDL.Nat16, Uint16Array.of(0, 65535)], [IDL.Int16, Int16Array.of(-32768, 32767)],
    [IDL.Nat32, Uint32Array.of(0, 4294967295)], [IDL.Int32, Int32Array.of(-2147483648, 2147483647)],
    [IDL.Nat64, BigUint64Array.of(0n, (1n << 64n) - 1n)], [IDL.Int64, BigInt64Array.of(-(1n << 63n), (1n << 63n) - 1n)],
  ]) agree([IDL.Vec(type)], [value]);
});

test('native integer vectors reject float, DataView, exotic and spoofed storage witnesses', () => {
  const integerKinds = [
    [IDL.Nat8, Uint8Array], [IDL.Int8, Int8Array], [IDL.Nat16, Uint16Array], [IDL.Int16, Int16Array],
    [IDL.Nat32, Uint32Array], [IDL.Int32, Int32Array], [IDL.Nat64, BigUint64Array], [IDL.Int64, BigInt64Array],
  ];
  for (const [type, Class] of integerKinds) {
    const element = Class.BYTES_PER_ELEMENT === 8 ? 1n : 1;
    const value = new Class([element, element, element]).subarray(1);
    agree([IDL.Vec(type)], [value]);
  }
  agree([IDL.Vec(IDL.Nat8)], [Uint8ClampedArray.of(0, 128, 255)]);
  agree([IDL.Vec(IDL.Int8)], [Uint8ClampedArray.of(0, 127)]);
  agree([IDL.Vec(IDL.Nat16)], [Int16Array.of(0, 32767)]);
  agree([IDL.Vec(IDL.Int16)], [Uint16Array.of(0, 32767)]);
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Nat8)], [Int8Array.of(-1)]), /fixed integer range/);
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Int8)], [Uint8ClampedArray.of(128)]), /fixed integer range/);
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Nat16)], [Int16Array.of(-1)]), /fixed integer range/);
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Int16)], [Uint16Array.of(32768)]), /fixed integer range/);
  for (const [type, value] of [[IDL.Nat32, Float32Array.of(1)], [IDL.Nat64, Float64Array.of(1)]])
    assert.throws(() => encodeCanonical([IDL.Vec(type)], [value]), /integer typed array/);
  const disguisedFloat = Float32Array.of(1);
  Object.setPrototypeOf(disguisedFloat, Uint32Array.prototype);
  Object.defineProperty(disguisedFloat, Symbol.toStringTag, { value: 'Uint32Array' });
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Nat32)], [disguisedFloat]), /integer typed array/);
  const dataView = new DataView(new ArrayBuffer(4));
  Object.defineProperties(dataView, { BYTES_PER_ELEMENT: { value: 4 }, length: { value: 1 } });
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Nat32)], [dataView]), /integer typed array/);
  for (const value of [{ 0: 1, length: 1, BYTES_PER_ELEMENT: 4 }, new Proxy(Uint32Array.of(1), {})])
    assert.throws(() => encodeCanonical([IDL.Vec(IDL.Nat32)], [value]), /integer typed array/);
  for (const [type, Class, key, value] of [
    [IDL.Nat32, Uint32Array, 'BYTES_PER_ELEMENT', 1],
    [IDL.Nat8, Uint8Array, 'length', 3],
    [IDL.Nat8, Uint8Array, 'byteLength', 3],
    [IDL.Nat8, Uint8Array, 'byteOffset', 1],
    [IDL.Nat8, Uint8Array, 'buffer', new ArrayBuffer(2)],
  ]) {
    const view = Class.of(1, 2);
    Object.defineProperty(view, key, { value });
    assert.throws(() => encodeCanonical([IDL.Vec(type)], [view]), /spoofed native vector metadata/);
  }
  const wrongWidth = Uint8Array.of(1);
  Object.defineProperty(wrongWidth, 'BYTES_PER_ELEMENT', { value: 4 });
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Nat32)], [wrongWidth]), /matching integer typed-array width/);
  const disguisedSigned = Int8Array.of(-1);
  Object.setPrototypeOf(disguisedSigned, Uint8Array.prototype);
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Nat8)], [disguisedSigned]), /fixed integer range/);
});

test('direct joined records, selected variants, nested options and tuple field IDs', () => {
  const Choice = IDL.Variant({ a: IDL.Null, b: IDL.Nat, z: IDL.Empty });
  const Entry = IDL.Record({ choice: Choice, nested: IDL.Opt(IDL.Opt(IDL.Nat)), note: IDL.Opt(IDL.Text), n: IDL.Null, r: IDL.Reserved });
  const values = [
    { choice: { b: 128n }, nested: [], note: [], n: null, r: null },
    { choice: { a: null }, nested: [[]], note: [''], n: null, r: 'ignored' },
    { choice: { b: 7n }, nested: [[1n]], note: [], n: null, r: undefined },
  ];
  const bytes = agree([IDL.Vec(Entry)], [values]);
  assert.deepEqual(decodeCompatible([IDL.Vec(Entry)], bytes)[0].map(x => x.nested), [[], [[]], [[1n]]]);
  agree([IDL.Tuple(IDL.Opt(IDL.Text), IDL.Nat, IDL.Opt(IDL.Nat))], [[[], 7n, []]]);
  assert.deepEqual(encodeCanonical([IDL.Record({ _01_: IDL.Text })], [{ _01_: 'same' }]),
    encodeCanonical([IDL.Record({ _0x1_: IDL.Text })], [{ _0x1_: 'same' }]));
  assert.throws(() => encodeCanonical([IDL.Record({ _1_: IDL.Text, _01_: IDL.Text })], [{ _1_: 'a', _01_: 'b' }]), /duplicate numeric field ID/);
});

test('direct traversal and nesting limits include binary bytes and their child depth', () => {
  const bytes = encodeCanonical([IDL.Vec(IDL.Nat8)], [new Uint8Array(1_999_999)]);
  assert.deepEqual(canonicalize(bytes), bytes);
  assert.throws(() => encodeCanonical([IDL.Vec(IDL.Nat8)], [new Uint8Array(2_000_000)]), /value traversal limit/);
  let type = IDL.Vec(IDL.Nat8), empty = new Uint8Array(), nonempty = Uint8Array.of(1);
  for (let i = 0; i < 256; i++) { type = IDL.Opt(type); empty = [empty]; nonempty = [nonempty]; }
  const emptyBytes = encodeCanonical([type], [empty]);
  assert.deepEqual(canonicalize(emptyBytes), emptyBytes);
  assert.throws(() => encodeCanonical([type], [nonempty]), /value traversal limit/);
});

test('direct reachable output table accepts 1024 entries and rejects 1025', () => {
  const names = Array.from({ length: 11 }, (_, i) => `field${i}`);
  const type = IDL.Record(Object.fromEntries(names.map(name => [name, IDL.Opt(IDL.Nat)])));
  const values = Array.from({ length: 1024 }, (_, mask) => Object.fromEntries(
    names.map((name, bit) => [name, mask & (1 << bit) ? [1n] : []])));
  const bytes = encodeCanonical(Array(1023).fill(type), values.slice(0, 1023));
  assert.deepEqual(canonicalize(bytes), bytes);
  assert.throws(() => encodeCanonical(Array(1024).fill(type), values), /output type-table limit/);
});

test('direct arbitrary integers at the 4096-byte boundary and the total output limit', () => {
  const natLimit = 1n << (4096n * 7n), intLimit = natLimit >> 1n;
  for (const [type, value] of [[IDL.Nat, natLimit - 1n], [IDL.Int, intLimit - 1n], [IDL.Int, -intLimit]]) {
    const bytes = encodeCanonical([type], [value]);
    assert.deepEqual(canonicalize(bytes), bytes);
  }
  assert.throws(() => encodeCanonical(Array(17).fill(IDL.Text), Array(17).fill('x'.repeat(2_000_000))), /output byte limit/);
});

test('direct rejection covers uninhabited, invalid, reference and oversized values', () => {
  for (const [type, value] of [[IDL.Empty, null], [IDL.Unknown, null], [IDL.Nat, -1n], [IDL.Int8, 128],
    [IDL.Bool, 1], [IDL.Null, undefined], [IDL.Opt(IDL.Nat), [1n, 2n]], [IDL.Vec(IDL.Nat8), [-1]],
    [IDL.Variant({ x: IDL.Null }), { unknown: null }], [IDL.Record({ x: IDL.Nat }), {}],
    [IDL.Func([], [], []), [Principal.anonymous(), 'f']], [IDL.Service({}), Principal.anonymous()]])
    assert.throws(() => encodeCanonical([type], [value]));
  assert.throws(() => encodeCanonical([IDL.Nat], []), /argument count/);
  assert.throws(() => encodeCanonical([IDL.Rec()], [[]]), /uninitialized/);
  assert.throws(() => encodeCanonical([IDL.Nat], [1n << (4096n * 7n)]), /length limit/);
  assert.throws(() => encodeCanonical([IDL.Int], [1n << (4096n * 7n - 1n)]), /length limit/);
  assert.throws(() => encodeCanonical([IDL.Principal], [Principal.fromUint8Array(new Uint8Array(30))]), /principal length/);
  assert.throws(() => encodeCanonical([IDL.Text], ['x'.repeat(2_000_001)]), /text byte length limit/);
});

test('direct encoding rejects option shapes that change between its two visits', () => {
  for (const [first, second] of [[[1n], []], [[], [1n]]]) {
    let reads = 0;
    const value = { get a() { return ++reads === 1 ? first : second; } };
    assert.throws(() => encodeCanonical([IDL.Record({ a: IDL.Opt(IDL.Nat) })], [value]), /shape changed/);
  }
  let reads = 0;
  const vector = { get a() { return ++reads === 1 ? Uint8Array.of(1) : new Uint8Array(2_000_000); } };
  assert.throws(() => encodeCanonical([IDL.Record({ a: IDL.Vec(IDL.Nat8) })], [vector]), /emitted value traversal limit/);
  for (const child of [IDL.Nat, IDL.Nat8]) {
    const elements = [1n, 2n];
    let reads = 0;
    Object.defineProperty(elements, 0, { get() { if (++reads === 2) elements.length = 1; return 1n; } });
    assert.throws(() => encodeCanonical([IDL.Vec(child)], [elements]), /invalid integer value/);
  }
});

test('direct principal serialization validates and captures bytes once per typed visit', () => {
  let reads = 0;
  const value = { _isPrincipal: true, toUint8Array() { reads++; return Uint8Array.of(1, 2); } };
  const bytes = encodeCanonical([IDL.Principal], [value]);
  assert.equal(reads, 2);
  assert.deepEqual(canonicalize(bytes), bytes);
});

test('principal byte spans use intrinsic storage length and offset', () => {
  const oversized = new Uint8Array(30).fill(1);
  Object.defineProperty(oversized, 'length', { value: 29 });
  assert.throws(() => encodeCanonical([IDL.Principal], [Principal.fromUint8Array(oversized)]), /principal length exceeds 29/);
  const storage = Uint8Array.of(7, 1, 2, 9), view = storage.subarray(1, 3);
  Object.defineProperties(view, { length: { value: 3 }, byteOffset: { value: 0 }, byteLength: { value: 3 } });
  const principal = Principal.fromUint8Array(view);
  const bytes = encodeCanonical([IDL.Principal], [principal]);
  assert.deepEqual(bytes, encodeCanonical([IDL.Principal], [Principal.fromUint8Array(Uint8Array.of(1, 2))]));
  assert.deepEqual(canonicalize(bytes), bytes);
});
