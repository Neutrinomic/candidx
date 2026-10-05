import { IDL, idlLabelToId } from '@icp-sdk/core/candid';
import { MAX_ITEMS, MAX_TYPES, fail } from './codec.js';
import { canonicalizePayload, WireNode } from './wire.js';

type Pipe = Parameters<IDL.Type['decodeValue']>[0];
const primitiveIds: Readonly<Record<string, number>> = {
  null: -1, bool: -2, nat: -3, int: -4,
  nat8: -5, nat16: -6, nat32: -7, nat64: -8,
  int8: -9, int16: -10, int32: -11, int64: -12,
  float32: -13, float64: -14, text: -15, reserved: -16, empty: -17, principal: -24,
};

// The SDK supplies its parsed wire type, not the application's expected type.
// Intern it once per message; every element of a vector can reuse the graph.
class WireTypes extends IDL.Visitor<void, WireNode> {
  readonly nodes: WireNode[] = [];
  private readonly refs = new Map<IDL.Type, number>();
  ref(type: IDL.Type): number {
    const old = this.refs.get(type);
    if (old !== undefined) return old;
    const aliases: IDL.Type[] = [];
    let actual = type;
    while (actual instanceof IDL.RecClass) {
      if (aliases.includes(actual)) fail('recursive wire alias cycle');
      aliases.push(actual);
      const next = actual.getType();
      if (!next) fail('uninitialized wire type');
      actual = next;
    }
    let ref = this.refs.get(actual);
    if (ref === undefined) {
      if (actual instanceof IDL.ConstructType) {
        if (this.nodes.length >= MAX_TYPES) fail('input type-table limit');
        ref = this.nodes.length;
        this.nodes.push({ op: -17, labels: [], edges: [] });
        this.refs.set(actual, ref);
        for (const alias of aliases) this.refs.set(alias, ref);
        this.nodes[ref] = actual.accept(this, undefined);
      } else {
        ref = actual.accept(this, undefined).op;
        this.refs.set(actual, ref);
      }
    }
    for (const alias of aliases) this.refs.set(alias, ref);
    return ref;
  }
  visitType<T>(_type: IDL.Type<T>): WireNode { return fail('unsupported wire type'); }
  visitPrimitive<T>(type: IDL.PrimitiveType<T>): WireNode {
    const op = primitiveIds[type.name];
    if (op === undefined) return fail('unsupported wire primitive');
    return { op, labels: [], edges: [] };
  }
  visitOpt<T>(_type: IDL.OptClass<T>, child: IDL.Type<T>): WireNode {
    return { op: -18, labels: [], edges: [this.ref(child)] };
  }
  visitVec<T>(_type: IDL.VecClass<T>, child: IDL.Type<T>): WireNode {
    return { op: -19, labels: [], edges: [this.ref(child)] };
  }
  private fields(op: number, fields: Array<[string, IDL.Type]>): WireNode {
    if (fields.length > MAX_ITEMS) fail('collection length limit');
    const sorted = fields.map(([key, type]) => ({ id: idlLabelToId(key), type }))
      .sort((a, b) => a.id - b.id);
    for (let i = 1; i < sorted.length; i++)
      if (sorted[i - 1].id === sorted[i].id) fail('duplicate numeric field ID');
    return { op, labels: sorted.map(f => f.id), edges: sorted.map(f => this.ref(f.type)) };
  }
  visitRecord(_type: IDL.RecordClass, fields: Array<[string, IDL.Type]>): WireNode {
    return this.fields(-20, fields);
  }
  visitVariant(_type: IDL.VariantClass, fields: Array<[string, IDL.Type]>): WireNode {
    return this.fields(-21, fields);
  }
  // Reference signatures may occur in unused declarations. Actual values
  // still fail in the shared data-profile reader.
  visitFunc(): WireNode { return { op: -22, labels: [], edges: [] }; }
  visitService(): WireNode { return { op: -23, labels: [], edges: [] }; }
}

type Session = { wire: WireTypes; failure?: unknown };
const sessions = new WeakMap<Pipe, Session>();
const markers = new WeakSet<IDL.Type>();

export function isCanonicalBytes(type: IDL.Type): boolean { return markers.has(type); }

/** Re-throw failures that the SDK's optional-coercion fallback might catch. */
export function checkCanonicalBytes(pipe: Pipe): void {
  const session = sessions.get(pipe);
  if (session && 'failure' in session) throw session.failure;
}

class CanonicalBytesType extends IDL.UnknownClass {
  constructor() { super(); markers.add(this); }
  get name(): string { return 'CandidX.CanonicalBytes'; }
  decodeValue(pipe: Pipe, wireType: IDL.Type): Uint8Array {
    let session = sessions.get(pipe);
    if (!session) { session = { wire: new WireTypes() }; sessions.set(pipe, session); }
    checkCanonicalBytes(pipe);
    try {
      const root = session.wire.ref(wireType);
      // save/restore operate on the SDK's zero-copy reading view. Do not copy
      // the remaining reply for every transaction or decode JS values first.
      const view = pipe.save();
      const result = canonicalizePayload(view, session.wire.nodes, root);
      pipe.restore(view.subarray(result.consumed));
      return result.bytes;
    } catch (error) {
      session.failure = error;
      throw error;
    }
  }
}

const marker = new CanonicalBytesType();
Object.freeze(marker);
/** Decoder-only marker producing one complete, standalone cx Candid message.
 * Use in decodeCompatible, or wrap an actor return type with decoderType.
 */
export const CanonicalBytes: IDL.Type<Uint8Array> = marker;
