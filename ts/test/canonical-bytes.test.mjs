import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { IDL, PipeArrayBuffer } from '@icp-sdk/core/candid';
import { Actor } from '@icp-sdk/core/agent';
import { Principal } from '@icp-sdk/core/principal';
import { canonicalize } from '../dist/index.js';
import { CanonicalBytes, decoderType, decodeCompatible, encodeCanonical } from '../dist/candid.js';

const hex = bytes => Buffer.from(bytes).toString('hex');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const fromHex = text => Uint8Array.from(Buffer.from(text, 'hex'));
const concat = (...parts) => Uint8Array.from(parts.flatMap(part => Array.from(part)));

function checkValue(type, value, ordinary = true) {
  const expected = encodeCanonical([type], [value]);
  const inputs = ordinary ? [IDL.encode([type], [value]), expected] : [expected];
  for (const raw of inputs) {
    for (const actual of [decodeCompatible([CanonicalBytes], raw)[0],
      IDL.decode([decoderType(CanonicalBytes)], raw)[0]]) {
      assert(actual instanceof Uint8Array);
      assert.deepEqual(actual, expected);
      assert.equal(hash(actual), hash(expected));
      assert.deepEqual(canonicalize(actual), actual);
      const original = decodeCompatible([type], expected);
      assert.deepEqual(decodeCompatible([type], actual), original);
    }
  }
  return expected;
}

test('CanonicalBytes preserves new present fields before old-schema projection', () => {
  const old = IDL.Record({ amount: IDL.Nat });
  const operation = IDL.Variant({ A: IDL.Nat, B: IDL.Text, unused: IDL.Func([], [], []) });
  const grown = IDL.Record({ amount: IDL.Nat, memo: IDL.Opt(IDL.Text), operation: IDL.Opt(operation) });
  const values = [
    { amount: 7n, memo: ['\uFEFFnew field'], operation: [{ B: 'future case' }] },
    { amount: 8n, memo: [], operation: [] },
  ];
  const response = IDL.Record({ first_index: IDL.Nat, transactions: IDL.Vec(grown), future: IDL.Text });
  const client = IDL.Record({ first_index: IDL.Nat, transactions: IDL.Vec(CanonicalBytes) });
  const input = { first_index: 50n, transactions: values, future: 'outside the hash unit' };
  for (const raw of [IDL.encode([response], [input]), encodeCanonical([response], [input])]) {
    const [reply] = decodeCompatible([client], raw);
    assert.equal(reply.first_index, 50n);
    assert(!Object.hasOwn(reply, 'future'));
    const captures = reply.transactions;
    for (let i = 0; i < values.length; i++) {
      const expected = encodeCanonical([grown], [values[i]]);
      assert.deepEqual(captures[i], expected);
      assert.equal(hash(captures[i]), hash(expected));
      assert.deepEqual(decodeCompatible([grown], captures[i]), [values[i]]);
      const projected = decodeCompatible([old], captures[i]);
      assert.deepEqual(projected, [{ amount: values[i].amount }]);
      assert.equal(hash(encodeCanonical([old], projected)) === hash(expected), i === 1);
    }
  }
});

test('individual canonical transactions validate the phash chain with an older client schema', () => {
  const old = IDL.Record({ index: IDL.Nat, amount: IDL.Nat, phash: IDL.Opt(IDL.Vec(IDL.Nat8)) });
  const grown = IDL.Record({ index: IDL.Nat, amount: IDL.Nat, phash: IDL.Opt(IDL.Vec(IDL.Nat8)),
    memo: IDL.Opt(IDL.Text) });
  const digest = bytes => Uint8Array.from(createHash('sha256').update(bytes).digest());
  const transactions = [], encoded = [];
  for (let i = 0; i < 4; i++) {
    const transaction = { index: BigInt(i), amount: BigInt(i + 1),
      phash: i === 0 ? [] : [digest(encoded[i - 1])], memo: i === 1 ? ['new field'] : [] };
    transactions.push(transaction);
    encoded.push(encodeCanonical([grown], [transaction]));
  }
  const server = IDL.Record({ first_index: IDL.Nat, transactions: IDL.Vec(grown) });
  const client = IDL.Record({ first_index: IDL.Nat, transactions: IDL.Vec(CanonicalBytes) });
  for (const raw of [IDL.encode([server], [{ first_index: 0n, transactions }]),
    encodeCanonical([server], [{ first_index: 0n, transactions }])]) {
    const [reply] = decodeCompatible([client], raw);
    assert.deepEqual(reply.transactions, encoded);
    const projected = reply.transactions.map(bytes => decodeCompatible([old], bytes)[0]);
    assert.deepEqual(projected[0].phash, []);
    for (let i = 1; i < projected.length; i++)
      assert.deepEqual(projected[i].phash[0], digest(reply.transactions[i - 1]));
    // A newer present field changes the true hash, even though old decoding
    // discards it. Hashing that projected object would break this link.
    assert.notDeepEqual(projected[2].phash[0], digest(encodeCanonical([old], [projected[1]])));
    const tampered = encodeCanonical([grown], [{ ...transactions[1], memo: ['tampered'] }]);
    assert.deepEqual(decodeCompatible([old], tampered), [projected[1]]);
    assert.notDeepEqual(projected[2].phash[0], digest(tampered));
  }
});

test('captured bytes are independent of neighbours, batching, metadata and type numbering', () => {
  const choice = IDL.Variant({ _0_: IDL.Nat, _127_: IDL.Text, _4294967295_: IDL.Null });
  const transaction = IDL.Record({ x: IDL.Opt(IDL.Text), op: choice });
  const target = { x: [], op: { _4294967295_: null } };
  const neighbours = [{ x: ['\uFEFFpresent'], op: { _0_: 9n } }, { x: [], op: { _127_: 'x' } }];
  const expected = encodeCanonical([transaction], [target]);
  for (const batch of [[target], [target, ...neighbours], [...neighbours, target], [target, ...neighbours, target]]) {
    for (const metadata of [false, true]) {
      const fields = { transactions: IDL.Vec(transaction) };
      if (metadata) fields.extra = IDL.Record({ references: IDL.Vec(IDL.Principal), label: IDL.Text });
      const response = IDL.Record(fields);
      const client = IDL.Record({ transactions: IDL.Vec(CanonicalBytes) });
      const value = { transactions: batch, extra: { references: [Principal.anonymous()], label: 'metadata' } };
      for (const raw of [IDL.encode([response], [value]), encodeCanonical([response], [value])]) {
        const [result] = IDL.decode([decoderType(client)], raw);
        for (let i = 0; i < batch.length; i++) {
          const bytes = result.transactions[i];
          assert.deepEqual(bytes, encodeCanonical([transaction], [batch[i]]));
          if (batch[i] === target) assert.deepEqual(bytes, expected);
        }
      }
    }
  }
});

test('variant extraction remaps multi-byte wire tags to the standalone used case', () => {
  const fields = Object.fromEntries(Array.from({ length: 130 }, (_, i) => [`_${i}_`, IDL.Nat]));
  const type = IDL.Variant(fields), value = { _129_: 1000n };
  const raw = IDL.encode([type], [value]);
  const actual = checkValue(type, value);
  assert.deepEqual(decodeCompatible([type], actual), [value]);
  assert.notDeepEqual(raw, actual);
  assert.equal(hex(actual).endsWith('00e807'), true);
});

test('all scalar kinds, integer widths, signed zero, NaNs, exact text and principals', () => {
  for (const [type, value] of [
    [IDL.Null, null], [IDL.Reserved, null], [IDL.Bool, false], [IDL.Bool, true],
    [IDL.Nat, (1n << 1024n) + 1n], [IDL.Int, -(1n << 1024n) - 1n],
    [IDL.Nat8, 255], [IDL.Nat16, 65535], [IDL.Nat32, 4294967295], [IDL.Nat64, (1n << 64n) - 1n],
    [IDL.Int8, -128], [IDL.Int16, -32768], [IDL.Int32, -2147483648], [IDL.Int64, -(1n << 63n)],
    [IDL.Text, '\uFEFF\0é𝄞\uFFFD'], [IDL.Principal, Principal.anonymous()],
    [IDL.Principal, Principal.fromUint8Array(new Uint8Array(29).fill(1))],
  ]) checkValue(type, value);
  for (const type of [IDL.Float32, IDL.Float64])
    for (const value of [NaN, -0, 0, Infinity, -Infinity, 1.401298464324817e-45, 5e-324]) checkValue(type, value);
  assert.notDeepEqual(checkValue(IDL.Float64, -0), checkValue(IDL.Float64, 0));
});

test('wire NaN payloads normalize without projecting finite bits or signed zero', () => {
  for (const raw of ['4449444c0001730100807f', '4449444c000172010000000000f0ff',
    '4449444c00017300000080', '4449444c0001720000000000000080']) {
    const bytes = fromHex(raw);
    assert.deepEqual(decodeCompatible([CanonicalBytes], bytes), [canonicalize(bytes)]);
  }
});

test('nested arrays join each selected value independently and retain option presence', () => {
  const entry = IDL.Record({ name: IDL.Text, future: IDL.Opt(IDL.Opt(IDL.Text)), n: IDL.Null, r: IDL.Reserved });
  const value = [[{ name: 'a', future: [], n: null, r: null }, { name: 'b', future: [[]], n: null, r: null }],
    [], [{ name: 'c', future: [['\uFEFFfuture']], n: null, r: null }]];
  const type = IDL.Vec(IDL.Vec(entry));
  checkValue(type, value);
  const [arrays] = decodeCompatible([IDL.Vec(CanonicalBytes)], IDL.encode([type], [value]));
  for (let i = 0; i < value.length; i++) assert.deepEqual(arrays[i], encodeCanonical([IDL.Vec(entry)], [value[i]]));
  const optional = IDL.Opt(IDL.Opt(IDL.Null));
  const identities = [[], [[]], [[null]]].map(value => hex(checkValue(optional, value)));
  assert.equal(new Set(identities).size, 3);
});

test('recursive records, aliases, mutual recursion and omitted optional tuples', () => {
  const list = IDL.Rec(), alias = IDL.Rec(); alias.fill(list);
  list.fill(IDL.Opt(IDL.Record({ head: IDL.Text, next: alias, extra: IDL.Opt(IDL.Text) })));
  const value = [{ head: '\uFEFFnode', next: [{ head: 'tail', next: [], extra: ['new'] }], extra: [] }];
  // SDK 6.1.0 emits a malformed table for this source Rec-to-Rec alias.
  // Valid direct producer bytes still support the original aliased schema.
  assert.throws(() => canonicalize(IDL.encode([list], [value])));
  checkValue(list, value, false);
  const plain = IDL.Rec();
  plain.fill(IDL.Opt(IDL.Record({ head: IDL.Text, next: plain, extra: IDL.Opt(IDL.Text) })));
  checkValue(plain, value);
  const left = IDL.Rec(), right = IDL.Rec();
  left.fill(IDL.Opt(IDL.Record({ right, text: IDL.Text })));
  right.fill(IDL.Opt(IDL.Tuple(IDL.Text, left, IDL.Opt(IDL.Nat))));
  checkValue(left, [{ right: [['\uFEFFtuple', [], []]], text: 'record' }]);
  checkValue(IDL.Tuple(IDL.Opt(IDL.Text), IDL.Nat, IDL.Opt(IDL.Nat)), [[], 7n, []]);
});

test('binary vectors, empty nonbinary vectors and packed integer arrays', () => {
  for (const type of [IDL.Vec(IDL.Text), IDL.Vec(IDL.Empty), IDL.Vec(IDL.Func([], [], []))])
    checkValue(type, []);
  const binary = checkValue(IDL.Vec(IDL.Nat8), Uint8Array.of());
  assert.notDeepEqual(binary, checkValue(IDL.Vec(IDL.Nat), []));
  for (const [type, value] of [
    [IDL.Nat8, Uint8Array.of(0, 255)], [IDL.Int8, Int8Array.of(-128, 127)],
    [IDL.Nat16, Uint16Array.of(0, 65535)], [IDL.Int16, Int16Array.of(-32768, 32767)],
    [IDL.Nat32, Uint32Array.of(0, 4294967295)], [IDL.Int32, Int32Array.of(-2147483648, 2147483647)],
    [IDL.Nat64, BigUint64Array.of(0n, (1n << 64n) - 1n)], [IDL.Int64, BigInt64Array.of(-(1n << 63n), (1n << 63n) - 1n)],
  ]) checkValue(IDL.Vec(type), value);
});

test('markers work inside options, variants, tuples and mutually recursive expected schemas', () => {
  const wire = IDL.Record({ a: IDL.Opt(IDL.Nat), b: IDL.Variant({ chosen: IDL.Text }),
    c: IDL.Tuple(IDL.Nat, IDL.Text) });
  const expected = IDL.Record({ a: IDL.Opt(CanonicalBytes), b: IDL.Variant({ chosen: CanonicalBytes }),
    c: IDL.Tuple(CanonicalBytes, CanonicalBytes) });
  const [result] = decodeCompatible([expected], IDL.encode([wire], [{ a: [7n], b: { chosen: 'BOM\uFEFF' }, c: [9n, 'x'] }]));
  assert.deepEqual(result.a, [encodeCanonical([IDL.Nat], [7n])]);
  assert.deepEqual(result.b.chosen, encodeCanonical([IDL.Text], ['BOM\uFEFF']));
  assert.deepEqual(result.c, [encodeCanonical([IDL.Nat], [9n]), encodeCanonical([IDL.Text], ['x'])]);
  const rec = IDL.Rec(); rec.fill(IDL.Opt(IDL.Record({ value: CanonicalBytes, next: rec })));
  const source = IDL.Rec(); source.fill(IDL.Opt(IDL.Record({ value: IDL.Nat, next: source })));
  const value = [{ value: 1n, next: [{ value: 2n, next: [] }] }];
  const [decoded] = decodeCompatible([rec], encodeCanonical([source], [value]));
  assert.deepEqual(decoded, [{ value: encodeCanonical([IDL.Nat], [1n]), next: [{ value: encodeCanonical([IDL.Nat], [2n]), next: [] }] }]);
});

test('surrounding reference values and unknown metadata are decoded normally', () => {
  const callback = IDL.Func([IDL.Nat], [IDL.Text], ['query']);
  const response = IDL.Record({ transactions: IDL.Vec(IDL.Nat), callback, ignored: IDL.Text });
  const expected = IDL.Record({ transactions: IDL.Vec(CanonicalBytes), callback });
  const value = { transactions: [5n, 6n], callback: [Principal.anonymous(), 'fetch'], ignored: 'extra' };
  const raw = IDL.encode([response], [value]);
  assert.throws(() => canonicalize(raw), /function\/service/);
  const [decoded] = decodeCompatible([expected], raw);
  assert.deepEqual(decoded.callback, value.callback);
  assert.deepEqual(decoded.transactions, value.transactions.map(n => encodeCanonical([IDL.Nat], [n])));
});

test('SDK actor uses decoderType without transport changes and survives failed calls', async () => {
  const transactionType = IDL.Record({ a: IDL.Nat, b: IDL.Opt(IDL.Text) });
  const response = IDL.Record({ first_index: IDL.Nat, transactions: IDL.Vec(transactionType) });
  const expected = IDL.Record({ first_index: IDL.Nat, transactions: IDL.Vec(CanonicalBytes) });
  const transaction = { a: 7n, b: ['new field'] };
  let raw = IDL.encode([response], [{ first_index: 5n, transactions: [transaction] }]);
  const agent = { query: async (_canister, request) => {
    assert.equal(request.methodName, 'get_transactions');
    assert.deepEqual(IDL.decode([IDL.Nat], request.arg), [5n]);
    return { status: 'replied', reply: { arg: raw } };
  } };
  const actor = Actor.createActor(({ IDL }) => IDL.Service({
    get_transactions: IDL.Func([IDL.Nat], [decoderType(expected)], ['query']),
  }), { agent, canisterId: 'aaaaa-aa' });
  for (let i = 0; i < 2; i++) {
    const reply = await actor.get_transactions(5n);
    assert.equal(reply.first_index, 5n);
    assert.deepEqual(reply.transactions, [encodeCanonical([transactionType], [transaction])]);
    const saved = raw;
    raw = raw.slice(0, -1);
    await assert.rejects(actor.get_transactions(5n));
    raw = saved;
  }
});

test('optional coercion cannot hide extraction errors, including inside nested options', () => {
  const func = IDL.Func([], [], []), bad = IDL.encode([IDL.Opt(func)], [[[Principal.anonymous(), 'f']]]);
  const expected = IDL.Opt(CanonicalBytes), wrapped = decoderType(expected);
  assert.throws(() => decodeCompatible([expected], bad), /function\/service/);
  assert.throws(() => IDL.decode([wrapped], bad), /function\/service/);
  const nestedSource = IDL.Opt(IDL.Record({ value: IDL.Opt(func) }));
  const nestedExpected = IDL.Opt(IDL.Record({ value: IDL.Opt(CanonicalBytes) }));
  const raw = IDL.encode([nestedSource], [[{ value: [[Principal.anonymous(), 'f']] }]]);
  assert.throws(() => IDL.decode([decoderType(nestedExpected)], raw), /function\/service/);
  const valid = IDL.encode([IDL.Opt(IDL.Nat)], [[7n]]);
  assert.deepEqual(IDL.decode([wrapped], valid), [[encodeCanonical([IDL.Nat], [7n])]]);
  assert.deepEqual(IDL.decode([wrapped], IDL.encode([IDL.Opt(IDL.Nat)], [[]])), [[]]);
});

test('new selected cases remain hashable when the old transaction schema cannot decode them', () => {
  const old = IDL.Variant({ known: IDL.Nat }), grown = IDL.Variant({ known: IDL.Nat, future: IDL.Text });
  const bytes = checkValue(grown, { future: 'new operation' });
  assert.throws(() => decodeCompatible([old], bytes));
  assert.deepEqual(decodeCompatible([CanonicalBytes], bytes), [bytes]);
});

test('normalizes padded scalar LEB and rejects malformed or unsupported selected values', () => {
  assert.equal(hex(decodeCompatible([CanonicalBytes], fromHex('4449444c00017d8100'))[0]), '4449444c00017d01');
  const invalid = ['4449444c00017e02', '4449444c00017d80', '4449444c00017102ff00',
    '4449444c0001680000', '4449444c016e7d010002', '4449444c016b01617d010001',
    '4449444c016c02617d617d01000000', '4449444c00016f', '4449444c00017d0100'];
  for (const raw of invalid) assert.throws(() => decodeCompatible([CanonicalBytes], fromHex(raw)), raw);
  assert.throws(() => decodeCompatible([CanonicalBytes], concat(IDL.encode([IDL.Nat], [1n]), [0])), /left-over|trailing/i);
});

test('resource bounds include nested values, binary bytes, scalar LEB and text', () => {
  for (const [type, value] of [[IDL.Nat, (1n << (4096n * 7n)) - 1n],
    [IDL.Int, -(1n << (4096n * 7n - 1n))]]) checkValue(type, value);
  assert.throws(() => decodeCompatible([CanonicalBytes], IDL.encode([IDL.Nat], [1n << (4096n * 7n)])), /integer length limit/);
  assert.throws(() => decodeCompatible([CanonicalBytes], IDL.encode([IDL.Text], ['x'.repeat(2_000_001)])), /collection length limit/);
  assert.throws(() => decodeCompatible([CanonicalBytes], IDL.encode([IDL.Vec(IDL.Nat8)], [new Uint8Array(2_000_000)])), /value traversal limit/);
  let type = IDL.Vec(IDL.Nat8), empty = new Uint8Array(), nonempty = Uint8Array.of(1);
  for (let i = 0; i < 256; i++) { type = IDL.Opt(type); empty = [empty]; nonempty = [nonempty]; }
  checkValue(type, empty);
  assert.throws(() => decodeCompatible([CanonicalBytes], IDL.encode([type], [nonempty])), /value traversal limit/);
});

test('wire and reachable canonical type tables enforce 1024 definitions', () => {
  const make = count => {
    const fields = {}, value = {};
    for (let i = 0; i < count; i++) {
      fields[`_${i}_`] = IDL.Record({ [`_${i}_`]: IDL.Nat });
      value[`_${i}_`] = { [`_${i}_`]: 1n };
    }
    return [IDL.Record(fields), value];
  };
  checkValue(...make(1023));
  const [type, value] = make(1024);
  assert.throws(() => decodeCompatible([CanonicalBytes], IDL.encode([type], [value])), /input type-table limit/);
});

test('SDK globals, frozen schemas, input bytes and independent results remain unchanged', () => {
  const source = IDL.Vec(IDL.Text), expected = IDL.Vec(CanonicalBytes);
  const raw = IDL.encode([source], [['\uFEFFone', 'two']]), original = raw.slice();
  const textDecoder = IDL.Text.decodeValue;
  Object.freeze(expected);
  Object.freeze(CanonicalBytes);
  const decoder = decoderType(expected);
  for (let i = 0; i < 3; i++) {
    const [values] = decodeCompatible([decoder], raw);
    assert.equal(IDL.Text.decodeValue, textDecoder);
    assert.deepEqual(raw, original);
    assert.equal(expected._type, CanonicalBytes);
    assert.deepEqual(values[0], encodeCanonical([IDL.Text], ['\uFEFFone']));
    values[0].fill(0);
    assert.deepEqual(values[1], encodeCanonical([IDL.Text], ['two']));
    assert.deepEqual(raw, original);
  }
  assert.throws(() => IDL.encode([CanonicalBytes], [new Uint8Array()]), /Unknown|Invalid/);
  assert.throws(() => encodeCanonical([CanonicalBytes], [new Uint8Array()]), /unsupported/);
  assert.throws(() => IDL.encode([decoder], [[]]), /Unknown|Invalid/);
});

test('selected values bypass SDK JS value decoders', () => {
  const rec = IDL.Rec();
  const option = IDL.Opt(IDL.Text), binary = IDL.Vec(IDL.Nat8);
  const variant = IDL.Variant({ chosen: IDL.Nat, unused: IDL.Text });
  const record = IDL.Record({ text: IDL.Text, option, binary, variant, next: rec });
  rec.fill(IDL.Opt(record));
  const value = [{ text: '\uFEFFexact text', option: ['present'], binary: Uint8Array.of(0, 255),
    variant: { chosen: 7n }, next: [] }];
  const raw = IDL.encode([rec], [value]), expected = encodeCanonical([rec], [value]);
  const prototypes = new Set([rec, rec.getType(), record, option, binary, variant, IDL.Text, IDL.Nat, IDL.Nat8]
    .map(type => Object.getPrototypeOf(type)));
  const saved = [];
  for (const prototype of prototypes) {
    saved.push([prototype, Object.getOwnPropertyDescriptor(prototype, 'decodeValue')]);
    Object.defineProperty(prototype, 'decodeValue', { configurable: true,
      value() { throw new Error('SDK JS value decoder invoked'); } });
  }
  try {
    assert.deepEqual(decodeCompatible([CanonicalBytes], raw), [expected]);
    assert.deepEqual(IDL.decode([decoderType(CanonicalBytes)], raw), [expected]);
  } finally {
    for (const [prototype, descriptor] of saved) {
      if (descriptor) Object.defineProperty(prototype, 'decodeValue', descriptor);
      else delete prototype.decodeValue;
    }
  }
});

test('CanonicalBytes never copies the remaining reply through Pipe.buffer during batch capture', () => {
  const values = Array.from({ length: 100 }, (_, i) => ({ n: BigInt(i), text: '\uFEFFpayload' }));
  const transaction = IDL.Record({ n: IDL.Nat, text: IDL.Text });
  const raw = IDL.encode([IDL.Vec(transaction)], [values]);
  const expected = values.map(value => encodeCanonical([transaction], [value]));
  const prototype = PipeArrayBuffer.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, 'buffer');
  const markerPrototype = Object.getPrototypeOf(CanonicalBytes);
  const decodeDescriptor = Object.getOwnPropertyDescriptor(markerPrototype, 'decodeValue');
  let capturing = false;
  Object.defineProperty(prototype, 'buffer', { configurable: true,
    get() {
      if (capturing) throw new Error('remaining reply copied during capture');
      return descriptor.get.call(this);
    } });
  Object.defineProperty(markerPrototype, 'decodeValue', { configurable: true,
    value(pipe, wire) {
      capturing = true;
      try { return decodeDescriptor.value.call(this, pipe, wire); }
      finally { capturing = false; }
    } });
  try {
    assert.deepEqual(decodeCompatible([IDL.Vec(CanonicalBytes)], raw), [expected]);
    assert.deepEqual(IDL.decode([decoderType(IDL.Vec(CanonicalBytes))], raw), [expected]);
  } finally {
    Object.defineProperty(prototype, 'buffer', descriptor);
    Object.defineProperty(markerPrototype, 'decodeValue', decodeDescriptor);
  }
});

test('decodeCompatible accepts sliced Uint8Array and pooled Buffer without mutation', () => {
  const raw = IDL.encode([IDL.Text], ['\uFEFFtext']);
  const storage = Uint8Array.from([1, 2, 3, ...raw, 9, 8]);
  const view = storage.subarray(3, 3 + raw.length), before = storage.slice();
  const buffer = Buffer.from(view);
  for (const input of [view, buffer]) {
    const snapshot = Uint8Array.from(input);
    assert.deepEqual(decodeCompatible([CanonicalBytes], input), [encodeCanonical([IDL.Text], ['\uFEFFtext'])]);
    assert.deepEqual(decodeCompatible([IDL.Text], input), ['\uFEFFtext']);
    assert.deepEqual(Uint8Array.from(input), snapshot);
  }
  assert.deepEqual(storage, before);
});
