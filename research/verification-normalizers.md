# Local verification

Historical validation of the inbound normalizers and encode-and-normalize adapters before the direct typed encoders. Capture-time source hashes remain in the reports. Current direct-encoding validation is in [verification.md](verification.md).

The value-derived `cx` implementations passed three final
fixed-corpus checks and three final shared seeded fuzz runs. These results were
captured locally before the repository's initial publication. No issue, PR, or
forum posting was part of verification. The fixed encoding contract
and 1.0.0 packages are MIT licensed. Generated testing is not a formal proof.

## Fixed corpus, repeated three times

| Pass | Compiler executing native Motoko | Result |
| --- | --- | --- |
| [1](results/final-pass-1.json) | source 1.16.1-44-g9384e1076 | Pass |
| [2](results/final-pass-2.json) | source 1.16.1-44-g9384e1076 | Pass |
| [3](results/final-pass-3.json) | installed 0.16.3 | Pass |

Each pass checks 476 messages through Rust, TypeScript, OCaml, native
Motoko, and the standalone Rust/Wasm embedding against frozen outputs and an
independent Python model. The Wasm backend reuses Rust's codec. Native Motoko
programs execute in three fresh Wasm instances, not only under the source
interpreter. The suite includes 61 typed cases, 27 real typed evolution probes,
19 positive binary edge cases, 183 type-table transformations, and 30
malformed/unsupported/resource-limit inputs. Twelve identity groups and ten
distinct-data pairs check the requested behavior.

The fixture is
[`tests/vectors.json`](../tests/vectors.json), SHA-256
`878efe14b3f4fb9727dc409bb6d4be20571e46e3afb67e20d7f382975fa4bf86`.
The Rust package contains a portable copy; the runner requires it to match the
shared file exactly. Initial collection is not used to update expected outputs
during verification. Each report captures implementation/test/spec source
hashes and tool versions.

Full local build checks ran Rust tests with and without the Candid dependency,
Clippy with warnings denied, TypeScript compilation and 14 Node tests, OCaml
Dune builds with warnings denied, and native Motoko compilation/execution.
The final library also typechecks under Motoko 0.16.3 with zero warnings.

Each final pass additionally runs 870 native Motoko optimization regressions
(473 positive, 397 negative) in three fresh Wasm instances. These cover packed
float NaNs and signed zero, aligned canonical input versus shifted/padded
input, UTF-8 validation, fixed-width and scalar integer boundaries, wide
optional records, depth 255/256/257, and the two-million-step limit.

## Shared seeded fuzzing

| Seed | Actual typed records | Unused-schema extension pairs | Messages per normalizer | Result |
| --- | ---: | ---: | ---: | --- |
| [`0xcad1d`](results/fuzz-cad1d.json) | 1,124 | 562 | 4,510 | Pass |
| [`0x13579bdf`](results/fuzz-13579bdf.json) | 1,124 | 562 | 4,510 | Pass |
| [`20261004`](results/fuzz-135288c.json) | 1,124 | 562 | 4,510 | Pass |

Total: 3,372 matching typed records, 1,686 extension pairs, and 13,530 messages
through each of four independently implemented normalizers, the standalone
Wasm embedding, and the actual linked Motoko runtime primitive. Ordinary wire
messages come from actual Rust Candid 0.10.37, core SDK 6.1.0, and native Motoko
`to_candid`. Fourteen directed float32 wire records are added per seed, since
Motoko's source `Float` maps to float64. Seven directed distinct-data pairs
per seed check presence, scalar kinds, signed zero, exact Unicode, binary
empty data, and array order.

All canonical bytes and application-side SHA-256 hashes agree. Rust's
original-schema ordinary re-encoding is byte-identical, native Motoko recovers
the original supported values, and TypeScript's optional `decodeCompatible`
recovers them from both ordinary and canonical input. Every native program is
replayed in two fresh Wasm instances. All final fuzz reports have identical
source fingerprint maps, still matching the library sources at capture.

Vanilla SDK decoder caveats are separately recorded: 350, 306, and 326 events
for the three seeds. They are not counted as successful ordinary SDK recovery;
the compatible adapter must recover every original value.

Saved corpora, generated Motoko source, raw/canonical bytes, binary programs,
failure artifacts, and logs remain in local `_out/fuzz-cx-final-*/`. The seed and
source-fingerprinted [generator](../tests/fuzz.py) reproduce those cases.
An independent [oracle replay](../tests/replay_oracle.py) checks the complete
saved data before/after normalization as well as canonical bytes and hashes;
its captured result is [fuzz-oracle.json](results/fuzz-oracle.json).

## Independent audits and package checks

The [specification audit](audit_spec.md) verified 9,040 generated typed messages,
4,520 schema-extension pairs, normalized-data preservation, idempotence, and
Rust recovery of all 9,044 messages including minimal SDK caveats. The optional
JavaScript decoder adapter recovered all 9,044 values and passed focused tests
for mutual recursion, nested options, frozen schemas, tuples, and projection.

The [Rust/TypeScript audit](audit_codecs.md) passed 12,811 messages, including
2,222 exact float-bit cases, long padded integers, 4,096/4,097-byte limits,
5,000 malformed mutations, a wide unused function signature, and input
nonmutation. [Captured report](results/audit-codec-final-report.json).

Fuzzing discovered four defects that were corrected and retained as regression
tests: Rust/OCaml variant-join guard shadowing, a JavaScript spread exceeding
the VM argument limit, and Node Buffer aliasing during integer normalization.
The audit documents minimal reproductions and explains the three existing SDK
decoder behaviors separately.

[Package validation](results/packaging.json) tested a locally packed Rust crate
with standalone and all features, imported a packed TypeScript package without
the SDK, then tested its typed adapter with SDK 6.1.0. OCaml's package-specific
`dune build -p candidx` passed. License texts and declarations are included.
No package was published.

The [Wasm integration validation](results/wasm-validation.json) covers the
import-free ABI, idempotence, readable malformed-input errors, arena capacity
reuse, scratch exhaustion, and the actual patched compiler/runtime. Literal
messages also exercise the OCaml source interpreter. The supplied patch
applies to its pinned upstream base, and native/JS compiler builds pass; browser
JS interpreter execution is not part of this validation.

The original Motoko/compiler and upstream baseline suites were also built/run
during source research without production source changes. Their earlier
fixed-schema canonicalization experiments are historical, superseded by this
value-derived contract. They are not counted as conformance proof for this
profile.

## Reproduce

See [tests/README.md](../tests/README.md) and package READMEs for setup. The main
local commands, using a Python environment with Wasmtime installed, are:

```shell
.venv/bin/python tests/verify.py --moc /path/to/moc --core /path/to/core/src --report _out/pass.json
.venv/bin/python tests/fuzz.py --seed 0xcad1d --cases 500 --batch-size 128 --runtime-moc wasm/moc
.venv/bin/python tests/fuzz.py --seed 0x13579bdf --cases 500 --batch-size 128 --runtime-moc wasm/moc
.venv/bin/python tests/fuzz.py --seed 20261004 --cases 500 --batch-size 128 --runtime-moc wasm/moc
.venv/bin/python tests/replay_oracle.py _out/fuzz-cad1d-500 _out/fuzz-13579bdf-500 _out/fuzz-135288c-500 --report _out/oracle.json
.venv/bin/python tests/packaging.py
python3 tests/audit_spec.py --rust
```

Supply compiler/core paths to the fuzz commands when siblings differ from this
workspace. `--input <saved corpus.json>` replays exact records. Benchmarks and
their limits are reported in [performance.md](../benchmarks/performance.md).

This evidence does not establish all-input correctness, limits on
every architecture, or measured IC cycle charges. Actual function/service and
opaque reference values remain outside the v1 profile. Typed projection can
still lose present unknown data; applications must verify original canonical
bytes before decoding into smaller schemas. The `cx` identity rules are fixed;
incompatible future rules must use a different profile.
