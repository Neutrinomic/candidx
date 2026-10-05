# Local verification and shared fuzzing

`verify.py` builds all four packages, calls the Rust, TypeScript, and OCaml
direct typed encoders, checks the frozen corpus, and replays native Motoko
programs in three fresh Wasm instances. Its direct suite reconstructs all 61
frozen cases from explicit source schemas and values, with another 32 IEEE-bit,
vector, and multiple-root cases. Expected bytes come from a source-value Python
oracle whose wire parser and normalizer are disabled by a test. It does not
regenerate goldens. When present, `wasm/artifacts/cx.wasm` is checked through
both its typed-memory encoder and its inbound normalizer.
`motoko/test/optimization.mo` adds focused positive and negative regressions for
packed scalar data, UTF-8, numeric limits, omission, and depth/step boundaries.
It currently checks 473 positive and 397 negative inputs in each fresh instance.

```shell
.venv/bin/python tests/verify.py --moc /path/to/moc --core /path/to/core/src --out _out/pass
.venv/bin/python tests/verify.py --runtime-moc wasm/moc --core /path/to/core/src --out _out/direct-pass
```

Current frozen suite: 61 typed cases with Rust/TS/Motoko original bytes, 27 real
schema-evolution probes, 19 positive binary edge cases, 183 type-table
permutations/duplicates/unused declarations, and 30 malformed/unsupported/limit
cases. Each implementation receives 476 messages. Twelve equal-identity groups
and ten distinct-data pairs check the intended contract. Fixture SHA-256:
`878efe14b3f4fb9727dc409bb6d4be20571e46e3afb67e20d7f382975fa4bf86`.

`fuzz.py` generates the **same typed records and schemas** for three direct
encoders: Rust Candid serializer callbacks, JavaScript SDK schemas/native
values, and OCaml typed witnesses/native values. Ordinary Rust and SDK
serialization are separate controls. Generated native Motoko source retains
the temporary `to_candid` plus library-normalization fallback. With
`--runtime-moc wasm/moc`, the same values are independently encoded through
`to_candid_canonical` before ordinary encoding and recovered with the existing
`from_candid` decoder. The compiler's only public addition is that operator;
it requires no new flags and leaves ordinary `to_candid` unchanged. All four
library normalizers still check every ordinary message. The standalone
`cx.wasm` artifact retains its typed-memory ABI and incoming-message normalizer;
it embeds Rust as an additional backend. Every direct result must match
source-derived bytes and complete normalized data. The harness computes
SHA-256 externally.

```shell
.venv/bin/python tests/fuzz.py --seed 0xcad1d --cases 500 --moc /path/to/moc --core /path/to/core/src
.venv/bin/python tests/fuzz.py --seed 0xcad1d --cases 500 --runtime-moc wasm/moc --core /path/to/core/src
.venv/bin/python tests/fuzz.py --input _out/fuzz-cad1d-500/corpus.json --skip-build
.venv/bin/python tests/replay_oracle.py _out/fuzz-cad1d-500 --report _out/oracle.json
```

Coverage includes integer boundaries and arbitrary-precision integers,
options and nested presence, records, vector records with differing absent
fields, selected variants with differing payloads, finite recursive lists,
empty/nonempty Blob and vectors, principals, NaNs, signed zero, infinities,
subnormals, and UTF-8/BOM/Unicode distinctions. The direct fixed suite includes
both `float32` and `float64`, exact IEEE input patterns for Rust/TS/OCaml/Wasm,
native Motoko `Float32` conversions, tuples, and zero/multiple argument roots.

Decoder fidelity is checked against independently constructed original typed
values, separately from canonical-byte equality and re-encoding controls.
Rust and native Motoko must preserve values with their existing decoders;
TypeScript's `decodeCompatible` must do so with ordinary SDK type/value
bindings. Comparisons preserve option presence, sequence order and length,
record and variant payloads, exact text and integers, and every finite float
bit pattern including signed zero. NaNs compare by class and Reserved values
are ignored as specified. Ordinary SDK caveats are classified against the
original values and reported explicitly.

Every encoder's actual complete output, including OCaml and standalone Wasm,
is also decoded independently by Rust and TypeScript against the original
schema and native values. Decoder-only probes invoke no encoder. OCaml has no
ordinary binary value decoder, so its self-decoder report remains
`not-available`. Reports count successful original-value recoveries for each
encoder/decoder pair and track declared/visited source witness kinds and value
edge classes. Recursive `list` and `chain` kinds are schema shorthands rather
than literal wire constructors. Replaying the same corpus/seed is
deterministic. On failure, the harness saves case ID, schema, data, messages,
and a replayable corpus. Seed alone is sufficient with the same generator
version; reports fingerprint source and corpus files. `replay_oracle.py` also
checks the saved messages against the independent Python model, including
complete normalized-data preservation before and after canonicalization.

`direct_guard.py` copies instrumented packages into ignored `_out/` and runs
the direct suite with the old Rust parser/normalizer and OCaml
parser/normalized-value emitter/normalizer disabled. A Rust native custom
serializer also rejects the upstream ordinary serializer on all exported typed
APIs. TypeScript's isolated ESM hook makes `IDL.encode`, ordinary type serializer
methods, and the byte normalizer throw while direct encoding succeeds. These
guards run from `verify.py` and never modify production source.
When using the bundled `wasm/moc`, the verification report also captures the
compiler/RTS source guard and the artifact manifest hashes. Generated Wasm
derives used shapes, measures the exact output size, and writes header and
payload directly into one final Motoko Blob. The Rust RTS helper only interns
and joins shapes and writes the type header. Its source guard checks that
there is no full Rust codec dependency, payload tree, `CXTY` descriptor, binary
parser, or complete output byte vector in the compiler path, and compares
ordinary serialization routines with the pinned upstream source.

The compiler wrapper selects its bundled checkout by default. Use
`CX_MOTOKO_DIR` to run these checks against another prepared checkout:

```shell
CX_MOTOKO_DIR=/path/to/patched-checkout .venv/bin/python tests/verify.py --runtime-moc wasm/moc --core /path/to/core/src --out _out/direct-pass
```

`roundtrip_guard.py` tests the semantic comparators with deliberately changed
values, including option presence, array order/length, text, large integers,
signed zero, variants, tuple arity, and argument counts. Separate decoder-only
negative probes use valid independently encoded different values, alongside
positive controls for NaN classes and Reserved. Native Motoko runs the same
schema-driven equality predicates with mutation assertions and no serializer.
These guards run during full verification and each seeded run.
For a legacy compiler without the `Float32` source type, the native predicate
report records that diagnostic and the six skipped cases; Rust, TypeScript,
and bundled direct-compiler checks still cover those values.

`tests/packaging.py` locally packs and consumes the Rust and TypeScript
packages, checks standalone SDK-free imports and the typed adapter, builds the
OCaml package, and verifies included MIT license texts. Separate consumers call
the public direct APIs from the extracted Rust crate, npm tarball, and copied
installed OCaml package, including native records, absent options, binary
emptiness, and multiple roots. The TypeScript package consumer disables ordinary
serialization callbacks during direct encoding. It does not publish.

`audit_spec.py --rust` independently checks normalized data preservation,
9,040 generated typed messages, 4,520 extension pairs, and original-schema
Rust decoding. `ts/test/audit_codec.mjs` separately covers 12,811 messages,
including exact float bits, long padded integers, recursive schemas, and
5,000 malformed mutations. These seeded tests support the contract; they do
not exhaust all inputs or prove correctness.

The frozen fixture was collected from reviewed typed probes. Golden changes
require inspecting the identity/spec change and exact bytes; do not regenerate
expected outputs merely to make a failing test pass.

The runners accept toolchain, source-path, and output-directory flags; run `--help` for details.
On this workspace Dune can use the sibling Motoko Nix shell. Local reports and
generated source/Wasm are under ignored `_out/`; the reviewed summaries and
selected captured reports are under [research/](../research/).
