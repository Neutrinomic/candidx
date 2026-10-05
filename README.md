# CandidX

Canonical **ordinary Candid bytes derived from the data**, for typed records,
arrays, and application blocks. Profile: `cx`, package version 1.0.0. The spec
and frozen vectors define its fixed encoding contract. This standalone profile
is not currently an adopted Candid or ICRC standard.

The current Motoko compiler integration exposes only `to_candid_canonical`,
with no new flags or changes to ordinary `to_candid`. See
[the minimal integration and verification](research/minimal_integration.md)
and [current performance measurements](benchmarks/performance.md).

```candid
record { a = "abc" } : record { a : text }
record { a = "abc"; b = null } : record { a : text; b : opt text }
```

Both produce `4449444c016c016171010003616263`. Adding absent optional fields or
unselected variant cases does not change old canonical bytes, including inside
arrays and recursive declarations. Present data still changes them. There is
no application schema ID in the encoding. The ordinary Candid type table is
derived from the data and remains necessary for decoding.

Start with the [specification](spec/CanonicalValues.md) and self-contained
[Candid appendix](spec/CandidAppendix.md). The [verification](research/verification.md) and
[performance measurements](benchmarks/performance.md) explain the evidence and
limits.

## Packages

| Directory | Typed canonical encoding | Inbound ordinary-message normalization |
| --- | --- | --- |
| [ts/](ts/README.md) | `encodeCanonical(types, values)` with existing SDK IDL types | `canonicalize(bytes)` |
| [rust/](rust/README.md) | `encode_one(&value)` / `encode_args(args)` with existing `CandidType` values | `canonicalize(&bytes)` |
| [ocaml/](ocaml/README.md) | `Candidx.encode witness value` / `encode_args` with typed native records | `Candidx.canonicalize bytes` |
| [motoko/](motoko/README.md) | Temporary native library: `CandidX.canonicalize(to_candid(block))` | Native `CandidX.canonicalize(blob)` |
| [wasm/](wasm/README.md) | Standalone typed memory ABI; compiler operator `to_candid_canonical(v1, ...)` | Standalone `cx_canonicalize(pointer, length)` ABI |

Libraries only encode/normalize bytes. Applications select and implement the
hash. There is no hashing function or SHA-256 dependency in Motoko.
The direct typed encoders derive the used wire types and write a canonical
header and payload from the original values. They may revisit values to size
or emit the result. Typed encoding creates no intermediate ordinary message
and invokes no ordinary value decoder. Rust uses Candid's type/serializer
traits, and TypeScript uses the SDK's type visitor.
The [direct encoding notes](research/direct_encoding.md) explain the visits,
source stability requirements, and tests that disable the ordinary path.

The optional compiler patch adds `to_candid_canonical` to an isolated Motoko
checkout. This is its only public addition: it requires no new compiler flags
and leaves ordinary `to_candid` behavior unchanged. Generated Wasm derives the
used shape, measures the exact output size, and writes directly into one final
Motoko Blob. A small Rust RTS helper interns and joins shapes and writes the
type header; the generated Wasm traverses and emits the payload.

The operator also has source and IR interpreter implementations. It is a
proposal for upstream adoption, available through the supplied local patch and
`wasm/moc` wrapper. `CX_MOTOKO_DIR` selects another patched compiler directory.
The native Motoko library retains its existing encode-and-normalize fallback.
The four language codecs are independent. The standalone `cx.wasm` artifact
embeds the Rust codec and retains both its typed ABI and incoming-message
normalizer.

## Data contract

Supports all Candid scalar data kinds, public principals, options, vectors,
records, variants, and finite recursive values. Blob and `[Nat8]` are both
`vec nat8`, including empty. Empty nonbinary vectors erase their element type;
binary vectors retain `nat8`. Actual function/service reference values and
opaque references are outside the v1 profile; unused declarations are accepted.

Absent optional record fields disappear. Explicit `null`/`reserved` fields and
every present option wrapper remain. Scalar widths, selected case IDs, array
order, exact text bytes, and signed zero matter. All float NaNs normalize to one
positive quiet NaN per width. Fields use Candid numeric IDs, not string keys.
Hashing a Rust `HashMap` still requires choosing a stable entry order: Candid
vectors are ordered data.

This is stability under **unused schema extensions**, not all Candid subtype
coercions. An older typed decoder can discard new present fields. Verify the
full original canonical bytes before projecting into an older schema; sending
each canonical block as a `blob` preserves them. This produces a different
digest from the existing ICRC-3 `Value` hash. It is an encoding foundation for
a separate typed block format; archive endpoints and certification are outside
this library.

Ordinary Candid coercion rules reconstruct supported original values modulo
NaNs and ignored `reserved` data. The audited JavaScript SDK has three decoder
limitations; its optional `decodeCompatible` adapter addresses the tested
cases. See the [decoder audit](research/audit_spec.md). Rust and Motoko use their
existing decoders. Canonicalization should precede typed decoding.

## Local development

Requirements: Python 3.11+, Rust/Cargo, Node 20+ with npm, OCaml 4.14+ with Dune
3.10+ and Zarith, a Motoko compiler, and `motoko-core` sources. This workspace
used Rust 1.98.1, Node 24.11.1, core SDK 6.1.0, OCaml 5.4.1, Wasmtime 49.0.0,
Motoko source compiler 1.16.1, and a second Motoko compiler 0.16.3.

```shell
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
npm ci --prefix ts
.venv/bin/python tests/verify.py --moc /path/to/moc --core /path/to/motoko-core/src
.venv/bin/python tests/fuzz.py --seed 0xcad1d --cases 500 --moc /path/to/moc --core /path/to/motoko-core/src
.venv/bin/python benchmarks/run.py --label local --moc /path/to/moc --core /path/to/motoko-core/src
```

Build the OCaml package with `dune build` inside `ocaml/`. On this workspace the
test scripts can fall back to `nix develop` in the sibling Motoko checkout.
Adjust `--rust-toolchain` to an installed toolchain; when using Rustup, the
scripts set the matching `RUSTC`/`RUSTDOC` explicitly. The benchmark runner's
OCaml fallback uses that sibling Nix environment; standalone commands are in
the package documentation.

The shared seeded fuzzer generates matching typed records for the direct
encoders, checks them against an independent model and frozen vectors, and
compares external SHA-256 hashes. It decodes every encoder's output with Rust
and TypeScript and compares it with independently constructed original values;
native Motoko also checks original-value recovery. Separate mutation checks
verify that these comparisons catch changed data. It also checks the native
Motoko fallback and inbound normalizers. Pass `--runtime-moc wasm/moc` to additionally exercise
the direct compiler operator; the standalone Wasm artifact is checked when
available. Failures save the input schema, data, seed, and messages for
replay. [tests/README.md](tests/README.md) explains coverage and limitations.

The [performance report](benchmarks/performance.md) records the configurations
and source fingerprints behind saved encoding, decoding, and canister
allocation/GC measurements. Read those results against their recorded sources;
they do not measure every later compiler integration change.

Package registry publishing is disabled. The `cx` encoding contract is fixed: incompatible changes
must use a different profile and must not change historical `cx` bytes.

## License

MIT. See [LICENSE](LICENSE). Upstream dependencies and components linked into
the supplied runtime artifacts retain their own licenses; the Wasm package
includes [third-party notices](wasm/THIRD_PARTY_NOTICES.md).
