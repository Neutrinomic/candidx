# Appendix: Canonical Encoding of Data Values

Proposed addition alongside `spec/Candid.md`. This appendix defines an
optional data profile; it does not change the ordinary serialisation relation.
Its identifier is `cx` and is not part of the serialized bytes. The encoding
rules and conformance vectors form a fixed contract for this profile.

## Motivation

The ordinary encoding relation permits multiple byte representations of one
value, including different orders of type definitions and different declared
variant case sets. Hashing an arbitrary ordinary encoding therefore need not
give the same result across implementations or after a schema extension.

This profile chooses one representation based on the data being serialised.
Unused declarations do not contribute to it. In particular, extending a record
with an absent optional field or a variant with an unselected case does not
change the canonical representation of an existing value.

## Domain

The profile covers first-order data values: primitive data, public principals,
options, vectors, records, and variants. Recursive declarations are supported
when their values are finite. Service and function reference values and opaque
references are outside this profile.

Function and service references carry declared signatures that ordinary
decoders check. Those signatures cannot generally be erased while preserving
typed reconstruction, so the value-derived profile excludes these values.

Unused type declarations, including function and service declarations, do not
affect supported data. The type `empty` has no values, but can occur as the
element type of an empty vector. Blob and `vec nat8` denote the same binary
data kind, including when empty.

As in the main specification, record and variant labels denote numeric field
ids. Tuples follow the same rules as records with numeric labels. Primitive
numeric kinds remain distinct. Text is a sequence of UTF-8 bytes. The profile
does not identify arbitrary values related by a coercion that discards
information.

Field identity follows Candid's numeric label ids, including existing label
equivalences/collisions. The original spelling of a label is not recoverable
from its wire id and is not separately committed by this profile.

## Normalisation

Let `N` map a typed value to a finite normalised value tree. Types in this tree
carry primitive kinds and the binary data kind, but not unused schema
information. Option presence is retained; an absent option becomes `null`.
Normalise the complete actual value before any receiver-side projection. A
present field unknown to a receiver must not be discarded.

```
N(null : null)                      = null
N(v : reserved)                     = null : reserved
N(v : t)                            = v : t     // other primitive data
N(null : opt t)                     = null
N(opt v : opt t)                    = opt N(v : t)
N(vec {v1; ...; vN} : vec nat8)      = blob b    // b is the sequence of bytes
N(vec {v1; ...; vN} : vec t)         = vec {N(v1 : t); ...; N(vN : t)}
                                                // t is not nat8
N(record {ki = vi} : record {ki : ti})
  = record {ki = N(vi : ti) | not (vi = null and ti = opt t)}
N(variant {k = v} : variant {...; k : t; ...})
  = variant {k = N(v : t)}
```

The primitive rule for floats replaces every NaN with positive quiet NaN bits
`0x7fc00000` or `0x7ff8000000000000` for `float32` or `float64`, respectively.
All other float bits, including the sign of zero, subnormals, and infinities,
are retained. Integer values remain exact and their kinds remain distinct.
Text bytes are retained without Unicode normalisation.

Record fields are identified by their numeric Candid field ids; original
field-name strings do not appear in the normalised tree. Only absent optional
record fields are omitted. Explicit `null` and `reserved` fields are retained,
as are null variant payloads. The scalar kind `reserved` is retained, though
its ignored value carries no data. Vector elements and arguments retain their
length and order. A null argument is not omitted.

In particular, `null`, `opt null`, and `opt (opt null)` are distinct normalised
values. A present option containing an absent option does not disappear from
a record.

## Value-Derived Wire Type

Let `P(w)` denote the finite wire type derived from the normalised tree:

```
P(null)                = null
P(v : t)               = t
P(opt w)               = opt P(w)
P(record {ki = wi})    = record {ki : P(wi)}
P(variant {k = w})     = variant {k : P(w)}
P(blob b)              = vec nat8
P(vec {})              = vec empty   // non-binary vectors
P(vec {w1; ...; wN})   = vec J(P(w1), ..., P(wN))
```

The ordinary type `vec nat8` denotes the binary data kind in this profile,
including an empty blob. Other empty vectors erase their element type. The
nonempty-vector rule applies to non-binary vectors.

`J` is a partial, deterministic structural join on the finite types produced by
normalisation. Apply the following rules in order, also allowing their
arguments to be exchanged:

* `J(T, T) = T` and `J(empty, T) = J(T, empty) = T`.
* `J(null, opt T) = opt T`. For non-null, non-empty, non-option `T`,
  `J(null, T) = opt T`.
* `J(opt A, opt B) = opt J(A, B)`.
* For non-null, non-option `B`, `J(opt A, B) = opt J(A, B)`.
* `J(vec A, vec B) = vec J(A, B)`.
* Record joins use the union of field ids. A field missing from one record
  contributes type `null`; the two types at each field are joined recursively.
  An optional field absent from every value has already been omitted by `N`.
* Variant joins use the union of selected field ids. If a case occurs in both
  types, join its payload types recursively. If a case occurs in only one
  type, retain that payload type.
* All remaining pairs have no join. In particular, distinct scalar kinds and
  distinct outer constructors must not be joined by substituting `reserved`
  or by a failing optional coercion that would discard data.

The join of a vector's element types is folded from left to right in element
order. A uniformly typed Candid vector of supported values has the required
joins. Inputs for which a required join does not exist are rejected.

For example, normalised elements
`record {a = "x"}` and `record {a = "y"; b = opt "z"}` share the wire type
`record {a : text; b : opt text}`. The first element emits optional absence at
field `b`, and the second emits optional presence. This join is defined by the
rules above, rather than by the full Candid subtype relation with its lossy
optional conversions.

## Canonical Serialisation

Let `C(v* : t*)` be the canonical representation. Compute `w* = N(v* : t*)`
and `P(w*)` pointwise, and serialise `w*` at those types using the ordinary
encoding rules with the following choices:

* Fields are ordered by increasing field id.
* Structurally equal compound types have one type definition.
* Type definitions are numbered in depth-first preorder from argument roots;
  a definition receives its number before its children are visited.
* Only reachable definitions are included. A primitive uses its ordinary
  negative opcode, rather than a type-table entry.
* All integers, lengths, indices, and type references use shortest `leb128` or
  `sleb128` encodings.
* Missing fields under a vector's shared record type are emitted as absent.
  At a wire type `opt t`, a null value emits marker `0`; a present option
  emits marker `1` followed by its child at `t`. A non-null, non-option value
  at `opt t` emits marker `1` followed by that value at `t`.
* A variant index refers to its case's position in the derived type's sorted
  case list. Payloads, lengths, and indices are serialised anew; narrowing a
  type table must not leave its old payload unchanged.

Thus `C` remains an ordinary `DIDL` message. The type table describes actual
normalised data; no declared schema identifier is prepended or appended.
Normalising a canonical message again gives the same bytes.

For example:

```
C(record {a = "abc"} : record {a : text})
  = C(record {a = "abc"; b = null} : record {a : text; b : opt text})
  = 0x4449444c016c016171010003616263
```

Under the ordinary Candid coercion rules, decoding at the original supported
schema recovers the original value, modulo NaN equivalence and `reserved`'s
ignored value. Omitted optional record fields are synthesised as absent.
Present option wrappers and their nesting are retained. This reconstruction
property assumes a decoder that implements those coercion rules.

Changing a used field type is not generally an identity-preserving change,
even if a Candid coercion allows it. In particular, changing `text` to
`opt text` introduces option presence, and changing a scalar numeric kind
changes the normalised value.

## Application Hashing

For a block protocol choosing SHA-256, define:

```
H(v* : t*) = SHA-256(C(v* : t*))
```

The hash covers the entire canonical message. This includes the value-derived
type table and argument framing; it excludes any application schema ID.
Applications may select another hash function in their protocol. The encoder
does not implement hashing, and this appendix introduces no language hashing
primitive.

Hash the full original data before any typed projection. A client that decodes
a new block under a smaller old schema can lose present fields, and hashing
that projected value is not verification of the original block. A `blob`
containing the canonical message permits verification followed by ordinary
typed decoding without losing unknown fields first.

The recipe defines a different construction from ICRC-3's
representation-independent `Value` hash. Its purpose here is stability of
typed data under unused schema extensions, not digest or endpoint compatibility
with the existing ICRC-3 standard.

## Implementation Limits

An implementation may reject input outside the profile, malformed messages,
or input that exceeds its stated resource limits. Such rejection is not a
normalisation rule and must not be implemented by discarding present data.
