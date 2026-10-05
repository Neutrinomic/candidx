import { test } from 'node:test';
import assert from 'node:assert/strict';
import { IDL } from '@icp-sdk/core/candid';
import { encodeCanonical, decodeCompatible } from '../dist/candid.js';

test('SDK adapter preserves BOM text without changing SDK globals', () => {
  const schema = IDL.Record({ text: IDL.Text, nested: IDL.Vec(IDL.Opt(IDL.Text)) });
  const value = { text: '\uFEFFabc', nested: [[], ['\uFEFF'], ['x\uFEFF']] };
  const raw = IDL.encode([schema], [value]);
  const bytes = encodeCanonical([schema], [value]);
  assert.deepEqual(decodeCompatible([schema], raw), [value]);
  assert.deepEqual(decodeCompatible([schema], bytes), [value]);
  assert.equal(IDL.decode([schema], raw)[0].text, 'abc');
});

test('SDK adapter reconstructs leading, trailing and all-absent optional tuples', () => {
  for (const [schema, value] of [
    [IDL.Tuple(IDL.Opt(IDL.Nat), IDL.Text), [[], 'x']],
    [IDL.Tuple(IDL.Text, IDL.Opt(IDL.Nat)), ['x', []]],
    [IDL.Tuple(IDL.Opt(IDL.Nat)), [[]]],
    [IDL.Tuple(IDL.Text, IDL.Opt(IDL.Nat), IDL.Bool), ['x', [], true]],
  ]) {
    const bytes = encodeCanonical([schema], [value]);
    assert.deepEqual(decodeCompatible([schema], bytes), [value]);
    assert.throws(() => IDL.decode([schema], bytes));
  }
});

test('SDK adapter recovers finite recursive optional fields without mutating schemas', () => {
  const list = IDL.Rec();
  list.fill(IDL.Opt(IDL.Record({ head: IDL.Nat, tail: list, text: IDL.Text })));
  const schema = IDL.Vec(list);
  const value = [[], [{ head: 1n, tail: [], text: '\uFEFFend' }],
    [{ head: 2n, tail: [{ head: 3n, tail: [], text: 'tail' }], text: 'head' }]];
  const before = IDL.encode([schema], [value]);
  const bytes = encodeCanonical([schema], [value]);
  assert.deepEqual(decodeCompatible([schema], bytes), [value]);
  assert.deepEqual(decodeCompatible([schema], before), [value]);
  assert.deepEqual(IDL.encode([schema], [value]), before);
  assert.notDeepEqual(IDL.decode([schema], bytes), [value]);
});

test('SDK adapter retains option presence inside an optional tuple', () => {
  const schema = IDL.Opt(IDL.Tuple(IDL.Opt(IDL.Opt(IDL.Null)), IDL.Opt(IDL.Text)));
  const values = [[], [[[], []]], [[[[]], []]], [[[[null]], ['\uFEFFpresent']]]];
  const encodings = [];
  for (const value of values) {
    const bytes = encodeCanonical([schema], [value]);
    encodings.push(Buffer.from(bytes).toString('hex'));
    assert.deepEqual(decodeCompatible([schema], bytes), [value]);
  }
  assert.equal(new Set(encodings).size, values.length);
});

test('SDK adapter handles mutual recursion through nullable tuple and record fields', () => {
  const tuple = IDL.Rec(), record = IDL.Rec();
  tuple.fill(IDL.Opt(IDL.Tuple(IDL.Text, record)));
  record.fill(IDL.Opt(IDL.Record({ text: IDL.Text, next: tuple })));
  const value = [['\uFEFFtuple', [{ text: '\uFEFFrecord', next: [] }]]];
  const values = [value, [], [[], value]];
  const schemas = [tuple, record, IDL.Vec(tuple)];
  const before = IDL.encode(schemas, values);
  const bytes = encodeCanonical(schemas, values);
  const tupleDefinition = tuple.getType(), recordDefinition = record.getType();
  Object.freeze(tuple);
  Object.freeze(record);
  Object.freeze(tupleDefinition);
  Object.freeze(recordDefinition);
  Object.freeze(schemas);
  for (let repeat = 0; repeat < 3; repeat++) {
    assert.deepEqual(decodeCompatible(schemas, bytes), values);
    assert.deepEqual(decodeCompatible(schemas, before), values);
    assert.equal(tuple.getType(), tupleDefinition);
    assert.equal(record.getType(), recordDefinition);
    assert.deepEqual(IDL.encode(schemas, values), before);
  }
});

test('SDK adapter unfolds chained recursive aliases while preserving nested option absence', () => {
  const alias = IDL.Rec(), definition = IDL.Rec();
  alias.fill(definition);
  definition.fill(IDL.Opt(IDL.Opt(IDL.Record({ next: alias, text: IDL.Text }))));
  const schema = IDL.Record({ first: alias, second: alias });
  for (const first of [[], [[]], [[{ next: [], text: '\uFEFFvalue' }]]]) {
    const value = { first, second: [[{ next: [[]], text: '\uFEFFsecond' }]] };
    const before = IDL.encode([schema], [value]);
    const bytes = encodeCanonical([schema], [value]);
    assert.deepEqual(decodeCompatible([schema], bytes), [value]);
    assert.deepEqual(IDL.encode([schema], [value]), before);
    assert.equal(alias.getType(), definition);
  }
});

test('SDK adapter preserves mutual recursive record aliases with different option depths', () => {
  const left = IDL.Rec(), right = IDL.Rec();
  const leftAlias = IDL.Rec(), rightAlias = IDL.Rec();
  leftAlias.fill(left);
  rightAlias.fill(right);
  left.fill(IDL.Opt(IDL.Record({ peer: rightAlias, text: IDL.Text })));
  right.fill(IDL.Opt(IDL.Opt(IDL.Record({ peer: leftAlias, text: IDL.Text }))));
  const schema = IDL.Record({ left: leftAlias, right: rightAlias });
  const values = [
    { left: [], right: [] },
    { left: [{ peer: [[]], text: '\uFEFFleft' }], right: [[]] },
    { left: [{ peer: [[{ peer: [], text: '\uFEFFinner' }]], text: 'outer' }],
      right: [[{ peer: [{ peer: [], text: 'inner' }], text: '\uFEFFright' }]] },
  ];
  for (const value of values) {
    const before = IDL.encode([schema], [value]);
    const bytes = encodeCanonical([schema], [value]);
    assert.deepEqual(decodeCompatible([schema], bytes), [value]);
    assert.deepEqual(IDL.encode([schema], [value]), before);
    assert.equal(leftAlias.getType(), left);
    assert.equal(rightAlias.getType(), right);
  }
});

test('SDK adapter preserves all absence depths in a deeply nested option vector', () => {
  const depth = 64;
  let element = IDL.Text;
  for (let i = 0; i < depth; i++) element = IDL.Opt(element);
  const values = Array.from({ length: depth }, (_, level) => {
    let value = [];
    for (let i = 0; i < level; i++) value = [value];
    return value;
  });
  let present = '\uFEFFdeep';
  for (let i = 0; i < depth; i++) present = [present];
  values.push(present);
  const schema = IDL.Vec(element);
  const bytes = encodeCanonical([schema], [values]);
  assert.deepEqual(decodeCompatible([schema], bytes), [values]);
});

test('SDK adapter keeps ordinary projection and unknown optional variant coercion', () => {
  const oldRecord = IDL.Record({ text: IDL.Text });
  const newRecord = IDL.Record({ text: IDL.Text, future: IDL.Vec(IDL.Text) });
  const bytes = encodeCanonical([newRecord], [{ text: '\uFEFFknown', future: ['new data'] }]);
  const decoded = decodeCompatible([oldRecord], bytes);
  assert.deepEqual(decoded, [{ text: '\uFEFFknown' }]);
  assert.notDeepEqual(encodeCanonical([oldRecord], decoded), bytes);
  const oldChoice = IDL.Opt(IDL.Variant({ known: IDL.Text }));
  const newChoice = IDL.Opt(IDL.Variant({ known: IDL.Text, future: IDL.Nat }));
  const unknown = encodeCanonical([newChoice], [[{ future: 7n }]]);
  assert.deepEqual(decodeCompatible([oldChoice], unknown), [[]]);
  assert.notDeepEqual(encodeCanonical([oldChoice], [[]]), unknown);
});

test('SDK adapter explicitly rejects decoding-only Unknown and uninitialized schemas', () => {
  const bytes = encodeCanonical([IDL.Null], [null]);
  for (const schema of [IDL.Unknown, IDL.Record({ unknown: IDL.Opt(IDL.Unknown) })])
    assert.throws(() => decodeCompatible([schema], bytes), {
      name: 'Error', message: /decoding-only IDL\.Unknown.*original-schema/,
    });
  const recursive = IDL.Rec();
  assert.throws(() => decodeCompatible([recursive], bytes), /uninitialized recursive schema/);
  assert.equal(recursive.getType(), undefined);
});
