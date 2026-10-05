/** Shared, SDK-free cx shape and wire machinery. */
export const MAX_BYTES = 32 * 1024 * 1024;
export const MAX_ITEMS = 2_000_000;
export const MAX_TYPES = 1024;
export const MAX_DEPTH = 256;
export const MAX_INTEGER_BYTES = 4096;
export function fail(message: string): never { throw new Error(`CandidX: ${message}`); }

const textEncoder = new TextEncoder();

export function textLength(text: string): number {
  let length = 0;
  for (let i = 0; i < text.length; i++) {
    const unit = text.charCodeAt(i);
    if (unit < 0x80) length++;
    else if (unit < 0x800) length += 2;
    else if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = text.charCodeAt(i + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) fail('text contains an unpaired UTF-16 surrogate');
      length += 4; i++;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) fail('text contains an unpaired UTF-16 surrogate');
    else length += 3;
    if (length > MAX_ITEMS) fail('text byte length limit');
  }
  return length;
}

export class Writer {
  private data = new Uint8Array(256);
  private view = new DataView(this.data.buffer);
  private size = 0;
  private reserve(extra: number): number {
    const length = this.size + extra;
    if (length > MAX_BYTES) fail('output byte limit');
    if (length > this.data.length) {
      const next = new Uint8Array(Math.min(MAX_BYTES, Math.max(length, this.data.length * 2)));
      next.set(this.data.subarray(0, this.size)); this.data = next;
      this.view = new DataView(next.buffer);
    }
    const start = this.size;
    this.size = length;
    return start;
  }
  add(bytes: Uint8Array | readonly number[]): void {
    const offset = this.reserve(bytes.length);
    this.data.set(bytes, offset);
  }
  byte(n: number): void { const offset = this.reserve(1); this.data[offset] = n; }
  int(n: number | bigint, signed = false): void {
    if (typeof n === 'number') {
      for (;;) {
        const byte = ((n % 128) + 128) % 128;
        n = Math.floor(n / 128);
        const done = signed ? (n === 0 && byte < 64) || (n === -1 && byte >= 64) : n === 0;
        this.byte(byte | (done ? 0 : 128));
        if (done) return;
      }
    }
    for (;;) {
      const byte = Number(n & 127n);
      n >>= 7n;
      const done = signed ? (n === 0n && byte < 64) || (n === -1n && byte >= 64) : n === 0n;
      this.byte(byte | (done ? 0 : 128));
      if (done) return;
    }
  }
  fixed(n: number | bigint, bits: number): void {
    const offset = this.reserve(bits / 8);
    if (bits === 8) this.view.setUint8(offset, Number(n));
    else if (bits === 16) this.view.setUint16(offset, Number(n), true);
    else if (bits === 32) this.view.setUint32(offset, Number(n), true);
    else this.view.setBigUint64(offset, BigInt(n), true);
  }
  float(n: number, bits: number): void {
    const offset = this.reserve(bits / 8);
    if (bits === 32) {
      if (Number.isNaN(n)) this.view.setUint32(offset, 0x7fc00000, true);
      else this.view.setFloat32(offset, n, true);
    } else {
      if (Number.isNaN(n)) this.view.setBigUint64(offset, 0x7ff8000000000000n, true);
      else this.view.setFloat64(offset, n, true);
    }
  }
  text(text: string, length = textLength(text)): void {
    this.int(length);
    const offset = this.reserve(length);
    textEncoder.encodeInto(text, this.data.subarray(offset, offset + length));
  }
  finish(): Uint8Array { return this.data.slice(0, this.size); }
}

export type Shape = { op: number; labels: readonly number[]; children: readonly Shape[]; id: number };
const compound = (op: number): boolean => op >= -21 && op <= -18;
const noLabels: readonly number[] = [], noChildren: readonly Shape[] = [];

export class Shapes {
  private readonly shapes = new Map<string, Shape>();
  private readonly scalars = new Map<number, Shape>();
  private readonly joins = new Map<string, Shape>();
  readonly null = this.type(-1);
  readonly empty = this.type(-17);
  type(op: number, labels: readonly number[] = noLabels, children: readonly Shape[] = noChildren): Shape {
    if (!compound(op)) {
      const old = this.scalars.get(op);
      if (old) return old;
    }
    const key = `${op}/${labels.join(',')}/${children.map(c => c.id).join(',')}`;
    let t = this.shapes.get(key);
    if (!t) {
      t = { op, labels, children, id: this.shapes.size };
      this.shapes.set(key, t);
      if (!compound(op)) this.scalars.set(op, t);
    }
    return t;
  }
  join(a: Shape, b: Shape, depth = 0): Shape {
    if (depth > MAX_DEPTH) fail('type nesting limit');
    if (a === b || b.op === -17) return a;
    if (a.op === -17) return b;
    if (a.op === -1) return b.op === -18 ? b : this.type(-18, [], [b]);
    if (b.op === -1) return a.op === -18 ? a : this.type(-18, [], [a]);
    const key = a.id < b.id ? `${a.id},${b.id}` : `${b.id},${a.id}`;
    const old = this.joins.get(key);
    if (old) return old;
    let joined: Shape;
    if (a.op === -18 || b.op === -18) {
      joined = this.type(-18, [], [this.join(a.op === -18 ? a.children[0] : a,
        b.op === -18 ? b.children[0] : b, depth + 1)]);
    } else if (a.op === -19 && b.op === -19) {
      joined = this.type(-19, [], [this.join(a.children[0], b.children[0], depth + 1)]);
    } else {
      if (a.op !== b.op || (a.op !== -20 && a.op !== -21)) return fail('incompatible value shapes');
      const labels: number[] = [], children: Shape[] = [];
      let ai = 0, bi = 0;
      while (ai < a.labels.length || bi < b.labels.length) {
        const al = a.labels[ai] ?? Infinity, bl = b.labels[bi] ?? Infinity;
        const label = Math.min(al, bl);
        const ac = al === label ? a.children[ai++] : undefined;
        const bc = bl === label ? b.children[bi++] : undefined;
        labels.push(label);
        children.push(a.op === -21 && !ac ? bc! : a.op === -21 && !bc ? ac! :
          this.join(ac ?? this.null, bc ?? this.null, depth + 1));
      }
      joined = this.type(a.op, labels, children);
    }
    this.joins.set(key, joined);
    return joined;
  }
}

/** Reachable finite shapes are numbered before their children, in root order. */
export function writeHeader(w: Writer, types: readonly Shape[]): void {
  const table: Shape[] = [], indices = new Map<Shape, number>();
  const intern = (t: Shape, depth = 0): number => {
    if (!compound(t.op)) return t.op;
    if (depth > MAX_DEPTH) fail('type nesting limit');
    const old = indices.get(t); if (old !== undefined) return old;
    if (table.length >= MAX_TYPES) return fail('output type-table limit');
    const id = table.length; indices.set(t, id); table.push(t);
    for (const child of t.children) intern(child, depth + 1);
    return id;
  };
  const args = types.map(t => intern(t));
  w.add([68, 73, 68, 76]); w.int(table.length);
  for (const t of table) {
    w.int(t.op, true);
    if (t.op === -18 || t.op === -19) w.int(intern(t.children[0]), true);
    else {
      w.int(t.labels.length);
      for (let i = 0; i < t.labels.length; i++) { w.int(t.labels[i]); w.int(intern(t.children[i]), true); }
    }
  }
  w.int(args.length);
  for (const arg of args) w.int(arg, true);
}
