# Local verification

The final direct encoders passed three complete fixed-suite runs and three
500-case seeded differential runs. The seeds produced 3,390 matching typed
records, 43,470 successful original-value recoveries across encoder/decoder
combinations, and 36,453 saved messages checked by an independent oracle.
Exact canonical bytes and application-side SHA-256 hashes agree for matching
typed data. The native Motoko library retains its tested
ordinary-encode-and-normalize fallback. Wasm embeddings reuse Rust and provide
additional integration coverage.

The fixed `cx` contract and version 1.0.0 packages are MIT licensed. No package,
issue, upstream PR, or forum post was published during validation. This evidence
is substantial testing and review, not a formal proof or security audit.

## Three complete fixed-suite passes

| Pass | Compiler for native Motoko fallback | Result |
| --- | --- | --- |
| [1](results/direct-final-pass-1.json) | Source 1.16.1-44-g9384e1076 | Pass |
| [2](results/direct-final-pass-2.json) | Source 1.16.1-44-g9384e1076 | Pass |
| [3](results/direct-final-pass-3.json) | Installed 0.16.3 | Pass |

Every pass checks 93 direct typed cases, containing 125 argument roots, across
Rust, TypeScript, OCaml, the standalone Wasm typed ABI, and the patched Motoko
compiler/runtime. These include the unchanged 61 frozen goldens plus exact
floating-point bits and multiple arguments. Expected bytes are derived
independently from original schemas and values, without parsing an ordinary
source message. Each encoder's actual bytes also pass both independent
original-value decoder checks: 93 × 5 × 2 = 930 recoveries per pass.

Each pass also checks 476 ordinary-message normalization cases per backend,
27 typed evolution probes, 183 type-table transformations, 30 malformed,
unsupported, or limited inputs, twelve identity groups, and ten distinct-data
pairs. Native Motoko executes in three fresh Wasm instances. An additional
870 native optimization regressions run in three fresh instances per pass,
covering packed floats, input alignment/padding, UTF-8, scalar boundaries,
wide optional records, depth 255/256/257, and traversal limits.

Rust passes 27 tests with all features, 15 without default features, and
all-target Clippy with warnings denied. TypeScript passes compilation and
28 Node tests. OCaml passes its warning-error build and all 66 native typed
tests. The three fixed-suite reports have identical 79-file source maps,
unchanged sources during execution, and identical direct-record hashes.
The three seed reports and package report match the same frozen map.
Generated install files and caches are excluded from source provenance.

The frozen fixture is [tests/vectors.json](../tests/vectors.json), SHA-256
`878efe14b3f4fb9727dc409bb6d4be20571e46e3afb67e20d7f382975fa4bf86`.
Its Rust package copy must match exactly. Verification never regenerates these
expected outputs from an implementation under test.

## Direct-path guards and compiler integration

[Disabled-path guards](results/direct-guards.json) exercise all 93 Rust and
OCaml typed cases with inbound parsing/normalization entry points disabled.
Rust also rejects an intentionally invoked ordinary serializer. Six focused
TypeScript cases execute with `IDL.encode`, type serialization/table methods,
and the inbound canonicalizer disabled; negative controls verify the hooks.
The broader direct corpus exercises every supported constructor independently.

Sixteen compiler call-chain guards check that the dedicated IR primitive
passes source type/layout metadata and native values to the heap visitor,
without ordinary serialization or a value-message parser. These source
checks complement byte and decoder tests. [Direct encoding notes](direct_encoding.md)
explain the two internal visits and callback stability requirements.

The [Wasm validation](../wasm/artifacts/validation.json) includes ten compiler
tests spanning frontend, interpreters and generated Wasm, two JS interpreter
fixtures, one JS-compiler-emitted Wasm fixture, 837 independent scalar
properties, eighteen malformed typed-ABI cases, arena reuse, and resource
exhaustion checks. The 67-file patch applies cleanly to its pinned compiler
base. All 76 manifest source fingerprints and six build binary fingerprints
match the final local build. Its existing JS `map_file` build warning is
recorded alongside passing JS execution, rather than described as a clean
warning-free upstream build.

Direct compiler probes run before ordinary serialization, catching a lazy
large-integer initialization defect during development. Review also corrected
swallowed custom serializer errors, source changes invalidating derived
shapes, an empty-vector depth boundary, and spoofed JavaScript vector/principal
metadata. These have retained regression checks.

## Three shared seeded runs

| Seed | Matching typed records | Unused-schema extension pairs | Messages per normalizer | Original-value recoveries | SDK caveats | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| [0xcad1d](results/direct-fuzz-cad1d.json) | 1,130 | 565 | 5,664 | 14,490 | 352 | Pass |
| [20261004 / 0x135288c](results/direct-fuzz-135288c.json) | 1,130 | 565 | 5,664 | 14,490 | 346 | Pass |
| [0x13579bdf](results/direct-fuzz-13579bdf.json) | 1,130 | 565 | 5,664 | 14,490 | 328 | Pass |

Totals are 3,390 matching typed records, 1,695 extension pairs, and 16,992
messages through each independent normalizer and each Wasm normalization
embedding. Each seed also checks all 93 directed typed cases. The actual
Rust, JS and Motoko ordinary encoders supply separate control messages;
OCaml supplies its native direct encoder. The direct paths are checked against
both the independent source-value model and ordinary-message normalization.

All canonical bytes and external hashes agree. Rust original-schema ordinary
re-encoding is byte-identical. Native programs run in two fresh Wasm instances
per seed. Vanilla SDK 6.1.0 decoder caveats are recorded separately in the
table: leading text BOM, omitted optional tuple components, and some recursive
option fields require the tested compatibility adapter. These observations
are excluded from successful vanilla SDK recovery counts.

The [independent replay](results/direct-fuzz-oracle.json) checks 36,453 saved
messages, including complete normalized-data preservation, canonical bytes,
idempotence, source-value expectations and external hashes: 12,151 per seed.
Repeated messages and control paths explain why this count differs from
messages per normalizer.
Detailed corpora, generated programs, binaries and logs remain in local
`_out/direct-final-v3-*` directories; the captured seed and source hashes identify
reproducible inputs without checking temporary build output into Git.

## Original-value round trips and edge coverage

The decoder matrix checks `decode(encode(x)) == x` against values constructed
independently from the source schema/value corpus. Rust uses Candid's ordinary
decoder; TypeScript uses `decodeCompatible`. The matrix invokes decoder-only
driver modes and compares complete source values, including argument count,
record presence, option wrappers and variant payloads. It does not call an
encoder to decide equality. All non-NaN float bits, including signed zero,
infinities and subnormals, must match; NaNs compare by class, and `reserved`
follows Candid's ignored-value semantics.

| Canonical output producer | Rust Candid decoder, per seed | TypeScript `decodeCompatible`, per seed |
| --- | ---: | ---: |
| Rust direct | 1,130 | 1,130 |
| TypeScript direct | 1,130 | 1,130 |
| OCaml direct | 1,130 | 1,130 |
| Native Motoko fallback | 1,130 | 1,130 |
| Standalone Wasm typed ABI, using Rust | 1,130 | 1,130 |
| Motoko compiler/RTS direct, using Rust | 1,130 | 1,130 |

This gives 13,560 recoveries per seed; its separate 93-case direct suite adds
930. Across three seeds that is 43,470 recoveries. The three fixed-suite passes
add 2,790, for 46,260 successful recovery calls overall. Repeated directed
cases are included in these execution counts. Native Rust, TypeScript and
Motoko programs also assert original-value equality after their own canonical
round trips. OCaml has no ordinary binary decoder in the audited repositories;
its real output bytes are checked by both host decoders. The standalone Wasm
ABI likewise supplies no separate decoder.

The reports retain `constructor_coverage` maps for source declarations,
visited values and observed edge classes. Across each seeded corpus every
supported inhabited primitive appears, including `reserved`, both float
widths, every integer width, text and principal. Compound coverage includes
recursive lists/chains, records, variants, options, vectors and blobs. `list`
and `chain` are source-corpus shorthands for recursive Candid constructors.

| Edge class | Retained checks |
| --- | --- |
| Float32 and Float64 | Exact bit witnesses for finite values, both zeros, infinities, subnormals and NaN payload classes |
| Optional presence | `None`, `Some(None)`, absent record fields, explicit null/reserved and unused-schema extensions |
| Compounds | Empty/nonempty binary and nonbinary vectors, joined record/variant vectors, recursive aliases, tuple labels, zero/single/multiple arguments |
| Text and principal | Empty values, Unicode, BOM, NUL, binary subarrays, malformed UTF-8/UTF-16 and principal length limits |
| Validation and limits | Integer ranges, label collisions, selector validity, 1,024/1,025 output-table entries, depth and traversal limits, scalar/output byte limits |

The last two rows include native unit and independent-review rejection
checks; the seeded matrix covers valid values. These counts describe exercised
inputs and are not an exhaustive enumeration of all values.

The permanent [round-trip mutation guard](../tests/roundtrip_guard.py) checks
the equality predicates themselves. Each Rust and TypeScript backend runs
62 independently constructed comparison probes: 49 unequal mutants must fail
and 13 equal controls must pass. Decoder-only controls additionally accept
76 valid source-value messages and reject 47 valid messages containing the
wrong original data. Mutations cover arguments, scalar values, integer signs
and widths, float bits, text/principal/blob bytes, vector length, option
wrappers, record fields, variant cases and recursive tails. NaN-class and
reserved-value controls verify their intentional equality rules.

Modern native Motoko checks 55 predicates, with 42 unequal and 13 equal cases;
seven schema/arity mutants are statically incompatible and remain checked by
Rust and TypeScript. Compiler 0.16.3 checks 49 predicates, with 38 unequal and
11 equal cases. Its six Float32 predicates are explicitly skipped only after
the exact `M0029` unbound-`Float32` diagnostic; the report lists their IDs.
The patched modern compiler, Rust and TypeScript retain Float32 coverage in
every fixed-suite pass. Guard results and source hashes are embedded in every
final fixed-suite and seeded report.

## Independent review and package consumers

The earlier [specification/decoder audit](audit_spec.md) and
[normalizer audit](audit_codecs.md) retain minimal reproductions and historical
source-fingerprinted evidence. Their counts are not added to current direct
encoding runs. The current [independent direct review](results/direct-review.json)
records nine corrected findings, retained regression tests, and no outstanding
findings. It separately checks native variants,
integer and resource boundaries, typed witnesses, source visitor joins, and
the unsafe Motoko heap interface. It includes 2,187 additional pairwise
optional-presence/vector/variant combinations against the unoptimized tree
join and re-normalization, plus adaptive-cache boundary and callback-error
checks. These deterministic review counts are kept separate from shared fuzz.

The final [Rust CPU and allocator evidence](results/rust-typed-cpu.json) records
the bounded source-metadata cache, exact production hashes, Clippy/tests and
native allocation requests. Record workloads improve over the preceding
direct implementation but remain slower than ordinary encoding plus
normalization in that capture. Requested allocation bytes exclude existing
input and allocator overhead and do not measure IC occupied heap. The earlier
[shape-reuse allocation report](results/rust-direct-allocations.json) is
historical; the final report supersedes it for the current Rust source.

[Package checks](results/direct-packaging.json) passed using real Cargo and
npm archives and their extracted
public interfaces. They cover dependency-free Rust, its typed Candid adapter,
SDK-free TypeScript imports and SDK typed encoding, and an OCaml library copied
from the public Dune installation into an isolated consumer. MIT texts and
version 1.0.0 metadata are checked. These checks pack/install locally; they do
not publish to registries.

## Reproduce

Set up the dependencies in [tests/README.md](../tests/README.md) and build the
optional compiler integration described in [wasm/README.md](../wasm/README.md).

```shell
.venv/bin/python tests/verify.py --moc /path/to/moc --core /path/to/core/src --runtime-moc wasm/moc --out _out/pass-1 --report _out/pass-1/results.json
.venv/bin/python tests/verify.py --moc /path/to/moc --core /path/to/core/src --runtime-moc wasm/moc --out _out/pass-2 --report _out/pass-2/results.json
.venv/bin/python tests/verify.py --moc /path/to/second/moc --core /path/to/core/src --runtime-moc wasm/moc --out _out/pass-3 --report _out/pass-3/results.json
.venv/bin/python tests/fuzz.py --seed 0xcad1d --cases 500 --batch-size 128 --runtime-moc wasm/moc
.venv/bin/python tests/fuzz.py --seed 0x13579bdf --cases 500 --batch-size 128 --runtime-moc wasm/moc
.venv/bin/python tests/fuzz.py --seed 20261004 --cases 500 --batch-size 128 --runtime-moc wasm/moc
.venv/bin/python tests/replay_oracle.py _out/fuzz-cad1d-500 _out/fuzz-13579bdf-500 _out/fuzz-135288c-500 --report _out/oracle.json
.venv/bin/python tests/packaging.py --out _out/packages --report _out/packages/report.json
```

Supply compiler/core paths to fuzz commands when siblings differ. `--input`
replays a saved corpus; `--motoko-repo /path/to/motoko` selects the Nix build
environment for either harness when the default sibling checkout is absent.
[Performance measurements](../benchmarks/performance.md)
cover direct encoding and the retained fallback separately. Historical
adapter-only verification is preserved in [verification-normalizers.md](verification-normalizers.md).

Testing does not prove all-input correctness on every architecture. The
profile excludes actual function/service/opaque reference values, requires
stable callbacks and source values, and imposes explicit resource limits.
Typed projection can discard new present data: verify complete original bytes
before decoding into a smaller schema. Incompatible future identity rules
must use a different profile and preserve historical `cx` bytes.
