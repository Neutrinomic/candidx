# Minimal Motoko compiler integration

- Patch: [motoko-compiler.patch](../wasm/motoko-compiler.patch)
- Build instructions: [wasm/README.md](../wasm/README.md#direct-motoko-compiler-encoding)

The patch adds only `to_candid_canonical(...) : Blob`. It adds no compiler
flag, public Prim normalizer, hash function or standalone Rust dependency.
Existing ordinary Candid encoding and decoding remain unchanged.

The compiler generates source-type-specific Wasm for three internal visits:
derive used shapes and array joins, measure the payload under those shapes,
and write into an exactly sized final Blob. Rust RTS helpers retain only
interned shapes, join scratch and canonical type-header state. There is no
heap-layout descriptor, inbound parser, payload tree or complete output Vec.
The compiler reuses its existing primitive writers, field access and native
text/Blob handling. Fixed-width sizing and primitive-array shape derivation
use bulk paths. Big-integer magnitudes are bounded before signed sizing can
allocate a copy.

The `cx` contract, 61 golden encodings, standalone Rust/TypeScript/OCaml/Wasm
packages and portable native Motoko fallback remain unchanged. The standalone
Wasm ABI still provides its own typed encoder and inbound byte normalizer.

## Review and execution evidence

- The 61-file patch applies to pinned upstream
  `9384e1076bf71c1c44b78627b9191011d8543deb` with 69 exact hunks and no fuzz.
  Independent replay matches every candidate file and manifest source hash.
- Removing only the new compiler additions leaves `compile.ml` identical to
  pristine. Ordinary serializer, decoder and stream helpers, Prim, flags,
  allocator, bigint, text, memory and GC sources match pristine.
- Nine targeted compiler tests pass, including existing ordinary Candid and
  LEB128 controls. Expectations were generated with `test-runner`, preserving
  its human-output whitespace and trap formatting.
- Ten dependency-free shape/header tests pass with Rust warnings denied,
  including 2,916 optional-presence join comparisons, direct header writes,
  scratch accounting, reachable-table limits and depth boundaries.
- The [bridge](results/minimal-bridge.json) checks 95 source-interpreter/Wasm
  cases, two fresh Wasm instances, original-value recovery, three cold
  4,096-byte integer positives, depth-256 empty vectors and ten resource traps.
  The source interpreter's ordinary encoder/decoder remains unavailable and
  is reported as such; compiled Wasm checks use the existing decoder.
- Native and JS compiler builds pass. The JS interpreter and JS-built Wasm
  fixture pass, and both reject the removed Prim API. The pre-existing JS
  `caml_unix_map_file_bytecode` build warning remains recorded.
- [43 architecture checks](results/minimal-architecture.json), the
  [full fixed suite](results/minimal-fixed.json), and three 500-case seeds pass.
  They cover 3,390 matching typed cases, 1,695 unused-schema extension pairs and
  44,400 independent Rust/TypeScript original-value recovery checks. All 83
  proof-source fingerprints stayed unchanged during each run.
- [Independent replay](results/minimal-fuzz-oracle.json) verifies 36,453 saved
  messages for complete data preservation, canonical bytes and external
  SHA-256. The known vanilla SDK decoder caveats remain separate from exact
  recovery through its compatibility adapter.

Source review also confirms the runtime lifetime assumptions: generated
helpers have no GC safepoints, heap growth does not move source data, the final
Blob is allocated after exact sizing, and shape context is dropped before any
later GC increment. Real-canister probes retain output across collection and
recover the original typed source. This is testing and review evidence, not a
formal proof or security audit.

## Measurements and practical limits

[Matched CPU captures](../benchmarks/results-minimal-cpu.json) compile the same
native source with the previous Rust heap bridge and the new generated encoder.
Each of three sequential passes has seven trials per phase. Record workloads
save 41% allocation and about 9% Wasmtime fuel; observed wall-time savings are
about 2%. Small blocks save 35% allocation and 23% wall time. The binary workload
saves 49% allocation and 48% wall time. Fuel is not IC instructions or cycles.

The [current performance report](../benchmarks/performance.md) also records
actual IC instructions, single-operation heap growth and GC stress using three
fresh canisters per workload/implementation. Allocation is cumulative requested
heap space; occupied heap after GC includes current-partition garbage and
bookkeeping and is not exact live-object memory. Stress batches retain their
20,000-call and five-billion-instruction limits.
GC instruction accounting reads and sums each cleanup/increment interval;
the RTS counter reports the last message, so snapshot subtraction is invalid.
Those intervals include scheduling overhead even when no collection occurs.

The release RTS shrinks from 283,788 to 219,169 bytes against the previous
bridge. A [simple ordinary-encoding program](results/minimal-ordinary-code-size.json)
without the new operator is 216,023 bytes, versus 202,254 bytes with pristine
upstream and 277,280 with the previous bridge. The helper module still adds
13,769 bytes to this probe's bundled runtime; unchanged ordinary behavior
does not imply an identical whole Wasm artifact. Its exports remain only
`_start`, memory and table; internal canonical helpers are not exported by the
program.

The repository includes the patch, artifacts, docs and verification reports.
The discussion drafts remain private and excluded from commits. No package
publication, upstream issue, PR or forum post accompanies this integration.
