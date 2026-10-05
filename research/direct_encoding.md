# Direct canonical encoding

The primary typed APIs produce `cx` bytes from the original values. They do
not first serialize an ordinary Candid message, parse it, and serialize a
second message. The native Motoko library retains its temporary
`CandidX.canonicalize(to_candid(value))` fallback.

| API | Input integration | How bytes are produced |
| --- | --- | --- |
| Rust `encode_one` / `encode_args` | Existing `CandidType` values and serializer callbacks | Derive used shapes, then write from the original values |
| TypeScript `encodeCanonical` | Existing SDK IDL types and values | Visit types and values, then write from the original values |
| OCaml `encode` / `encode_args` | Typed witnesses, record getters, variant selectors | Derive used shapes, then write from native values |
| Patched Motoko `to_candid_canonical` | Compiler-supplied type/layout metadata and native heap values | Rust RTS reads the original heap and writes the canonical Blob |
| Standalone Wasm `cx_encode_typed` | Typed nodes, source optional-field flags, and native scalar data | Read the ABI nodes and write canonical bytes; no ordinary DIDL input |

The Rust `no_std` core exposes direct value-tree and source-visitor APIs for
embedding. The Motoko runtime uses the source visitor, avoiding a complete
intermediate payload tree. The standalone memory32 ABI reconstructs a typed
tree from its host transport. That marshalling has a cost and is measured
separately; it is not the native Motoko runtime's heap interface. Source and IR
interpreter adapters also use internal value nodes as a reference path.

## Why values can be visited twice

A Candid message places its type table before its payload. In `cx`, that table
depends on the data: absent optional record fields disappear, variants retain
used cases, and an array's common type depends on all its elements. The encoder
must establish those types before it can emit the header and payload under
them.

The direct encoders first derive finite used shapes and their joins. They
intern those shapes in the specified traversal order, write one header, and
visit the source again to emit the payload. The first visit stores shape
metadata rather than an ordinary encoded message. Scalar conversions and
payload writes happen in the emission path. This is one encoding operation
with internal visits, not an encode/decode/encode pipeline.

Shape scratch is reused across array elements. Once an accumulated array
shape already covers an element's shape, the join retains its buffers. Rust's
typed callbacks use the same reuse helpers. Byte spans have a bulk path;
Motoko ropes and arbitrary-precision integers still require conversion before
their bytes can be written. A direct encoder is not inherently allocation
free or necessarily faster for every input.

The Rust typed adapter also caches resolved source descriptors after enough
serializer callbacks to amortize the setup. The cache retains source type
ownership, visits only used variant children, and has explicit node/field
quotas. A valid schema exceeding those quotas falls back to streaming lookup.
The [CPU and allocation capture](results/rust-typed-cpu.json) records both the
record-workload improvement and the remaining callback overhead.

## Source consistency and bounds

Values, typed witness getters, selectors, and serializer callbacks must remain
stable during the call. Emission checks its observed source shape against the
first visit, rejecting changes that invalidate the derived wire shape, such
as its only present optional record field disappearing. This does not make
arbitrary user callbacks pure: changes that preserve an argument's joined
shape, including scalar changes, can still change the emitted value.

Both visits enforce the profile's value-depth, traversal, scalar-integer, and
output limits. Type/layout metadata is validated separately, including numeric
field-ID collisions. The low-level tree and source interfaces require each
vector to originate from one uniformly typed source vector; the values may
vary in option presence and selected variant cases. Arbitrarily heterogeneous
trees are not a replacement for a Candid source schema.

## Checking the direct paths

[Direct conformance tests](../tests/direct.py) construct expected bytes from
explicit original schemas and values, without decoding a source message.
[Guard tests](../tests/direct_guard.py) disable ordinary encoders/parsers and
inbound canonicalizers while exercising the Rust, TypeScript, and OCaml typed
paths. Negative controls verify that the disabled operations really fail.
The supplied compiler patch lowers the direct operator to its own primitive
and native heap RTS helper, independently of ordinary serialization.

Compiler tests call the direct operator before ordinary encoding. This caught
a lazy large-integer initialization defect that an ordinary-first test would
have hidden. The runtime now initializes the integer through its supported
accessor before reading it. Native vectors and principal spans in TypeScript
use intrinsic backing-storage metadata, with regressions for float views,
incorrect widths, spoofed lengths, and oversized principals.
The typed TypeScript API also rejects malformed UTF-16 before UTF-8 encoding,
so unpaired surrogates cannot silently become replacement-character text.

Round-trip tests compare decoded values with independently constructed
original native values. Re-encoding is only a secondary check: a byte round
trip alone could hide the same mistake in an encoder and decoder. Shared
fuzzing sends every encoder's actual output through Rust and TypeScript
decoders, while native Motoko additionally checks its own typed recovery.
Mutation guards confirm that the original-value comparisons detect changed
options, arrays, variants, integers, text, and finite floating-point values.

The [verification report](verification.md) records the complete repeated
cross-language checks. The [performance report](../benchmarks/performance.md)
separates direct encoding, explicit ordinary-encode-plus-normalize controls,
inbound normalization, and ordinary decoding. SHA-256 is computed externally
by the tests; none of these encoding APIs includes a hash implementation.
