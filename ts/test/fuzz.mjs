// Consumes the exact shared schema/value corpus produced by tests/fuzz.py.
// Direct canonical serialization consumes SDK types/values; ordinary SDK
// serialization remains the independent incoming-message baseline.
import { readFileSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { createHash } from 'node:crypto';
import { IDL } from '@icp-sdk/core/candid';
import { Principal } from '@icp-sdk/core/principal';
import { canonicalize } from '../dist/index.js';
import { encodeCanonical, decodeCompatible, CanonicalBytes, decoderType } from '../dist/candid.js';

const hex = b => Buffer.from(b).toString('hex');
const digest = b => createHash('sha256').update(b).digest('hex');
function type(schema) {
  const [kind, child] = schema;
  if (kind === 'tuple') return IDL.Tuple(...schema.slice(1).map(type));
  if (kind === 'float32bits') return IDL.Float32;
  if (kind === 'float64bits') return IDL.Float64;
  if (kind === 'chain') {
    const rec = IDL.Rec();
    rec.fill(IDL.Record({ head: IDL.Nat, tail: IDL.Opt(rec) }));
    return rec;
  }
  if (kind === 'opt') return IDL.Opt(type(child));
  if (kind === 'vec' || kind === 'blob') return IDL.Vec(kind === 'blob' ? IDL.Nat8 : type(child));
  if (kind === 'record' || kind === 'variant') {
    const fields = Object.fromEntries(schema.slice(1).map(([label, t]) => [label, type(t)]));
    return kind === 'record' ? IDL.Record(fields) : IDL.Variant(fields);
  }
  if (kind === 'list') {
    const rec = IDL.Rec();
    const fields = { item: type(child), next: rec };
    if (schema[2]) fields.extra = IDL.Opt(IDL.Text);
    rec.fill(IDL.Opt(IDL.Record(fields)));
    return rec;
  }
  return IDL[kind === 'float64' ? 'Float64' : kind === 'float32' ? 'Float32' : kind[0].toUpperCase() + kind.slice(1)];
}
function value(schema, v) {
  const [kind, child] = schema;
  if (kind === 'tuple') {
    if (v.length !== schema.length - 1) throw new Error('original tuple arity mismatch');
    return schema.slice(1).map((t, i) => value(t, v[i]));
  }
  if (kind === 'chain') return { head: BigInt(v.head), tail: v.tail.length ? [value(schema, v.tail[0])] : [] };
  if (kind === 'float32bits' || kind === 'float64bits') {
    const data = new DataView(new ArrayBuffer(8));
    if (kind === 'float32bits') { data.setUint32(0, Number(BigInt(v)), true); return data.getFloat32(0, true); }
    data.setBigUint64(0, BigInt(v), true); return data.getFloat64(0, true);
  }
  if (kind === 'opt') return v.length ? [value(child, v[0])] : [];
  if (kind === 'vec') return v.map(x => value(child, x));
  if (kind === 'blob') return Uint8Array.from(v);
  if (kind === 'record') return Object.fromEntries(schema.slice(1).map(([label, t]) => [label, value(t, v[label])]));
  if (kind === 'variant') { const label = Object.keys(v)[0]; return { [label]: value(schema.slice(1).find(([k]) => k === label)[1], v[label]) }; }
  if (kind === 'list') {
    if (!v.length) return [];
    const record = { item: value(child, v[0].item), next: value(schema, v[0].next) };
    if (schema[2]) record.extra = value(['opt', ['text']], v[0].extra);
    return [record];
  }
  if (kind === 'nat' || kind === 'int' || kind === 'nat64' || kind === 'int64') return BigInt(v);
  if (kind === 'float64' || kind === 'float32') {
    const number = v === 'nan' ? NaN : v === 'inf' ? Infinity : v === '-inf' ? -Infinity : Number(v);
    return kind === 'float32' ? Math.fround(number) : number;
  }
  if (kind === 'principal') return Principal.fromUint8Array(Uint8Array.from(v));
  if (kind === 'reserved') return null;
  return v;
}
function hasRecursiveOption(schema) {
  const kind = schema[0];
  if (kind === 'list' || kind === 'chain') return true;
  if (kind === 'tuple') return schema.slice(1).some(hasRecursiveOption);
  if (kind === 'opt' || kind === 'vec') return hasRecursiveOption(schema[1]);
  return (kind === 'record' || kind === 'variant') && schema.slice(1).some(([, child]) => hasRecursiveOption(child));
}
function normalized(schema, v) {
  const kind = schema[0];
  if (kind === 'tuple') {
    if (!Array.isArray(v) || v.length !== schema.length - 1) throw new Error('decoded tuple arity mismatch');
    return schema.slice(1).map((t, i) => normalized(t, v[i]));
  }
  if (kind === 'chain') {
    if (Object.keys(v).length !== 2 || !Object.hasOwn(v, 'head') || !Object.hasOwn(v, 'tail'))
      throw new Error('decoded recursive record field mismatch');
    return { head: v.head, tail: normalized(['opt', schema], v.tail) };
  }
  if (kind === 'opt') {
    if (!Array.isArray(v) || v.length > 1) throw new Error('decoded option arity mismatch');
    return v.length ? [normalized(schema[1], v[0])] : [];
  }
  if (kind === 'vec') return Array.from(v, x => normalized(schema[1], x));
  if (kind === 'blob') return Array.from(v);
  if (kind === 'record') {
    if (Object.keys(v).length !== schema.length - 1) throw new Error('decoded record field count mismatch');
    return Object.fromEntries(schema.slice(1).map(([label, child]) => {
    if (!Object.hasOwn(v, label)) throw new Error(`decoder omitted expected field ${label}`);
    return [label, normalized(child, v[label])];
    }));
  }
  if (kind === 'variant') {
    if (Object.keys(v).length !== 1) throw new Error('decoded variant arity mismatch');
    const label = Object.keys(v)[0];
    return { [label]: normalized(schema.slice(1).find(([k]) => k === label)[1], v[label]) };
  }
  if (kind === 'list') {
    if (!Array.isArray(v) || v.length > 1) throw new Error('decoded recursive option arity mismatch');
    if (!v.length) return [];
    if (Object.keys(v[0]).length !== (schema[2] ? 3 : 2) || !Object.hasOwn(v[0], 'item') || !Object.hasOwn(v[0], 'next'))
      throw new Error('decoded recursive record field mismatch');
    const record = { item: normalized(schema[1], v[0].item), next: normalized(schema, v[0].next) };
    if (schema[2]) {
      if (!Object.hasOwn(v[0], 'extra')) throw new Error('decoder omitted expected recursive extra field');
      record.extra = normalized(['opt', ['text']], v[0].extra);
    }
    return [record];
  }
  if (kind === 'principal') return Array.from(v.toUint8Array());
  if (kind === 'reserved') return null;
  return v;
}
function recoverOriginal(types, schemas, bytes, original) {
  // Ordinary SDK decoding projects away additional roots. Probe with one
  // extra Unknown decoder to reject that projection in this complete-x test.
  let extraRoots = false;
  try { IDL.decode(Array(types.length + 1).fill(IDL.Unknown), bytes); extraRoots = true; }
  catch (error) { if (error.message !== 'Wrong number of return values') throw error; }
  if (extraRoots) throw new Error('argument/root count differs from original typed input');
  const decoded = decodeCompatible(types, bytes);
  if (decoded.length !== original.length || !isDeepStrictEqual(schemas.map((s, i) => normalized(s, decoded[i])), original))
    throw new Error('decodeCompatible does not recover the original typed value');
}

const clientDecoders = [decodeCompatible, (types, bytes) => IDL.decode(types.map(decoderType), bytes)];
function verifyCapture(bytes, expected, type, schema, original) {
  if (!(bytes instanceof Uint8Array) || hex(bytes) !== hex(expected) || digest(bytes) !== digest(expected))
    throw new Error('CanonicalBytes differs from standalone direct producer bytes/hash');
  recoverOriginal([type], [schema], bytes, [original]);
}
function captureRoots(types, schemas, bytes, original, expected) {
  let checks = 0;
  for (const decode of clientDecoders) {
    const captured = decode(types.map(() => CanonicalBytes), bytes);
    if (captured.length !== types.length) throw new Error('CanonicalBytes argument count mismatch');
    captured.forEach((bytes, i) => {
      verifyCapture(bytes, expected[i], types[i], schemas[i], original[i]);
      checks++;
    });
  }
  return checks;
}
function captureEnvelopes(types, schemas, values, original, expected) {
  let checks = 0;
  for (let i = 0; i < types.length; i++) {
    const wire = IDL.Record({ metadata: IDL.Text, transactions: IDL.Vec(types[i]),
      optional: IDL.Opt(types[i]), choice: IDL.Variant({ selected: types[i], unused: IDL.Null }) });
    const client = IDL.Record({ transactions: IDL.Vec(CanonicalBytes), optional: IDL.Opt(CanonicalBytes),
      choice: IDL.Variant({ selected: CanonicalBytes, unused: IDL.Null }) });
    const reply = { metadata: '\uFEFFunknown future metadata', transactions: [values[i], values[i]],
      optional: [values[i]], choice: { selected: values[i] } };
    for (const raw of [IDL.encode([wire], [reply]), encodeCanonical([wire], [reply])]) {
      for (const decode of clientDecoders) {
        const [result] = decode([client], raw);
        if (Object.keys(result).length !== 3 || result.transactions.length !== 2 || result.optional.length !== 1)
          throw new Error('CanonicalBytes envelope shape mismatch');
        for (const bytes of [...result.transactions, result.optional[0], result.choice.selected]) {
          verifyCapture(bytes, expected[i], types[i], schemas[i], original[i]);
          checks++;
        }
      }
    }
  }
  return checks;
}
const corpus = JSON.parse(readFileSync(process.argv[2], 'utf8'));
for (const c of corpus.cases) {
  try {
    const schemas = c.schemas ?? [c.schema], sourceValues = c.values ?? [c.value];
    if (schemas.length !== sourceValues.length) throw new Error('original source argument count mismatch');
    const types = schemas.map(type), values = schemas.map((t, i) => value(t, sourceValues[i]));
    const original = schemas.map((s, i) => normalized(s, values[i]));
    if (corpus.comparison_only) {
      if (c.decoded_schemas.length !== c.decoded_values.length) throw new Error('mutant source argument count mismatch');
      const decoded = c.decoded_schemas.map((schema, i) => value(schema, c.decoded_values[i]));
      let equal = false;
      try {
        equal = decoded.length === original.length && isDeepStrictEqual(schemas.map((schema, i) => normalized(schema, decoded[i])), original);
      } catch { /* Structural mismatches must reject the mutant. */ }
      console.log(JSON.stringify({id: c.id, comparison_equal: equal}));
      continue;
    }
    if (corpus.decode_only) {
      const roundtrips = {}, clientChecks = {};
      const expected = types.map((type, i) => encodeCanonical([type], [values[i]]));
      for (const [encoder, encoded] of Object.entries(c.decode_inputs)) {
        // SDK PipeArrayBuffer reads from backing-buffer offset zero. Give it
        // an owned Uint8Array instead of a pooled Node Buffer subview.
        const bytes = Uint8Array.from(Buffer.from(encoded, 'hex'));
        recoverOriginal(types, schemas, bytes, original);
        clientChecks[encoder] = captureRoots(types, schemas, bytes, original, expected);
        roundtrips[encoder] = 'exact';
      }
      console.log(JSON.stringify({id: c.id, roundtrips, client_canonical_checks: clientChecks}));
      continue;
    }
    const raw = IDL.encode(types, values), canonical = encodeCanonical(types, values);
    const expected = types.map((type, i) => encodeCanonical([type], [values[i]]));
    const clientChecks = captureRoots(types, schemas, raw, original, expected)
      + captureRoots(types, schemas, canonical, original, expected)
      + captureEnvelopes(types, schemas, values, original, expected);
    if (hex(canonicalize(raw)) !== hex(canonical)) throw new Error('direct encoding differs from ordinary-message normalization');
    if (hex(canonicalize(canonical)) !== hex(canonical)) throw new Error('canonical idempotence failure');
    for (const [label, bytes] of [['ordinary', raw], ['canonical', canonical]]) {
      try { recoverOriginal(types, schemas, bytes, original); }
      catch (error) { throw new Error(`${label}: ${error.message}`); }
    }
    if (hex(IDL.encode(types, values)) !== hex(raw)) throw new Error('decodeCompatible mutated the caller schema');
    const decodedRaw = IDL.decode(types, raw);
    const baseline = canonicalize(IDL.encode(types, decodedRaw));
    let upstreamExact;
    try { upstreamExact = decodedRaw.length === original.length && isDeepStrictEqual(schemas.map((s, i) => normalized(s, decodedRaw[i])), original); }
    catch { upstreamExact = false; }
    let decoding;
    try {
      const decodedValues = IDL.decode(types, canonical);
      const decoded = canonicalize(IDL.encode(types, decodedValues));
      let sameValues;
      try { sameValues = decodedValues.length === original.length && isDeepStrictEqual(schemas.map((s, i) => normalized(s, decodedValues[i])), original); }
      catch { sameValues = false; }
      decoding = hex(decoded) === hex(canonical) && sameValues ? 'exact'
        : !upstreamExact ? 'upstream-decoder-loss'
        : schemas.some(hasRecursiveOption) ? 'sdk-recursive-optional-field-loss' : 'canonical-decoder-loss';
    } catch (error) {
      decoding = schemas.some(hasRecursiveOption) && error.message.includes('Cannot find required field')
        ? 'sdk-recursive-optional-field-loss' : `canonical-decode-error: ${error.message}`;
    }
    console.log(JSON.stringify({ id: c.id, direct_path: 'typed-values', raw: hex(raw), canonical: hex(canonical), decoding, compatible_decoding: 'exact', canonical_roundtrip: true, ordinary_roundtrip: true, client_canonical_checks: clientChecks, upstream_decoding_exact: upstreamExact, upstream_reencoding_exact: hex(baseline) === hex(canonical) }));
  } catch (error) {
    console.log(JSON.stringify({ id: c.id, error: `${error.name}: ${error.message}` }));
    process.exitCode = 1;
  }
}
