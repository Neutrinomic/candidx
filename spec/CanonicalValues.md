# Canonical values and encoding

Profile: `cx`, fixed encoding contract, October 2026.

## Contract

The input is a typed Candid argument sequence, or its ordinary Candid encoding.
The output is a deterministic ordinary Candid message representing its
normalized data. An application can hash the entire output with its chosen
hash implementation. No application schema ID is added.

These values MUST have identical canonical bytes:

```candid
record { a = "abc" } : record { a : text }
record { a = "abc"; b = null }
  : record { a : text; b : opt text }
```

Adding unselected variant cases, absent optional record fields, or unused
recursive type definitions MUST NOT change old values' bytes. These rules
apply recursively, including records in arrays. Present fields, selected tags,
array order, text bytes, scalar values, and scalar kinds remain meaningful.

This is not equality under every Candid coercion. Decoding can discard present
fields or unknown optional variants. That loses data and can change the
canonical result. Changing `nat64` to `nat`, renaming a field, or changing its
meaning is not an identity-preserving schema extension.

## Supported data

The data profile supports `null`, `reserved`, booleans, all Candid integer and
floating types, text, public principals, options, binary data, vectors, records, variants,
and finite values of recursive declarations. Blob and `vec nat8` are the same
Candid type and the binary data kind; an empty blob retains that kind. Tuples are records with numeric field IDs.

Function and service reference values are excluded in v1. Their signatures
are declared schema, yet existing decoders check those signatures. Erasing them
cannot generally preserve typed decoding. Unused function/service declarations
in an input table do not affect otherwise supported data. Opaque references
are excluded. `empty` has no values, but is used as an empty vector element
type.

## Value normalization

Normalization produces a finite tree. It traverses every actual field before
any receiver-side projection; it MUST NOT discard a present unknown field.

1. Primitive scalar kinds and values are retained. `reserved` has no data
   payload but retains its scalar kind. Integers are exact, including
   arbitrarily large `nat`/`int`.
2. Every float NaN normalizes to the positive quiet NaN: bits `0x7fc00000` for
   `float32`, `0x7ff8000000000000` for `float64`. Other bits, including negative
   zero, subnormals, and infinities, are retained. Text is exact UTF-8; no Unicode
   normalization is performed.
3. An absent option normalizes to `null`. A present option retains its wrapper
   and normalized child. Presence and nesting are data: `null`, `opt null`, and
   `opt (opt null)` remain different. Preserving wrappers avoids ambiguous
   optional coercions and works with current decoders.
4. A record omits absent optional fields and normalizes each remaining field.
   Explicit `null` and `reserved` fields remain present. A present option
   containing an absent option does not disappear:
   `opt null` retains that presence information. Fields are numeric Candid IDs,
   sorted in increasing order; no original field-name strings are added.
5. A variant retains just the selected field ID and its normalized payload.
   A null variant payload is retained. Unselected declared cases disappear.
6. A vector retains its length and normalized elements in their original order.
   Its declared element type is not retained. The argument sequence retains
   its length and order; a null argument is not omitted.

Field identity is the numeric Candid ID, including the existing Candid label
equivalences/collisions. Original label spelling cannot be recovered from the
wire and is not separately committed by this profile. Protocols must assign
stable, unambiguous field IDs.

These invariants cover adding absent optional fields and unselected variant
cases. They do not identify required data with present optional data: changing
an existing field's type from `text` to `opt text` changes the value's presence
structure. Candid compatibility alone does not imply value identity.

## Deriving the wire type

Let `P(v)` be the finite type obtained from the normalized value. Primitives
keep their kind; a retained present option has type `opt P(v)`; a record has
only its retained fields; a variant has only its selected tag. An empty non-binary vector
has type `vec empty`; binary data has type `vec nat8` even when empty. A nonempty vector has type `vec J(P(v1), ..., P(vN))`.

`J` is a deterministic join on the finite types produced by normalization:

* `J(empty, T) = T` and `J(T, T) = T`.
* `J(null, opt T) = opt T`; for any other non-null `T`,
  `J(null, T) = opt T`.
* `J(opt A, opt B) = opt J(A, B)`.
* For non-null, non-option `B`, `J(opt A, B) = opt J(A, B)`.
* Vectors join their element types.
* Records use the union of field IDs. A missing field contributes type `null`;
  matching fields join recursively. This retains all present data. Fields that
  are absent everywhere were already omitted.
* Variants use the union of selected field IDs. Payload types of matching cases
  join recursively; a missing case contributes no payload constraint.
* Other unequal scalar kinds or unequal outer constructors have no join in
  this profile. They cannot arise from a supported, uniformly typed Candid
  vector without a variant discriminator. Implementations reject such inputs
  rather than silently falling back to `reserved` and losing data.

Records in one vector share a wire type. For example, normalized records
`[record {a = "x"}, record {a = "y"; b = opt "z"}]` use
`vec record {a : text; b : opt text}`. Their
payload supplies option-absent for the first element and option-present for the
second. Normalizing the resulting Candid again MUST give identical bytes.

Fold joins from left to right in vector order. This definition, rather than an
unspecified least-supertype operation using Candid's lossy optional rules, fixes
the emitted shape.

## Emitting ordinary Candid

Intern equal finite compound types. Emit reachable entries in depth-first
preorder from argument roots, visiting record/variant children in increasing
field ID order. Assign an entry its index before visiting its children. Emit
each structurally equal type once. Finite value-derived types are acyclic even
when the input declaration was recursive.

Use ordinary Candid `T`, `I`, and `M`, with shortest unsigned/signed LEB128
representations throughout. A record emits exactly the derived wire fields.
Missing vector-element fields encode null/absent at their joined optional type.
Non-nullable data at an optional wire type receives an option-present marker.
A retained present option emits its marker and then its nullable child. Variant
indices refer to the derived type's sorted cases, so narrowing a case list
requires rewriting indices. Vector lengths and all scalar payloads are emitted
anew. Copying the old payload after changing only its header is incorrect.

The output is ordinary Candid. A decoder implementing Candid's coercion rules
at the original supported schema reconstructs its value modulo NaN equivalence
and `reserved`'s ignored value. Omitted optional record fields are synthesized
as absent. Existing SDKs have [documented compatibility limitations](../research/audit_spec.md#ordinary-javascript-decoder-caveats);
the TypeScript [decodeCompatible adapter](../ts/src/decode.ts), exported through
`candidx/candid`, corrects the tested JavaScript SDK cases without changing the
canonical bytes or caller-owned schemas. Typed decoding can still project away
present fields or unknown optional cases. An old application schema is not
guaranteed to understand every new present field or variant.

## Hashing and transport

An application may compute `SHA-256(C(v))` over the entire emitted message,
including the DIDL prefix, derived type table, argument count, and payload.
Another digest may be selected by its block protocol. CandidX only emits bytes;
all hashing occurs in the application. Fix the profile and hash algorithm in
the protocol; a schema version is not part of the hash preimage.

This has ICRC-3's desired stability for absent fields and unused cases, not its
existing representation-independent digest algorithm or endpoint format.
Current ICRC-3 compliance still requires its standard `Value` and hash rules.

Clients with an older schema should verify the original canonical block bytes
before typed decoding. Hashing a projected decoded record would lose new
present fields. Transporting a canonical block as a `blob` retains those bytes;
the client can then call the existing typed Candid decoder. Sending the record
through ordinary Candid encoding remains possible, but every verifier must
normalize the complete received message before schema projection.

## Resource limits

Implementations may reject oversized or excessively nested messages explicitly.
These are implementation limits, not value transformations. The initial
packages use 32 MiB input/output limits, 1,024 input type entries, two million
value/collection steps, 256 value/type nesting levels, and 4,096 bytes per
arbitrary-precision integer, and ten bytes per index/reference/field ID.
They reject malformed types, invalid UTF-8,
invalid tags, illegal public principals, trailing payload, and uninhabited
`empty` values. Accepted padded LEB128 input is rewritten to shortest form.

## Source basis

The [Candid spec](https://github.com/dfinity/candid/blob/89b7cff95ff706507ced4563f4069379eb50bb7b/spec/Candid.md)
already permits optional field omission through coercion and value-dependent
variant types during serialization. CandidX makes the choice deterministic
and recursive. The [ICRC-3 specification](https://github.com/dfinity/ICRC-1/blob/main/standards/ICRC-3/README.md)
explains why projected typed decoding alone cannot verify unknown block fields.
