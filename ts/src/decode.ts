import { IDL, lebDecode, safeRead } from '@icp-sdk/core/candid';
import { CanonicalBytes, isCanonicalBytes, checkCanonicalBytes } from './canonical-bytes.js';
import { MAX_BYTES, fail } from './codec.js';

type Pipe = Parameters<IDL.TextClass['decodeValue']>[0];

// These expected-type objects are private to a single decode. SDK globals and
// caller-owned schemas remain unchanged, including recursive declarations.
class TextDecoderType extends IDL.TextClass {
  decodeValue(pipe: Pipe, wire: IDL.Type): string {
    this.checkType(wire);
    const length = lebDecode(pipe);
    if (length > 32n * 1024n * 1024n) throw new Error('CandidX: text byte limit');
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
      .decode(safeRead(pipe, Number(length)));
  }
}

class TupleDecoderType extends IDL.TupleClass<unknown[]> {
  decodeValue(pipe: Pipe, wire: IDL.Type): unknown[] {
    // A tuple is a numeric record. Apply ordinary missing-optional-field
    // coercion even when the wire record is sparse or shorter than the tuple.
    const record = IDL.RecordClass.prototype.decodeValue.call(this, pipe, wire);
    return this._components.map((_, i) => record[`_${i}_`]);
  }
}

class OptionalRecField extends IDL.OptClass<unknown> {
  constructor(private readonly rec: IDL.RecClass) { super(IDL.Reserved); }
  decodeValue(pipe: Pipe, wire: IDL.Type): [] | [unknown] {
    // RecordClass can recognize absence through OptClass. For present fields
    // delegate to the recursive schema, retaining the actual option nesting.
    return this.rec.decodeValue(pipe, wire) as [] | [unknown];
  }
}

class DecodingTypes extends IDL.Visitor<void, IDL.Type> {
  private readonly memo = new Map<IDL.Type, IDL.Type>();
  copy(type: IDL.Type): IDL.Type {
    if (isCanonicalBytes(type)) return CanonicalBytes;
    const original = decoderSources.get(type);
    if (original) return this.copy(original);
    if (type instanceof IDL.UnknownClass)
      throw new Error('CandidX: decoding-only IDL.Unknown is outside the original-schema decoder profile');
    const old = this.memo.get(type);
    if (old) return old;
    if (type instanceof IDL.RecClass) {
      const rec = IDL.Rec();
      this.memo.set(type, rec);
      const unfolded = type.getType();
      if (!unfolded) throw new Error('CandidX: uninitialized recursive schema');
      rec.fill(this.copy(unfolded) as IDL.ConstructType);
      return rec;
    }
    const result = type.accept(this, undefined);
    this.memo.set(type, result);
    return result;
  }
  private field(type: IDL.Type): IDL.Type {
    const copy = this.copy(type);
    if (!(copy instanceof IDL.RecClass)) return copy;
    let unfolded = type;
    const seen = new Set<IDL.Type>();
    while (unfolded instanceof IDL.RecClass && !seen.has(unfolded)) {
      seen.add(unfolded);
      const next = unfolded.getType();
      if (!next) throw new Error('CandidX: uninitialized recursive schema');
      unfolded = next;
    }
    return unfolded instanceof IDL.OptClass ? new OptionalRecField(copy) : copy;
  }
  visitType<T>(type: IDL.Type<T>): IDL.Type { return type; }
  visitText(): IDL.Type { return new TextDecoderType(); }
  visitOpt<T>(_type: IDL.OptClass<T>, child: IDL.Type<T>): IDL.Type { return IDL.Opt(this.copy(child)); }
  visitVec<T>(_type: IDL.VecClass<T>, child: IDL.Type<T>): IDL.Type { return IDL.Vec(this.copy(child)); }
  visitRecord(_type: IDL.RecordClass, fields: Array<[string, IDL.Type]>): IDL.Type {
    return IDL.Record(Object.fromEntries(fields.map(([key, child]) => [key, this.field(child)])));
  }
  visitTuple<T extends unknown[]>(_type: IDL.TupleClass<T>, components: IDL.Type[]): IDL.Type {
    return new TupleDecoderType(components.map(child => this.field(child)));
  }
  visitVariant(_type: IDL.VariantClass, fields: Array<[string, IDL.Type]>): IDL.Type {
    return IDL.Variant(Object.fromEntries(fields.map(([key, child]) => [key, this.copy(child)])));
  }
}

const decoderSources = new WeakMap<IDL.Type, IDL.Type>();

class ReturnDecoderType<T> extends IDL.UnknownClass {
  constructor(private readonly source: IDL.Type<T>, private readonly decoder: IDL.Type<T>) {
    super();
    decoderSources.set(this, source);
  }
  get name(): string { return `CandidX.Decoder(${this.source.name})`; }
  decodeValue(pipe: Pipe, wire: IDL.Type): T {
    try {
      const value = this.decoder.decodeValue(pipe, wire);
      checkCanonicalBytes(pipe);
      return value;
    } catch (error) {
      checkCanonicalBytes(pipe);
      throw error;
    }
  }
}

/** Prepare a return type for normal SDK/actor decoding with compatibility
 * fixes and fail-closed CanonicalBytes extraction. This type is decode-only.
 */
export function decoderType<T>(type: IDL.Type<T>): IDL.Type<T> {
  return new ReturnDecoderType(type, new DecodingTypes().copy(type));
}

/** Ordinary typed SDK decoding with three tested 6.1.0 compatibility fixes:
 * preserve leading text BOMs and synthesize omitted optional tuple/Rec fields.
 * Ordinary fields are projected. CanonicalBytes subtrees retain the actual
 * wire data as standalone canonical messages for verification before decoding.
 */
export function decodeCompatible(types: IDL.Type[], bytes: Uint8Array): unknown[] {
  if (bytes.byteLength > MAX_BYTES) fail('input byte limit');
  // SDK 6.1.0's Pipe constructor starts at buffer offset zero. Normalize a
  // sliced Uint8Array/Node Buffer before its header parser sees the bytes.
  const input = bytes.byteOffset === 0 ? bytes : Uint8Array.from(bytes);
  const visitor = new DecodingTypes();
  return IDL.decode(types.map(type => new ReturnDecoderType(type, visitor.copy(type))), input);
}
