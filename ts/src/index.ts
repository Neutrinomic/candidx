import { MAX_BYTES, MAX_ITEMS, MAX_TYPES, MAX_DEPTH, Shape as Ty, Shapes, Writer, fail, writeHeader } from './codec.js';

/** cx. No hashing or application schema identifier. */
export const PROFILE = 'cx';
const utf8 = new TextDecoder('utf-8', { fatal: true });
type Node = { op: number; labels: number[]; edges: number[] };
type Val = { op: number; raw?: Uint8Array; items?: Val[]; labels?: number[]; binary?: boolean };
const nullVal: Val = { op: -1 };
class Reader {
  pos = 0;
  constructor(readonly data: Uint8Array) {}
  take(n: number): Uint8Array {
    if (!Number.isSafeInteger(n) || n < 0 || n > this.data.length - this.pos) fail('truncated message');
    const out = this.data.subarray(this.pos, this.pos + n);
    this.pos += n;
    return out;
  }
  byte(): number {
    if (this.pos === this.data.length) fail('truncated message');
    return this.data[this.pos++];
  }
  integer(signed = false, max = 4096): bigint {
    let value = 0n, shift = 0n;
    for (let i = 0; i < max; i++) {
      const byte = this.byte();
      value |= BigInt(byte & 127) << shift;
      shift += 7n;
      if (byte < 128) return signed && (byte & 64) ? value - (1n << shift) : value;
    }
    return fail('integer length limit');
  }
  scalarInteger(signed: boolean): Uint8Array {
    const start = this.pos;
    for (let i = 0; i < 4096; i++) {
      if (this.byte() < 128) {
        let end = this.pos;
        while (end - start > 1) {
          const last = this.data[end - 1] & 127, previous = this.data[end - 2];
          if ((!signed && last === 0) || (signed && ((last === 0 && !(previous & 64)) || (last === 127 && (previous & 64))))) end--;
          else break;
        }
        // Node Buffer.slice aliases its input despite extending Uint8Array.
        const bytes = Uint8Array.from(this.data.subarray(start, end));
        bytes[bytes.length - 1] &= 127;
        return bytes;
      }
    }
    return fail('integer length limit');
  }
  number(signed = false): number {
    const value = this.integer(signed, 10);
    if (value > BigInt(Number.MAX_SAFE_INTEGER) || value < BigInt(Number.MIN_SAFE_INTEGER)) fail('index range');
    return Number(value);
  }
  length(): number {
    const n = this.number();
    if (n > MAX_ITEMS) fail('collection length limit');
    return n;
  }
  text(): Uint8Array {
    const bytes = this.take(this.length());
    utf8.decode(bytes);
    return bytes;
  }
}

/** Normalize an ordinary Candid message before any typed receiver projection. */
export function canonicalize(input: Uint8Array | ArrayBuffer): Uint8Array {
  const data = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (data.length > MAX_BYTES) fail('input byte limit');
  const r = new Reader(data);
  if (Array.from(r.take(4)).join(',') !== '68,73,68,76') fail('missing DIDL magic');
  const count = r.length();
  if (count > MAX_TYPES) fail('input type-table limit');
  const nodes: Node[] = [];
  // Parse unused reference signatures too; only actual reference values fail.
  const refs = (): number[] => Array.from({ length: r.length() }, () => r.number(true));
  for (let i = 0; i < count; i++) {
    const op = r.number(true), labels: number[] = [], edges: number[] = [];
    if (op === -18 || op === -19) edges.push(r.number(true));
    else if (op === -20 || op === -21) {
      const n = r.length();
      for (let j = 0; j < n; j++) {
        const id = r.number();
        if (id > 0xffffffff || (j > 0 && id <= labels[j - 1])) fail('invalid field IDs');
        labels.push(id); edges.push(r.number(true));
      }
    } else if (op === -22) {
      const args = refs(), rets = refs();
      for (const edge of args) edges.push(edge);
      for (const edge of rets) edges.push(edge);
      const modes = r.take(r.length());
      if (modes.length > 1 || (modes.length === 1 && ![1, 2, 3].includes(modes[0])) || (modes[0] === 2 && rets.length)) fail('invalid function annotations');
    } else if (op === -23) {
      const n = r.length(); let previous: Uint8Array | undefined;
      for (let j = 0; j < n; j++) {
        const name = r.text();
        if (previous && compareBytes(previous, name) >= 0) fail('unordered service methods');
        previous = name; edges.push(r.number(true));
      }
    } else fail('unsupported compound type');
    nodes.push({ op, labels, edges });
  }
  const roots = refs();
  for (const ref of [...roots, ...nodes.flatMap(n => n.edges)]) {
    if (ref >= nodes.length || (ref < 0 && !(ref >= -17 && ref <= -1) && ref !== -24)) fail('invalid type reference');
  }
  for (const n of nodes) if (n.op === -23 && n.edges.some(e => e < 0 || nodes[e].op !== -22)) fail('method must be func');
  let steps = 0;
  const read = (ref: number, depth: number): Val => {
    if (++steps > MAX_ITEMS || depth > MAX_DEPTH) fail('value traversal limit');
    if (ref >= 0) {
      const n = nodes[ref];
      if (n.op === -18) {
        const tag = r.byte();
        if (tag === 0) return nullVal;
        if (tag !== 1) fail('invalid option tag');
        const child = read(n.edges[0], depth + 1);
        return { op: -18, items: [child] };
      }
      if (n.op === -19) {
        const length = r.length();
        if (n.edges[0] === -5) {
          steps += length;
          if (steps > MAX_ITEMS || (length !== 0 && depth + 1 > MAX_DEPTH)) fail('value traversal limit');
          return { op: -19, raw: r.take(length), binary: true };
        }
        return { op: -19, items: Array.from({ length }, () => read(n.edges[0], depth + 1)) };
      }
      if (n.op === -20) {
        const items: Val[] = [], labels: number[] = [];
        n.edges.forEach((edge, i) => {
          const value = read(edge, depth + 1);
          const absent = edge >= 0 && nodes[edge].op === -18 && value.op === -1;
          if (!absent) { labels.push(n.labels[i]); items.push(value); }
        });
        return { op: -20, labels, items };
      }
      if (n.op === -21) {
        const index = r.number();
        if (index >= n.edges.length) fail('invalid variant index');
        return { op: -21, labels: [n.labels[index]], items: [read(n.edges[index], depth + 1)] };
      }
      return fail('function/service values outside data profile');
    }
    if (ref === -1) return nullVal;
    if (ref === -16) return { op: -16 };
    if (ref === -17) fail('empty has no values');
    let raw: Uint8Array;
    if (ref === -2) { raw = r.take(1); if (raw[0] > 1) fail('invalid bool'); }
    else if (ref === -3 || ref === -4) raw = r.scalarInteger(ref === -4);
    else if (ref >= -12 && ref <= -5) raw = r.take(2 ** ((-ref - 5) % 4));
    else if (ref === -13 || ref === -14) {
      raw = r.take(ref === -13 ? 4 : 8);
      let bits = 0n; for (let i = raw.length - 1; i >= 0; i--) bits = bits << 8n | BigInt(raw[i]);
      const frac = ref === -13 ? 23n : 52n, exponent = ref === -13 ? 8n : 11n;
      if (((bits >> frac) & ((1n << exponent) - 1n)) === (1n << exponent) - 1n && (bits & ((1n << frac) - 1n)) !== 0n)
        raw = Uint8Array.from(ref === -13 ? [0, 0, 192, 127] : [0, 0, 0, 0, 0, 0, 248, 127]);
    } else if (ref === -15) { const bytes = r.text(); const w = new Writer(); w.int(bytes.length); w.add(bytes); raw = w.finish(); }
    else if (ref === -24) {
      if (r.byte() !== 1) fail('opaque principal');
      const n = r.length(); if (n > 29) fail('principal length exceeds 29');
      raw = Uint8Array.from([1, n, ...r.take(n)]);
    } else return fail('unsupported primitive');
    return { op: ref, raw };
  };
  const values = roots.map(ref => read(ref, 0));
  if (r.pos !== data.length) fail('trailing payload');

  const shapes = new Shapes();
  const ty = shapes.type.bind(shapes), join = shapes.join.bind(shapes);
  const emptyType = shapes.empty;
  const shape = (v: Val, depth = 0): Ty => {
    if (depth > MAX_DEPTH) fail('type nesting limit');
    if (v.op === -18) return ty(-18, [], [shape(v.items![0], depth + 1)]);
    if (v.op === -19) return ty(-19, [], [v.binary ? ty(-5) : v.items!.reduce((a, v) => join(a, shape(v, depth + 1)), emptyType)]);
    if (v.op === -20 || v.op === -21) return ty(v.op, v.labels!, v.items!.map(v => shape(v, depth + 1)));
    return ty(v.op);
  };
  const types = values.map(v => shape(v)), w = new Writer();
  writeHeader(w, types);
  const emit = (v: Val, t: Ty, depth = 0): void => {
    if (depth > MAX_DEPTH) fail('emitted value nesting limit');
    if (t.op === -18) { w.byte(v.op === -1 ? 0 : 1); if (v.op !== -1) emit(v.op === -18 ? v.items![0] : v, t.children[0], depth + 1); }
    else if (t.op === -19 && v.op === -19) {
      if (v.binary) { if (t.children[0].op !== -5) fail('invalid binary shape'); w.int(v.raw!.length); w.add(v.raw!); }
      else { w.int(v.items!.length); v.items!.forEach(c => emit(c, t.children[0], depth + 1)); }
    }
    else if (t.op === -20 && v.op === -20) {
      const fields = new Map(v.labels!.map((k, i) => [k, v.items![i]])); t.labels.forEach((k, i) => emit(fields.get(k) ?? nullVal, t.children[i], depth + 1));
    } else if (t.op === -21 && v.op === -21) {
      const index = t.labels.indexOf(v.labels![0]); if (index < 0) fail('missing derived variant');
      w.int(index); emit(v.items![0], t.children[index], depth + 1);
    } else if (v.op === t.op && ![-17, -18, -19, -20, -21].includes(t.op)) { if (v.raw) w.add(v.raw); }
    else fail('cannot emit at derived type');
  };
  values.forEach((v, i) => emit(v, types[i]));
  return w.finish();
}

function compareBytes(a: Uint8Array, b: Uint8Array): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}
