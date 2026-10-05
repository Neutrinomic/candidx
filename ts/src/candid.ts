import { IDL, idlLabelToId } from '@icp-sdk/core/candid';
import { MAX_DEPTH, MAX_INTEGER_BYTES, MAX_ITEMS, Shape, Shapes, Writer, fail, textLength, writeHeader } from './codec.js';
export { decodeCompatible } from './decode.js';

class Kinds extends IDL.Visitor<void, number> {
  visitType<T>(_type: IDL.Type<T>): number { return fail('unsupported or decoding-only schema'); }
  visitEmpty(): number { return -17; }
  visitBool(): number { return -2; }
  visitNull(): number { return -1; }
  visitReserved(): number { return -16; }
  visitText(): number { return -15; }
  visitInt(): number { return -4; }
  visitNat(): number { return -3; }
  visitFloat(type: IDL.FloatClass): number {
    if (type._bits !== 32 && type._bits !== 64) fail('invalid float width');
    return type._bits === 32 ? -13 : -14;
  }
  visitFixedInt(type: IDL.FixedIntClass): number { return this.fixed(type._bits, -9); }
  visitFixedNat(type: IDL.FixedNatClass): number { return this.fixed(type._bits, -5); }
  private fixed(bits: number, base: number): number {
    if (![8, 16, 32, 64].includes(bits)) fail('invalid integer width');
    return base - (Math.log2(bits) - 3);
  }
  visitPrincipal(): number { return -24; }
  visitOpt<T>(_type: IDL.OptClass<T>): number { return -18; }
  visitVec<T>(_type: IDL.VecClass<T>): number { return -19; }
  visitRecord(): number { return -20; }
  visitTuple<T extends unknown[]>(_type: IDL.TupleClass<T>): number { return -20; }
  visitVariant(): number { return -21; }
  visitFunc(): number { return -22; }
  visitService(): number { return -23; }
}

type Field = { key: string | number; id: number; type: IDL.Type; optional: boolean };
const kinds = new Kinds();
const natLimit = 1n << BigInt(MAX_INTEGER_BYTES * 7);
const intLimit = natLimit >> 1n;
const fixedLimits = [8, 16, 32, 64].map(bits => ({
  unsigned: 1n << BigInt(bits), signed: 1n << BigInt(bits - 1),
}));
type Vector = ArrayLike<unknown>;
type IntegerView = { bytes: number; create(buffer: ArrayBufferLike, offset: number, length: number): Vector };
const integerViews: Record<string, IntegerView> = {
  Int8Array: { bytes: 1, create: (b, o, n) => new Int8Array(b, o, n) },
  Uint8Array: { bytes: 1, create: (b, o, n) => new Uint8Array(b, o, n) },
  Uint8ClampedArray: { bytes: 1, create: (b, o, n) => new Uint8ClampedArray(b, o, n) },
  Int16Array: { bytes: 2, create: (b, o, n) => new Int16Array(b, o, n) },
  Uint16Array: { bytes: 2, create: (b, o, n) => new Uint16Array(b, o, n) },
  Int32Array: { bytes: 4, create: (b, o, n) => new Int32Array(b, o, n) },
  Uint32Array: { bytes: 4, create: (b, o, n) => new Uint32Array(b, o, n) },
  BigInt64Array: { bytes: 8, create: (b, o, n) => new BigInt64Array(b, o, n) },
  BigUint64Array: { bytes: 8, create: (b, o, n) => new BigUint64Array(b, o, n) },
};
// These native getters identify the actual storage even when an instance's
// prototype, Symbol.toStringTag, length, or BYTES_PER_ELEMENT has been spoofed.
const typedArrayPrototype = Object.getPrototypeOf(Uint8Array.prototype);
const nativeTag = Object.getOwnPropertyDescriptor(typedArrayPrototype, Symbol.toStringTag)!.get!;
const nativeLength = Object.getOwnPropertyDescriptor(typedArrayPrototype, 'length')!.get!;
const nativeBuffer = Object.getOwnPropertyDescriptor(typedArrayPrototype, 'buffer')!.get!;
const nativeOffset = Object.getOwnPropertyDescriptor(typedArrayPrototype, 'byteOffset')!.get!;
const nativeBytes = Object.getOwnPropertyDescriptor(typedArrayPrototype, 'byteLength')!.get!;
type NativeSpan = { name: string; type: IntegerView; buffer: ArrayBufferLike; offset: number; length: number; bytes: number };

function nativeSpan(value: unknown): NativeSpan {
  if (!ArrayBuffer.isView(value)) fail('native vector must be an integer typed array');
  const name: string | undefined = nativeTag.call(value), type = name && integerViews[name];
  if (!name || !type) fail('native vector must be an integer typed array');
  return { name, type, buffer: nativeBuffer.call(value), offset: nativeOffset.call(value),
    length: nativeLength.call(value), bytes: nativeBytes.call(value) };
}

function trustedView(span: NativeSpan): Vector {
  try { return span.type.create(span.buffer, span.offset, span.length); }
  catch { return fail('invalid or detached native vector'); }
}

/** Metadata comes from the public Type visitor; recursive aliases have no wire constructor. */
class Schemas {
  private readonly aliases = new Map<IDL.Type, IDL.Type>();
  private readonly kinds = new Map<IDL.Type, number>();
  private readonly fieldCache = new Map<IDL.Type, readonly Field[]>();
  unwrap(type: IDL.Type): IDL.Type {
    if (!(type instanceof IDL.RecClass)) return type;
    const old = this.aliases.get(type);
    if (old) return old;
    const path: IDL.Type[] = [];
    let actual: IDL.Type = type;
    while (actual instanceof IDL.RecClass) {
      if (path.includes(actual) || path.length > MAX_DEPTH) fail('recursive alias cycle or nesting limit');
      path.push(actual);
      const next = actual.getType();
      if (!next) fail('uninitialized recursive schema');
      actual = next;
    }
    for (const alias of path) this.aliases.set(alias, actual);
    return actual;
  }
  kind(type: IDL.Type): number {
    const actual = this.unwrap(type), old = this.kinds.get(actual);
    if (old !== undefined) return old;
    const op = actual.accept(kinds, undefined);
    this.kinds.set(actual, op);
    return op;
  }
  fields(type: IDL.Type, fields: Array<[string, IDL.Type]>): readonly Field[] {
    const old = this.fieldCache.get(type);
    if (old) return old;
    if (fields.length > MAX_ITEMS) fail('collection length limit');
    const result = fields.map(([key, child]) => ({ key, id: idlLabelToId(key), type: child,
      optional: this.kind(child) === -18 })).sort((a, b) => a.id - b.id);
    for (let i = 1; i < result.length; i++)
      if (result[i].id === result[i - 1].id) fail('duplicate numeric field ID');
    this.fieldCache.set(type, result);
    return result;
  }
  tuple(type: IDL.Type, components: IDL.Type[]): readonly Field[] {
    const old = this.fieldCache.get(type);
    if (old) return old;
    if (components.length > MAX_ITEMS) fail('collection length limit');
    const result = components.map((child, id) => ({ key: id, id, type: child,
      optional: this.kind(child) === -18 }));
    this.fieldCache.set(type, result);
    return result;
  }
}

function integer(value: unknown): bigint {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number' && Number.isInteger(value)) return BigInt(value);
  return fail('invalid integer value');
}

function validatePrimitive(op: number, value: unknown): Uint8Array | number | undefined {
  if (op === -17) fail('empty has no values');
  if (op === -1 && value !== null) fail('invalid null value');
  if (op === -2 && typeof value !== 'boolean') fail('invalid bool value');
  if (op === -15) {
    if (typeof value !== 'string') fail('invalid text value');
    return textLength(value);
  }
  if (op === -3 || op === -4) {
    const n = integer(value), bound = op === -3 ? natLimit : intLimit;
    if ((op === -3 && n < 0n) || n >= bound || (op === -4 && n < -bound)) fail('integer range or length limit');
  } else if (op >= -12 && op <= -5) {
    const limits = fixedLimits[(-op - 5) % 4], signed = op <= -9;
    const bound = signed ? limits.signed : limits.unsigned;
    const min = signed ? -bound : 0n;
    if (typeof value === 'number') {
      if (!Number.isInteger(value) || value < Number(min) || value >= Number(bound)) fail('fixed integer range');
    } else if (typeof value !== 'bigint' || value < min || value >= bound) fail('fixed integer range');
  } else if ((op === -13 || op === -14) && typeof value !== 'number' && !(value instanceof Number)) {
    fail('invalid float value');
  } else if (op === -24) {
    if (!value || typeof value !== 'object' || !('_isPrincipal' in value) || !value._isPrincipal ||
      !('toUint8Array' in value) || typeof value.toUint8Array !== 'function') fail('invalid public principal');
    const span = nativeSpan(value.toUint8Array());
    if (span.name !== 'Uint8Array') fail('invalid public principal bytes');
    if (span.length > 29) fail('principal length exceeds 29');
    const bytes = trustedView(span) as Uint8Array;
    return bytes;
  }
}

function option(value: unknown): unknown[] {
  if (!Array.isArray(value) || value.length > 1) fail('option must be [] or [value]');
  return value;
}

function vector(value: unknown, op: number): Vector {
  if (Array.isArray(value)) return value;
  const span = nativeSpan(value);
  if (op < -12 || op > -5 || span.type.bytes !== 2 ** ((-op - 5) % 4))
    fail('vector requires a matching integer typed-array width');
  const view = value as ArrayBufferView & { length: number; BYTES_PER_ELEMENT: number };
  if (view.BYTES_PER_ELEMENT !== span.type.bytes || view.length !== span.length ||
    view.buffer !== span.buffer || view.byteOffset !== span.offset || view.byteLength !== span.bytes)
    fail('spoofed native vector metadata');
  return trustedView(span);
}

function record(value: unknown): Record<string | number, unknown> {
  if (value === null || typeof value !== 'object') fail('invalid record value');
  return value as Record<string | number, unknown>;
}

function field(value: Record<string | number, unknown>, key: string | number): unknown {
  if (!Object.hasOwn(value, key)) fail(`record is missing field ${key}`);
  return value[key];
}

function selected(value: unknown, fields: readonly Field[]): Field {
  const v = record(value), labels = Object.keys(v);
  if (labels.length !== 1) fail('variant must have exactly one selected case');
  const selected = fields.find(f => f.key === labels[0]);
  return selected ?? fail('unknown selected variant case');
}

/** Visits typed values once to validate them and retain only the shapes actually used. */
class UsedShapes extends IDL.Visitor<unknown, Shape> {
  readonly shapes = new Shapes();
  private steps = 0;
  private depth = 0;
  constructor(readonly schemas: Schemas) { super(); }
  private consume(count: number, depth: number): void {
    this.steps += count;
    if (this.steps > MAX_ITEMS || depth > MAX_DEPTH) fail('value traversal limit');
  }
  shape(type: IDL.Type, value: unknown, depth = 0): Shape {
    this.consume(1, depth);
    const previous = this.depth; this.depth = depth;
    try { return this.schemas.unwrap(type).accept(this, value); }
    finally { this.depth = previous; }
  }
  visitType<T>(_type: IDL.Type<T>): Shape { return fail('unsupported or decoding-only schema'); }
  visitPrimitive<T>(type: IDL.PrimitiveType<T>, value: unknown): Shape {
    const op = this.schemas.kind(type);
    validatePrimitive(op, value);
    return this.shapes.type(op);
  }
  visitOpt<T>(_type: IDL.OptClass<T>, child: IDL.Type<T>, value: unknown): Shape {
    const v = option(value);
    return v.length ? this.shapes.type(-18, [], [this.shape(child, v[0], this.depth + 1)]) : this.shapes.null;
  }
  visitVec<T>(_type: IDL.VecClass<T>, child: IDL.Type<T>, value: unknown): Shape {
    const op = this.schemas.kind(child), v = vector(value, op), length = v.length;
    if (length > MAX_ITEMS) fail('collection length limit');
    if (op === -5) {
      this.consume(length, length ? this.depth + 1 : this.depth);
      if (!(v instanceof Uint8Array)) for (let i = 0; i < length; i++) {
        const n = integer(v[i]); if (n < 0n || n > 255n) fail('fixed integer range');
      }
      return this.shapes.type(-19, [], [this.shapes.type(-5)]);
    }
    let joined = this.shapes.empty;
    for (let i = 0; i < length; i++) joined = this.shapes.join(joined, this.shape(child, v[i], this.depth + 1));
    return this.shapes.type(-19, [], [joined]);
  }
  private fields(fields: readonly Field[], value: Record<string | number, unknown>): Shape {
    const labels: number[] = [], children: Shape[] = [];
    for (const f of fields) {
      const child = this.shape(f.type, field(value, f.key), this.depth + 1);
      if (child.op !== -1 || !f.optional) { labels.push(f.id); children.push(child); }
    }
    return this.shapes.type(-20, labels, children);
  }
  visitRecord(type: IDL.RecordClass, fields: Array<[string, IDL.Type]>, value: unknown): Shape {
    return this.fields(this.schemas.fields(type, fields), record(value));
  }
  visitTuple<T extends unknown[]>(type: IDL.TupleClass<T>, components: IDL.Type[], value: unknown): Shape {
    if (!Array.isArray(value) || value.length < components.length) fail('invalid tuple value');
    return this.fields(this.schemas.tuple(type, components), record(value));
  }
  visitVariant(type: IDL.VariantClass, fields: Array<[string, IDL.Type]>, value: unknown): Shape {
    const f = selected(value, this.schemas.fields(type, fields));
    return this.shapes.type(-21, [f.id], [this.shape(f.type, record(value)[f.key], this.depth + 1)]);
  }
  visitFunc(): Shape { return fail('function/service values outside data profile'); }
  visitService(): Shape { return fail('function/service values outside data profile'); }
}

/** Writes original typed values at their joined wire shapes, without a payload/value tree. */
class EmitValues extends IDL.Visitor<unknown, Shape> {
  private target!: Shape;
  private depth = 0;
  private steps = 0;
  constructor(private readonly schemas: Schemas, private readonly shapes: Shapes,
    private readonly writer: Writer) { super(); }
  private consume(count: number, depth: number): void {
    this.steps += count;
    if (this.steps > MAX_ITEMS || depth > MAX_DEPTH) fail('emitted value traversal limit');
  }
  emit(type: IDL.Type, value: unknown, target: Shape, depth = 0): Shape {
    this.consume(1, depth);
    const op = this.schemas.kind(type);
    if (target.op === -18 && op !== -18) {
      this.writer.byte(op === -1 ? 0 : 1);
      return op === -1 ? this.shapes.null : this.emit(type, value, target.children[0], depth + 1);
    }
    const previous = this.target, previousDepth = this.depth;
    this.target = target; this.depth = depth;
    try { return this.schemas.unwrap(type).accept(this, value); }
    finally { this.target = previous; this.depth = previousDepth; }
  }
  private require(op: number): Shape {
    if (this.target.op !== op) fail('cannot emit at derived type');
    return this.target;
  }
  visitType<T>(_type: IDL.Type<T>): Shape { return fail('unsupported or decoding-only schema'); }
  visitPrimitive<T>(type: IDL.PrimitiveType<T>, value: unknown): Shape {
    const op = this.schemas.kind(type); this.require(op);
    const payload = validatePrimitive(op, value);
    if (op === -1 || op === -16) return this.shapes.type(op);
    if (op === -2) this.writer.byte(value ? 1 : 0);
    else if (op === -3 || op === -4) this.writer.int(integer(value), op === -4);
    else if (op >= -12 && op <= -5) this.writer.fixed(value as number | bigint, 2 ** ((-op - 5) % 4 + 3));
    else if (op === -13 || op === -14) this.writer.float(Number(value), op === -13 ? 32 : 64);
    else if (op === -15) this.writer.text(value as string, payload as number);
    else if (op === -24) {
      const bytes = payload as Uint8Array;
      this.writer.byte(1); this.writer.int(bytes.length); this.writer.add(bytes);
    } else fail('empty has no values');
    return this.shapes.type(op);
  }
  visitOpt<T>(_type: IDL.OptClass<T>, child: IDL.Type<T>, value: unknown): Shape {
    const v = option(value);
    if (!v.length) {
      if (this.target.op === -18) this.writer.byte(0);
      else this.require(-1);
      return this.shapes.null;
    } else {
      const target = this.require(-18);
      this.writer.byte(1);
      return this.shapes.type(-18, [], [this.emit(child, v[0], target.children[0], this.depth + 1)]);
    }
  }
  visitVec<T>(_type: IDL.VecClass<T>, child: IDL.Type<T>, value: unknown): Shape {
    const target = this.require(-19), op = this.schemas.kind(child), v = vector(value, op), length = v.length;
    if (length > MAX_ITEMS) fail('collection length limit');
    this.writer.int(length);
    if (op === -5) {
      if (target.children[0].op !== -5) fail('invalid binary shape');
      this.consume(length, length ? this.depth + 1 : this.depth);
      if (v instanceof Uint8Array) this.writer.add(v);
      else for (let i = 0; i < length; i++) {
        const n = integer(v[i]); if (n < 0n || n > 255n) fail('fixed integer range');
        this.writer.byte(Number(n));
      }
      return this.shapes.type(-19, [], [this.shapes.type(-5)]);
    }
    let joined = this.shapes.empty;
    for (let i = 0; i < length; i++) joined = this.shapes.join(joined,
      this.emit(child, v[i], target.children[0], this.depth + 1));
    return this.shapes.type(-19, [], [joined]);
  }
  private fields(fields: readonly Field[], value: Record<string | number, unknown>): Shape {
    const target = this.require(-20);
    const labels: number[] = [], children: Shape[] = [];
    let index = 0;
    for (const f of fields) {
      const v = field(value, f.key);
      if (f.id !== target.labels[index]) {
        if (f.id > (target.labels[index] ?? Infinity) || !f.optional || option(v).length)
          fail('typed value shape changed during encoding');
        continue;
      }
      const child = this.emit(f.type, v, target.children[index++], this.depth + 1);
      if (child.op !== -1 || !f.optional) { labels.push(f.id); children.push(child); }
    }
    if (index !== target.labels.length) fail('missing derived record field');
    return this.shapes.type(-20, labels, children);
  }
  visitRecord(type: IDL.RecordClass, fields: Array<[string, IDL.Type]>, value: unknown): Shape {
    return this.fields(this.schemas.fields(type, fields), record(value));
  }
  visitTuple<T extends unknown[]>(type: IDL.TupleClass<T>, components: IDL.Type[], value: unknown): Shape {
    if (!Array.isArray(value) || value.length < components.length) fail('invalid tuple value');
    return this.fields(this.schemas.tuple(type, components), record(value));
  }
  visitVariant(type: IDL.VariantClass, fields: Array<[string, IDL.Type]>, value: unknown): Shape {
    const target = this.require(-21), f = selected(value, this.schemas.fields(type, fields));
    const index = target.labels.indexOf(f.id);
    if (index < 0) fail('missing derived variant case');
    this.writer.int(index);
    return this.shapes.type(-21, [f.id], [this.emit(f.type, record(value)[f.key], target.children[index], this.depth + 1)]);
  }
  visitFunc(): Shape { return fail('function/service values outside data profile'); }
  visitService(): Shape { return fail('function/service values outside data profile'); }
}

/** Direct cx encoding from ordinary SDK schemas and values. No ordinary message is built. */
export function encodeCanonical(types: readonly IDL.Type[], values: readonly unknown[]): Uint8Array {
  const count = types.length;
  if (count !== values.length) fail('argument count mismatch');
  if (count > MAX_ITEMS) fail('collection length limit');
  const schemas = new Schemas(), used = new UsedShapes(schemas);
  const shapes = types.map((type, i) => used.shape(type, values[i]));
  const writer = new Writer();
  writeHeader(writer, shapes);
  const emitter = new EmitValues(schemas, used.shapes, writer);
  for (let i = 0; i < count; i++)
    if (emitter.emit(types[i], values[i], shapes[i]) !== shapes[i]) fail('typed value shape changed during encoding');
  return writer.finish();
}
