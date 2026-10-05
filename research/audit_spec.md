# Independent specification and decoder audit

Audited on October 4, 2026. This audit concerns the local `cx` profile,
version 1.0.0, which is not an adopted Candid or ICRC standard.

The requested identity relation is specific: extending records with absent
optional fields and variants with unselected cases must preserve canonical
ordinary Candid bytes recursively, including within vectors. Blob and
`vec nat8` denote the same binary kind, even when empty. Existing option
presence, scalar numeric kinds, selected cases, present fields, order, and
text bytes remain meaningful. The encoder introduces neither an application
schema ID nor a hashing API. Applications hash the complete canonical message.

## Findings and changes

The original appendix was not self-contained: its normative join operation
linked to `CanonicalValues.md`. The revised
[CandidAppendix.md](../spec/CandidAppendix.md) spells out the normalization,
binary-data rule, finite wire type, complete partial join, left-to-right vector
fold, type-table numbering, and payload reconstruction. It also states the
application hashing recipe and its distinction from ICRC-3's existing hash.

The appendix now reflects the corrected record policy: only absent optional
record fields disappear. Explicit `null` and `reserved` fields remain, and
`reserved` retains its scalar kind. This preserves meaningful field presence
and avoids an ordinary JavaScript SDK decoder limitation with omitted required
null fields. The final `cx` specification and frozen vectors define the
record policy used by the packages.

The typed reconstruction statement is formulated under the ordinary Candid
coercion rules. It should not be advertised as unconditional compatibility
with every existing language binding. Three JavaScript SDK limitations are
reproducible below. These limitations do not justify altering the value
profile's bytes. The local TypeScript compatibility adapter corrects all three
for the tested original schemas.

## Ordinary JavaScript decoder caveats

These observations use the installed `@icp-sdk/core` 6.1.0 adapter. The relevant
upstream source is
[`packages/core/src/candid/idl.ts`](https://github.com/dfinity/icp-js-core/blob/28bbb0ab8e63ee3b2acc4c3146ced320bf6e4ada/packages/core/src/candid/idl.ts):
`TextClass.decodeValue` uses `TextDecoder` without preserving a leading BOM;
`TupleClass.decodeValue` requires a tuple shape of sufficient length rather
than applying the record rule for missing optional fields.

### Leading text BOM

Input text `"\uFEFFabc"` has raw and canonical bytes:

```
4449444c00017106efbbbf616263
```

The encoder preserves every byte. `IDL.decode([IDL.Text], bytes)` returns
`"abc"`. Decoding the original ordinary encoding has the same behavior, so
this is pre-existing upstream behavior rather than additional canonicalizer
loss. A decoder that preserves the full UTF-8 string requires BOM-preserving
text decoding, such as JavaScript's `TextDecoder` with `ignoreBOM: true`.

### Omitted optional tuple components

For `record { 0 : text; 1 : opt nat }`, value `record { 0 = "x"; 1 = null }`,
the canonical record contains only field 0. The SDK's tuple decoder rejects
the shorter tuple. For `record { 0 : opt nat; 1 : text }`, value
`record { 0 = null; 1 = "x" }`, the canonical wire record contains only field 1.
The SDK rejects that sparse record as a non-tuple.

Both canonical messages are ordinary Candid and recover their original values
under Candid's record coercion rule. A compatibility adapter can decode tuple
schemas through numeric record fields and reconstruct the tuple, or the
upstream decoder can implement that rule. Retaining absent optional tuple
components solely for this SDK would violate the uniform record omission rule
and weaken the requested identity contract.

### Recursive optional fields

The SDK represents recursive schemas with `IDL.Rec`, including declarations
whose unfolded type is optional. Its missing-record-field branches only check
the expected field's immediate class. They do not unfold `RecClass` to discover
an optional field. This causes a valid omitted recursive option to be treated
as a missing required field.

A minimal finite list demonstrates silent data loss:

```javascript
const List = IDL.Rec();
List.fill(IDL.Opt(IDL.Record({ head: IDL.Nat, tail: List })));
const value = [{ head: 1n, tail: [] }];
```

Its raw bytes and canonical bytes respectively are:

```
4449444c026e016c02a0d2aca8047d90eddae704000100010100
4449444c026e016c01a0d2aca8047d01000101
```

The ordinary decoder recovers the node from the first message. From the second
it returns an absent list, `[]`: `RecordClass` fails to synthesise the omitted
`tail`, then `OptClass`'s failed-coercion fallback silently discards the present
outer node. The canonical message preserves the node's data and is decodable
under the Candid record rule. Decoder compatibility requires resolving
recursive expected field types before checking whether they permit absence.

## TypeScript compatibility adapter

The [decodeCompatible adapter](../ts/src/decode.ts), exported through
`candidx/candid`, copies expected schema constructors with recursive
memoization. Its text decoder preserves a leading BOM. Its tuple decoder
applies numeric-record coercion. Private proxies make optional recursive
record and tuple fields recognizable as nullable while delegating present
values to their true recursive type, retaining all option nesting.

The adapter does not patch SDK globals, alter canonical bytes, or replace
caller-owned schema nodes. Focused checks include repeated decoding with
frozen recursive schemas, shared schemas across arguments, chained aliases,
mutual recursion across records and tuples, differing option depths on mutual
record aliases, and 64 nested option levels. The tests distinguish absent
options from present options containing absence, including within an optional
tuple.

Its contract is decoding at original encodable Candid schemas in the supported
data profile. The SDK's decoding-only `IDL.Unknown` placeholder is outside that
contract and now produces a readable `Error`, including when nested in an
expected schema. Uninitialized recursive schemas also fail explicitly.

The adapter still applies ordinary projection and optional-coercion rules:
new present fields can be discarded under an older expected record, and an
unknown optional variant can become absent. Therefore decoding must follow
verification of the full original canonical bytes. It is a scoped adapter for
the three demonstrated SDK limitations, not a replacement parser or a claim
to correct every upstream SDK edge case.

## Invariant review

No counterexample has been found to the corrected normalization/join rules for
uniformly typed, finite supported data.

Absent optional record fields do not appear in the normalized tree. Unselected
variant cases likewise do not appear. Adding either, including in recursive
declarations or nested vector element schemas, therefore produces the same
normalized trees and the same derived types. Deterministic field ordering,
type interning, numbering, and payload reconstruction then fix the same bytes.

Keeping every present option wrapper is essential. At a position whose source
type is optional, normalized values are either `null` or `opt w`. The join
therefore combines absence with an existing wrapper rather than erasing or
inventing presence. In a shared record type, an omitted field contributes
absence and a present optional field already has its wrapper. The emitter
must emit marker 0 for absence and marker 1 followed by the original child
for presence. This is why `null`, `opt null`, and `opt (opt null)` remain
distinct and can be reconstructed.

For vectors, bottom `empty` describes an unpopulated non-binary element set;
joining it with populated element types adds no value conversion. A binary
vector instead retains `nat8`, even if unpopulated. Canonical decoding can
therefore preserve the binary/non-binary distinction through nested empty
vectors. Scalar kinds never join through `reserved`, and variant discriminators
keep differing payload constructors separate.

These arguments are restricted to normalized values arising from a common
supported Candid element type. Arbitrary heterogeneous synthetic value trees
are not a justification for a broader join or identity claim. No claim is
made that every Candid subtype coercion preserves hashes: projection can
discard present fields or unknown optional variant cases. Applications must
verify the complete original bytes before typed projection.

## Reproducible checks

[tests/audit_spec.py](../tests/audit_spec.py) builds ordinary typed messages
using the upstream JavaScript encoder and independently exercises the profile.
It generates record/variant schema extensions, nested options, nullable record
arrays, recursive tree arrays, and empty binary/non-binary array mixtures.
Present function/service values are never generated; unused signatures are
deliberately inserted as absent record fields or unselected cases.

Run from the repository root after building TypeScript:

```shell
python3 tests/audit_spec.py --rust
```

This is the exact completed command. Its defaults are `--count 4000`,
`--seed 20261004`, and `--rust-toolchain 1.98.1`. The TypeScript build prerequisite
is `npm run build` executed from `ts/`. The optional Rust check builds its
temporary audit program with Cargo's `--offline` flag; it requires the already
cached `candid` 0.10.37 and related dependencies.

The script checks typed JavaScript decode equivalence to the original ordinary
encoding, extension identity, TypeScript/Python canonical byte agreement,
idempotence, and normalized data preservation. The optional Rust check uses
`candid` 0.10.37, reads each original wire schema, decodes the canonical message
at that schema, and checks Rust canonical byte agreement. NaN equivalence is
allowed; scalar kinds and signed zero remain checked. The known SDK tuple/BOM
caveats are reported separately rather than counted as successful JavaScript
typed reconstruction.

The completed run used seed `20261004` and the corrected record policy:

* 9,040 generated typed messages and 4,520 schema-extension pairs passed
  canonical byte agreement, idempotence, and normalized-data preservation.
* All 9,044 messages, including the four minimal SDK caveat examples, decoded
  correctly at their original schemas using Rust `candid` 0.10.37. Rust bytes
  also matched the TypeScript codec and Python oracle for every message.
* 8,840 of 9,040 ordinary JavaScript typed decoding checks passed. All 200
  recursive-tree checks (100 generated values at both old and extended
  schemas) encountered the missing-recursive-option SDK limitation. Those
  values still passed schema-extension identity and complete data preservation.
* Leading text BOM stripping and both leading/trailing optional tuple
  omissions were reproduced separately.

After adding the adapter, the exact command `python3 tests/audit_spec.py --rust`
was repeated with adapter reconstruction assertions. All 9,044 canonical
messages reconstructed their original-schema values through `decodeCompatible`.
This includes the 200 previously failing generated recursive cases and all
four minimal SDK caveat examples. Canonical agreement, original-schema Rust
decoding, extension identity, and idempotence remained successful. The ordinary
SDK failures are still reproduced independently and reported separately.

`npm test`, executed from `ts/`, builds TypeScript and runs the shared corpus and
[focused compatibility tests](../ts/test/compatibility.test.mjs). The focused
tests cover the adapter behavior and schema nonmutation described above. The
completed run passed all 14 Node tests, including 10 compatibility tests.

The 9,044-message generator above did not use the OCaml codec or native
Motoko decoder; those are also covered by the shared cross-language tests.
Generated testing supports the invariants; it is not a formal proof.

## Historical inbound Rust/Wasm runtime and optimization

This section records the earlier inbound-normalizer integration and its
measurements. Primary typed Rust and compiler encoding now use the direct
interfaces described below. `Prim.canonicalizeCandid` remains an inbound
utility; its original ordinary-encode pipeline is retained as a benchmark
comparison.

The [Wasm backend](../wasm/README.md) adds a standalone no-import memory32
module and a concrete local Motoko compiler/RTS integration. The compiler's
ordinary encoder feeds `Prim.canonicalizeCandid(Blob) : Blob`; the primitive
calls the same Rust codec inside the linked memory64 RTS. It uses the Motoko
allocator, drops scratch Rust structures before returning, and publishes a
fresh Motoko Blob with the incremental GC allocation barrier. This is an
ordinary-encoding-plus-normalization path, not a new direct typed encoder or
independently implemented fifth codec. Neither path adds hashing.

The standalone ABI passed 3,574 shared raw messages, 3,574 idempotence checks,
30 negative fixtures, and 1,000 equal-workload arena-reuse calls. The same
3,574 messages and 1,204 canonical-message idempotence checks passed through
both the native OCaml interpreter and compiler-emitted memory64 Wasm/RTS.
All 30 malformed/unsupported/resource-limit cases produce readable
`CandidX error:` traps in both modes; none produce `RTS panicked`. The typed
compiler smoke recovers the original record with its omitted optional field.
The native interpreter's test inputs are complete literal messages because
upstream interpreter modes do not implement `to_candid`/`from_candid`.

Exact commands from the CandidX root are:

```shell
python3 wasm/build.py
_build/wasm-venv/bin/python wasm/test.py --corpus _out/fuzz-cx-135288c-500/records.json --resource-stress
```

The bridge test is run from the Motoko repository to enter its Nix shell:

```shell
nix develop --command python3 ../candidx/wasm/test_bridge.py --corpus ../candidx/_out/fuzz-cx-135288c-500/records.json --resource-stress
```

`test-runner -b -f 'cx-candid|idl-ops|idl-decoding-bug|leb-random' test/run`,
run inside the isolated compiler worktree's Nix shell, checks the new silent
primitive test plus three existing ordinary Candid tests. All four pass.
Native and JS compiler targets build. The local OCaml 5.4 environment needs
Bigarray compatibility metadata; its existing JS warnings concern integer
truncation and `caml_unix_map_file_bytecode`. Browser JS interpreter execution
of the new primitive is outside this prototype's validation scope.

The parent heap driver measured the real linked canister path on fresh local
IC canisters and repeated calls crossing the 64 MiB GC allocation partition.
Retained typed values remain valid and closed partitions are reclaimed.
Post-GC occupied heap is not synonymous with exact live-object memory because
the collector leaves the current allocation partition in place. The shared
[benchmark report](../benchmarks/performance.md) records before/after figures.

Rust optimization keeps the same bytes and codec resource bounds. The no_std
core now has no external crate dependencies; the optional typed Candid adapter
uses the existing crate. Borrowed scalar/Blob/Text spans, ordered record
vectors, stack-based metadata LEB encoding, owned linear shape joins, borrowed
type interning, and a tested no-widening array fast path reduce allocations.
On the four native workloads, normalization medians improved from
12.707/129.352/12.044/1,015.372 microseconds to
2.767/27.307/3.340/200.033 microseconds respectively. Requested allocator bytes
fell from 12,968/207,036/270,235/1,601,212 to
3,574/34,264/68,337/245,251. Raw reports are
`_out/rust-before/{bench,allocations}.json` and
`_out/rust-after/{bench,allocations}.json`.

The tracked host profiler is [rust/examples/profile.rs](../rust/examples/profile.rs).
It uses existing dev dependencies and adds no production API or default
library dependency. The exact native commands are:

```shell
rustup run 1.98.1 cargo run --release --locked --manifest-path rust/Cargo.toml --example bench > _out/rust-after/bench.json
rustup run 1.98.1 cargo run --release --locked --manifest-path rust/Cargo.toml --example profile -- _out/rust-after/bench.json > _out/rust-after/allocations.json
```

The same commands captured the before snapshot. Its exact source and manifest
are retained at `_out/rust-before/{lib.rs,Cargo.toml,Cargo.lock}`; the `profile`
example measures allocator requested bytes independently of timing. The
parent's final differential run subsequently passed 13,530 messages through
all four logical implementations, the standalone Wasm module, and the actual
linked RTS bridge. Neither Wasm path is counted as an independent algorithm.

These native allocation figures count requested bytes including full realloc
requests, exclude allocator headers and already-existing input, and do not
replace actual canister heap measurements. Both Wasm embeddings add a 256 MiB
scratch bound; exhausting it traps rather than changing or truncating output.

The final [validation record](results/wasm-validation.json) retains exact run
counts, commands, source and artifact hashes, and clean-base patch checks.
Wasmtime Python 49.0.0 exercised the standalone ABI; CLI 45.0.2 exercised the
compiler-emitted memory64 bridge. The standalone scratch stress fills the
arena, verifies a trap without output, then successfully resets it. A valid
message of 30,303 records containing 65 explicit null fields stays within the
codec's 1,999,999 traversal steps and triggers the RTS scratch limit with a
readable error. Upstream Motoko tracked sources remain unchanged.

## Direct typed Rust encoding

`encode_one` and `encode_args` now call the original `CandidType` value
callbacks twice: first to derive only used `Shape` metadata, then to emit
the canonical header and typed payload. `encode_one_with_type` and
`encode_args_with_types` provide the same direct callbacks with explicit
source metadata for annotated dynamic values. These paths do not call the
ordinary encoder, build an intermediate payload tree, or parse wire bytes.
The no_std `encode_source_args` interface similarly visits original native
values through `DirectSource`; the Motoko compiler/RTS uses that interface.
The typed-tree `encode_direct_args` remains useful for a typed memory ABI.
Inbound `canonicalize` remains a separate supported operation.

Focused Rust tests cover native unit and transparent newtype callbacks,
flattened and nested variant payloads, selected-case indices, recursive
records, explicit Null/Reserved fields, absent optional fields, Some(None),
binary aliases including empty vectors, scalar integer magnitudes, NaNs,
signed zero, type-table bounds, traversal/depth bounds, shape changes between
visits, malformed source labels, swallowed errors, and unfinished compounds.
A custom serializer that rejects `candid::ser` serializers succeeds through
all four public typed interfaces. Shared direct conformance additionally
checks the ordinary parser and normalizer can be disabled without changing
the typed results; those final run counts live in the shared test report.

Custom callbacks and source visitors must remain stable between visits and
report accurate source types. Emitted shape witnesses reject changes which
could leave unused table entries or invalid payloads. Same-kind scalar value
changes do not change the shape and remain outside that guard. Low-level
vectors require a single uniform original element schema; retained record
presence may differ only through absent optional fields. Arbitrary mixtures
of required-null and absent fields from unrelated source schemas are outside
that typed contract.

Persistent scratch shapes and the exact no-widening join predicate preserve
buffers between array elements. Before the subsequent source-cache change,
the host allocation profiler measured the following requested bytes,
including full realloc requests:

| Workload | Direct before shape reuse | Direct after shape reuse | Ordinary encode + normalize |
|---|---:|---:|---:|
| Small | 11,155 | 9,475 | 11,898 |
| Records 128 | 126,382 | 30,710 | 78,801 |
| Blob 64 KiB | 73,872 | 73,872 | 337,837 |
| Records 1,024 | 947,791 | 173,631 | 452,426 |

All outputs match the preexisting shared fixture bytes. Fixture construction
and schema-cache warmup happen outside counters; encoders' own schema
allocations remain included. These are native allocator measurements, not
timings or actual canister heap measurements. The reproducible
[allocation record](results/rust-direct-allocations.json) includes commands,
fixture/source/artifact hashes, requests, peaks, output capacities, and all
four separately labeled phases. The benchmark labels primary typed encoding
`canonical_encode_direct`; `canonical_encode_pipeline` explicitly measures
the old ordinary-encode comparison.

```shell
rustup run 1.98.1 cargo test --locked --all-features --manifest-path rust/Cargo.toml
rustup run 1.98.1 cargo test --locked --no-default-features --manifest-path rust/Cargo.toml
rustup run 1.98.1 cargo clippy --locked --all-features --all-targets --manifest-path rust/Cargo.toml -- -D warnings
rustup run 1.98.1 cargo run --locked --release --manifest-path rust/Cargo.toml --example profile -- _out/rust-after/bench.json > _out/rust-direct-allocations-after-shape-reuse.json
```

The focused Rust run after shape reuse passed 24 tests with all features and
15 with no default features. Compiler/RTS artifact validation and actual IC
heap measurements are reported separately; the shared shape/writer reuse is
not counted as an independent implementation.

The subsequent std-only source cache retains validated opcodes, resolved
aliases, record label IDs, and selected variant children. Its keys retain
their original `Rc` types, and it activates after 32 actual serializer
callbacks rather than bulk byte steps. The cache has at most 1,024 source
nodes and 4,096 aggregate field/case descriptors; larger valid metadata uses
the original streaming checks. Unselected variant children are registered
only when selected. The two direct value visits, emitted shape witness,
sticky error checks, compound completion checks, and resource limits remain.

The final quiet native capture measured direct medians of 8.798 / 99.108 /
7.863 / 740.788 microseconds for Small / Records 128 / Blob 64 KiB / Records
1,024. The record workloads improved by 1.084x and 1.096x from the pre-cache
direct baseline. Small and blob cases did not improve. Record direct encoding
still takes 1.91x and 2.11x the explicit ordinary-encode-plus-normalize
comparison in this capture; lower allocation does not imply lower CPU cost.

Requested direct allocations after the cache are 9,491 / 33,998 / 73,888 /
176,919 bytes, versus pipeline allocations of 11,898 / 78,801 / 337,837 /
452,426 bytes. The cache adds 3,288 requested bytes on the record workloads
compared with shape reuse alone. The no_std runtime visitor is unchanged.
The [CPU and allocation record](results/rust-typed-cpu.json) retains the
Callgrind evidence, baseline snapshot, seven-trial captures, exact byte
hashes, source hashes, profiler results, commands, and limitations.

The frozen Rust callback source now passes 27 all-feature tests and 15
no-default-feature tests; Clippy passes with warnings denied. New focused
tests cover 16,384 distinct unused variant children, both cache capacity
fallbacks, and the separation of bulk byte steps from callback visits.
Shared original-value and cross-decoder fuzzing records its final counts in
the shared validation reports.

An independent rerun on the exact frozen callback source passes 54
callback/tree checks and 27 cache checks (15 positive, 12 rejection).
The unchanged native source visitor additionally passes 2,218 differential
cases, 19 rejection cases, and two boundary cases. Its differential set
includes 2,187 pairwise scratch joins. Cached and streaming alias limits,
quota fallback, malformed source labels, swallowed errors, forgotten
compounds, and unused unsupported children are included; the independent
[review record](results/direct-review.json) owns the full evidence.
