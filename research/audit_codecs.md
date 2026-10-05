# Rust / TypeScript normalizer audit, 4 October 2026

This is the historical audit of inbound normalizers and the adapter encoding
path before direct typed encoding. Its capture-time source hashes identify
those implementations; references below to final runs mean that earlier
capture. Current direct-encoder verification is in
[verification.md](verification.md), with architecture evidence in
[direct_encoding.md](direct_encoding.md).

The corrected codecs passed three shared seeded runs and an independent
Rust / TypeScript stress suite. The shared runs serialize the same record values
with the ordinary Rust `candid` encoder, JavaScript `IDL.encode`, and native
Motoko `to_candid`. Every resulting message, and each canonical message, is
checked by all four normalizers. SHA-256 is computed by this test application
with Python `hashlib`; the libraries emit bytes.

| Seed | Random records | Typed cases including extensions and boundaries | Extension pairs | Messages checked by each normalizer |
| --- | ---: | ---: | ---: | ---: |
| `0xcad1d` | 500 | 1,124 | 562 | 4,510 |
| `0x13579bdf` | 500 | 1,124 | 562 | 4,510 |
| `20261004` (`0x135288c`) | 500 | 1,124 | 562 | 4,510 |

All three runs passed byte equality, external digest equality, idempotence,
recursive unused-schema extension invariance, and seven directed distinctions
for option presence, scalar kind, signed zero, binary empty data, exact Unicode,
and array order. Each native Motoko program ran in two fresh Wasm instances.
Rust and native Motoko recovered the original supported values at the original
schema. Rust re-encoding at that schema produced byte-identical ordinary SDK
messages. TypeScript's `decodeCompatible` recovered each original value from
both ordinary and canonical input, including exact BOM text and recursive
option presence. The vanilla JavaScript SDK caveats remain recorded separately.
Each run also cross-normalizes fourteen directed float32 wire records covering
signed zero, subnormals, normal bounds, infinities, and signaling / quiet /
negative NaNs. Native Motoko's source `Float` is float64; these float32 cases
are explicitly wire-level tests.

The saved final reports are `_out/fuzz-cx-final-cad1d-500/report.json`,
`_out/fuzz-cx-final-13579bdf-500/report.json`, and
`_out/fuzz-cx-final-135288c-500/report.json`. Each directory includes the exact JSON
schemas and values, generated statically typed Motoko sources, native binaries,
ordinary SDK messages, canonical messages, application hashes, and test output.
The generator and replay entry point is [tests/fuzz.py](../tests/fuzz.py).
All final reports have identical source fingerprints, no source changes during
execution, and hashes matching the final workspace. Their aggregate report is
the [historical verification summary](verification-normalizers.md): 3,372 typed cases, 1,686 extension pairs, and
13,530 cross messages checked by each normalizer. The final runs also check the
import-free Rust/Wasm artifact and the actual linked Motoko runtime primitive.

From the repository root, using a Python environment with `wasmtime` installed:

```sh
../motoko_candid/research/candid-canonical/.venv/bin/python tests/fuzz.py --seed 0xcad1d --cases 500 --batch-size 128 --runtime-moc wasm/moc
../motoko_candid/research/candid-canonical/.venv/bin/python tests/fuzz.py --seed 0x13579bdf --cases 500 --batch-size 128 --runtime-moc wasm/moc
../motoko_candid/research/candid-canonical/.venv/bin/python tests/fuzz.py --seed 20261004 --cases 500 --batch-size 128 --runtime-moc wasm/moc
```

`--input <saved corpus.json>` replays exact schemas and values. A failure records
its seed, case ID, raw message, expected bytes, and actual output in
`failure.json`. Native compiler/runtime failures retain their generated source
and command output. Rust is built with explicit `RUSTC` / `RUSTDOC` paths from
toolchain `1.98.1`; the fuzz example is gated by the `fuzz` Cargo feature.

Fuzzing found four implementation defects that were corrected:

1. **Rust variant join guard shadowing.** The pattern
   `(Some(a), None) if a.op == -21` inspected the payload child named `a`
   instead of the outer variant shape. A missing right-hand case consequently
   acquired an optional payload. The first shared seed exposed a typed decode
   error in a nested variant array: wire `opt nat8`, expected `nat8`.
   The corrected pattern names the child separately and checks the outer
   constructor.
2. **The same OCaml variant join defect.**
   `Some a, None when a.kind = -21` had the same binding error. For
   `record { data = vec { variant { a = 255 }; variant { b = 0 } } }`
   at `record { data : vec variant { a : nat8; b : nat8 } }`, the ordinary and
   canonical bytes must both be
   `4449444c036c01aaac8d9304016d026b02617b627b01000200ff0100`.
   OCaml previously returned
   `4449444c046c01aaac8d9304016d026b026103627b6e7b0100020001ff0100`.
   Corrected shared runs include this case and its reverse order.
3. **JavaScript parser VM argument limit.** A valid unused function type with
   131,072 `null` arguments threw `RangeError: Maximum call stack size exceeded`
   at `edges.push(...args, ...rets)`. The 131,084-byte input satisfies the
   declared collection and byte limits, and must normalize to
   `4449444c0000`. Iterative pushes now accept it. The independent suite retains
   this regression case.
4. **Buffer aliasing in optimized integer normalization.** The scalar integer
   fast path used `input.slice()` and cleared a continuation bit in that slice.
   Node `Buffer.slice()` shares caller storage. Normalizing a padded integer
   could therefore change the original message. For example, the input Buffer
   `4449444c00017d8000` (nat zero with padding) was mutated to
   `4449444c00017d0000`, which has trailing payload. The independent malformed
   suite detected a cross-language discrepancy caused by this mutation. The
   corrected path creates an explicit `Uint8Array` copy. Regression checks
   cover input nonmutation on success and failure, plus sliced Buffers.

The failed runs are retained under `_out/fuzz-cad1d/`,
`_out/fuzz-cad1d-10/`, and `_out/fuzz-cad1d-100/`. They show the diagnostic and
byte disagreements observed before the fixes.

The independent suite is [ts/test/audit_codec.mjs](../ts/test/audit_codec.mjs).
The optimized codec run passed 12,811 Rust / TypeScript inputs: 5,500 typed
SDK messages, 2,750 recursive schema-extension pairs, 2,222 exact float-bit
cases, 80 padded integer cases, four 4,096 / 4,097-byte integer boundaries,
and 5,000 malformed-message mutations, of which 476 were valid messages.
It also checks the wide unused-function regression, input nonmutation, and
compatible tuple recovery. Its report is `_out/audit-codec-final-report.json`;
all inputs are exported to `_out/audit-codec-final-cases.json`.

```sh
cd ts
npm run build
node test/audit_codec.mjs --iterations 2500 --rust-probe ../rust/target/debug/examples/probe --report ../_out/audit-codec-final-report.json --export-cases ../_out/audit-codec-final-cases.json
```

The ordinary JavaScript decoder in installed `@icp-sdk/core` 6.1.0 has these
remaining recovery limitations:

* **Sparse tuples.** `IDL.Tuple(IDL.Opt(IDL.Nat), IDL.Text)` with `[[], "ok"]`
  canonicalizes to a record containing only numeric field `1`. The decoder
  rejects it as a non-tuple. An absent trailing optional component instead
  produces a tuple-length mismatch. Numeric records are valid ordinary Candid
  representations; the SDK's tuple decoder requires dense components.
* **Optional recursive aliases.** With `r = IDL.Rec()` filled by
  `IDL.Opt(IDL.Record({ item: IDL.Null, next: r }))`, the value
  `[{ item: null, next: [] }]` canonicalizes to a present option whose record
  keeps `item : null` and omits absent `next`. The SDK's missing-record-field
  branch does not unfold `RecClass` to discover `OptClass`, and throws.
  The containing option catches that error and silently decodes as `[]`.
  The same failure can throw directly when the missing alias is a top-level
  record field. Rust and native Motoko reconstruct these values correctly.
* **Leading UTF-8 BOM.** Ordinary `IDL.decode`, before any canonicalization,
  changes `"\uFEFFstart"` to `"start"`. The input and correct canonical bytes are
  `4449444c00017108efbbbf7374617274`. CandidX retains the text bytes. SDK text
  decoding strips the leading BOM.

The shared seed reports contain respectively 350, 306, and 326 JavaScript
recovery caveat events. Of these, 332 / 298 / 322 are recursive alias failures
and 18 / 8 / 4 are losses already present when decoding ordinary input.
These are distinct from the passing canonical byte and digest checks.
The `decodeCompatible` adapter corrects all three cases with a local copy of
the expected schema; the shared fuzz suite checks that caller-owned schemas
retain identical ordinary encoding after decoding. Vanilla SDK behavior remains
available and unchanged.

The initial draft also removed required `null` record fields. That exposed
another SDK omission limitation and could silently erase a surrounding present
option. The fixed `cx` contract retains explicit `null` and `reserved` record fields
and removes actual absent optional fields. The dedicated required-null record
and option tests now recover their values.

Packaging checks confirmed that TypeScript's standalone root exports contain
no SDK imports, while `/candid` provides the typed adapter. `npm pack --dry-run`
includes the standalone module, typed adapter, compatibility decoder, and
declarations. The SDK peer is optional for
standalone use. Rust's default typed API and standalone no-default-feature API
remain separate; the development-only fuzz example requires `fuzz`. The fuzz
example passed Clippy with warnings denied under Rust 1.98.1.

The fuzz coverage includes explicit null/reserved values, integer kinds and
bounds, 512-bit arbitrary integers, floats with NaNs / infinities / signed zero /
subnormals, exact UTF-8, principals of several lengths, empty and nonempty
blobs, generic empty vectors, records in vectors, variants with mixed selected
cases, nested options, finite recursive linked records, and recursively added
absent fields / unselected cases. Collection, byte, type-table, integer, and
nesting limits remain explicit implementation bounds.
