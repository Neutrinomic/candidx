# Performance and heap measurements

The primary measurements exercise direct canonical encoding from original
typed values in Rust, TypeScript, OCaml, and the linked Motoko runtime. The
temporary native Motoko library retains ordinary encoding followed by
normalization. Encode-and-normalize controls remain measured separately.
Hashing is outside every timed operation. Wasm embeddings reuse Rust; they
are integration paths rather than additional independent codecs.

## Encoding and decoding time

Ratios are medians of three within-run ratios, each with seven trials. `2.0x` means twice ordinary encoding wall time. OCaml reports direct microseconds because it has no ordinary binary baseline.

| Workload | Rust direct / ordinary | TypeScript direct / ordinary | OCaml direct, µs | Native Motoko fallback / ordinary | Compiled Motoko direct / ordinary |
| --- | ---: | ---: | ---: | ---: | ---: |
| small | 1.67x | 0.39x | 13.01 | 34.71x | 2.53x |
| records-128 | 4.36x | 0.52x | 283.79 | 13.28x | 1.40x |
| blob-64k | 1.04x | 0.35x | 25.88 | 33.37x | 1.11x |
| records-1024 | 5.03x | 0.57x | 2201.62 | 12.78x | 1.38x |

On these workloads, the TypeScript direct visitor takes 35–57% of ordinary
SDK encoding time. The compiled Motoko direct path takes 1.11–2.53 times its
generated ordinary encoder. Rust's record path remains slower than both its
ordinary encoder and encode-and-normalize control; its allocation savings
are reported separately below. The native Motoko fallback remains expensive.

Microseconds per operation, median of three run medians. Phases are timed separately and need not sum. `motoko-wasm` is the actual linked Rust encoder reading Motoko heap values; `motoko` is the native library fallback.

| Backend | Workload | Ordinary encode | Direct canonical encode | Normalize | Encode-and-normalize | Ordinary decode | Decode canonical |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| rust | small | 5.39 | 8.98 | 2.79 | 8.32 | 8.19 | 7.60 |
| rust | records-128 | 23.46 | 102.99 | 30.38 | 55.01 | 116.52 | 117.13 |
| rust | blob-64k | 7.47 | 7.84 | 3.30 | 11.06 | 206.75 | 205.66 |
| rust | records-1024 | 150.60 | 771.48 | 228.83 | 379.23 | 834.64 | 835.09 |
| ts | small | 262.28 | 101.98 | 79.62 | 335.68 | 136.59 | 107.68 |
| ts | records-128 | 2709.48 | 1500.65 | 510.85 | 3852.21 | 1309.32 | 1261.38 |
| ts | blob-64k | 508.27 | 179.74 | 168.06 | 737.21 | 1442.78 | 1270.23 |
| ts | records-1024 | 17979.27 | 10656.95 | 3960.37 | 24300.27 | 9851.27 | 9765.93 |
| ocaml | small | — | 13.01 | 8.69 | — | — | — |
| ocaml | records-128 | — | 283.79 | 114.35 | — | — | — |
| ocaml | blob-64k | — | 25.88 | 34.90 | — | — | — |
| ocaml | records-1024 | — | 2201.62 | 1044.90 | — | — | — |
| motoko | small | 5.41 | — | 182.10 | 187.60 | 9.39 | 9.39 |
| motoko | records-128 | 145.92 | — | 1817.57 | 1945.66 | 256.94 | 260.75 |
| motoko | blob-64k | 61.55 | — | 1947.13 | 2039.57 | 34.64 | 33.94 |
| motoko | records-1024 | 1113.85 | — | 12964.28 | 14213.86 | 2020.71 | 2036.48 |
| motoko-wasm | small | 5.36 | 13.66 | 12.64 | 17.92 | 9.38 | 9.28 |
| motoko-wasm | records-128 | 147.20 | 208.56 | 122.31 | 269.16 | 256.54 | 259.86 |
| motoko-wasm | blob-64k | 61.97 | 69.04 | 167.55 | 228.73 | 33.57 | 33.54 |
| motoko-wasm | records-1024 | 1129.16 | 1547.68 | 748.70 | 1858.27 | 2009.72 | 2014.69 |

The ordinary SDK decoder supports these benchmark schemas. Its optional compatibility adapter is measured separately; BOM/tuple/recursive-option caveats are documented in the decoder audit.

| Workload | SDK decode canonical, µs | decodeCompatible, µs |
| --- | ---: | ---: |
| small | 107.68 | 118.78 |
| records-128 | 1261.38 | 1265.67 |
| blob-64k | 1270.23 | 1280.52 |
| records-1024 | 9765.93 | 9608.49 |

Motoko's OCaml compiler generates its ordinary encoder/decoder as Wasm and
uses helpers from its own Rust runtime. It does not call the Rust Candid crate.
Those generated codecs are the baseline for both Motoko paths. OCaml's native
direct encoder and normalizer have no audited standalone ordinary binary
encoder/decoder, so their absolute times are reported without an invented ratio.

Rust uses optimized builds and `black_box`; TypeScript uses warmed V8 and
observable checksums; OCaml uses native compilation. Wasmtime 49.0.0 runs the
Motoko workloads with fuel enabled, excluding instantiation/JIT and including
small loop-marker overhead. Absolute times across runtimes are not a language
ranking. Fuel is not IC instructions or cycle charges.

## Real canister instructions and allocation

The [direct runtime capture](results-heap-rts-direct.json) runs actual canister
Wasm through PocketIC 10.0.0, using three fresh canisters per workload and
`performanceCounter(0)` around each synchronous operation. These are IC
instructions, not cycles. RPC, installation, verification, and hashing are
outside the measured operation. The table subtracts the measured 72-byte
statistics-record control from allocation deltas; the raw report retains it.

| Workload | Direct instructions | Direct / ordinary | Direct allocated bytes | Encode-and-normalize allocated bytes |
| --- | ---: | ---: | ---: | ---: |
| small | 100,451 | 2.62x | 5,032 | 5,752 |
| records-128 | 1,817,339 | 1.59x | 29,968 | 66,040 |
| blob-64k | 186,969 | 1.31x | 134,712 | 266,320 |
| records-1024 | 14,169,976 | 1.56x | 188,848 | 458,376 |
| nat64-4096 | 5,959,521 | 12.40x | 66,584 | 263,544 |
| text-64k | 242,846 | 1.83x | 131,640 | 262,928 |
| unicode-64k | 1,856,632 | 14.00x | 131,640 | 262,928 |

The 1,024-record block allocates 59% less through the direct heap visitor than
through the pipeline. Scalar visitation and Unicode validation remain costly:
the numeric-vector and multibyte-text workloads show why the block ratios
cannot be generalized to every value. This implementation does not establish
that direct encoding is always faster or cheaper.

The driver retains source data and output, clears previous results, forces four
GC increments before and after the operation, and records allocation,
occupied heap, reclaimed bytes, GC instructions, and linear-memory capacity.
It asserts no reclamation during the single operation, so the measured heap
increase bounds that operation's temporary occupancy above its starting heap.

Post-GC occupied heap is not exact live-object memory: Motoko leaves garbage
in the current 64 MiB allocation partition and adds collector bookkeeping.
Repeated direct-encoding batches therefore target 72 MiB allocation, capped
at 20,000 calls and five billion estimated instructions. All six workloads
that crossed a partition reclaimed temporary objects in all three trials;
retained outputs still decoded to the original values. The Nat64-vector batch
hit its instruction cap before crossing a partition and is not counted as a
separate reclamation proof. Single-operation capacity was 128 MiB; stress
commonly retained 192 MiB, including runtime reservations. Neither is codec
live heap or process RSS.

The unchanged native Motoko fallback has a separate
[heap capture](results-heap-native-final.json). Its 1,024-record normalization
allocates 2,505,728 bytes after control subtraction. This older capture
measures normalization of preencoded bytes, so it is not directly comparable
to the direct visitor's complete encoding allocation. Historical optimization
comparisons remain in [the adapter report](performance-adapters.md).

## Native Rust allocation and CPU work

The [Rust CPU/allocation capture](../research/results/rust-typed-cpu.json)
compares the final typed encoder with the pipeline and its earlier uncached
direct implementation. These are allocator-request totals, including full
reallocation requests, not RSS or exact live heap. Existing source data and
fixtures are outside the measurement.

| Workload | Direct requested bytes | Pipeline requested bytes | Reduction |
| --- | ---: | ---: | ---: |
| small | 9,491 | 11,898 | 20% |
| records-128 | 33,998 | 78,801 | 57% |
| blob-64k | 73,888 | 337,837 | 78% |
| records-1024 | 176,919 | 452,426 | 61% |

The typed encoder reuses shape buffers and caches resolved source descriptors
after 32 actual serializer callbacks. Its cache is bounded to 1,024 nodes and
4,096 aggregate fields/cases; larger valid schemas use a streaming fallback.
Only used variant children are registered. This avoids allocating the full
unused schema and preserves the same bytes and resource behavior.

That final CPU change improved the two record workloads by 8–10% in its
isolated before/after capture. Small and binary workloads changed slightly in
the opposite direction. Rust direct record encoding still takes about twice
the pipeline time in that capture, despite requesting much less allocation.
Two visits through generic serializer callbacks have a measurable cost.

## JavaScript occupied heap

The [V8 memory capture](results-ts-memory-direct.json) runs 48 fresh Node
processes: four workloads, four phases, and three trials. Inputs, fixtures,
operations, and output remain explicitly rooted. SDK caches are warmed outside
the operation, with four forced collections before and after it.

| Workload | Direct occupied-heap increase | Pipeline occupied-heap increase | Direct ArrayBuffer increase | Pipeline ArrayBuffer increase |
| --- | ---: | ---: | ---: | ---: |
| small | 84,096 | 262,480 | 1,104 | 4,007 |
| records-128 | 708,088 | 1,705,560 | 22,034 | 72,413 |
| blob-64k | 375,376 | 180,736 | 262,751 | 932,231 |
| records-1024 | 1,036,504 | 3,427,920 | 133,557 | 310,778 |

These are operation-end occupancy deltas, not total allocation, guaranteed
peak heap, or RSS. V8 may collect during an operation; JIT and GC noise can
produce negative retained-heap deltas. ArrayBuffers are tracked separately
from V8 heap. Direct encoding improves both counters for the record blocks,
but the binary workload occupies more V8 heap while using fewer ArrayBuffer
bytes. A single memory figure would hide that tradeoff.

## OCaml allocation and standalone Wasm arena

OCaml uses `Gc.allocated_bytes`, measuring newly allocated bytes rather than live or peak heap. Inputs and fixtures remain outside these operations.

| Workload | Direct allocated bytes/op | Normalizer allocated bytes/op |
| --- | ---: | ---: |
| small | 27,536 | 19,304 |
| records-128 | 632,945 | 289,809 |
| blob-64k | 274,544 | 339,912 |
| records-1024 | 4,985,057 | 2,213,237 |

The import-free memory32 [typed-ABI capture](results-wasm-memory-direct.json)
uses a resettable arena with no collector:

| Workload | Arena high-water including host input, bytes | Retained linear memory, bytes |
| --- | ---: | ---: |
| small | 3,220 | 1,114,112 |
| records-128 | 69,412 | 1,179,648 |
| blob-64k | 132,624 | 1,245,184 |
| records-1024 | 526,012 | 1,638,400 |

Each fresh instance repeated its workload 100 times with identical scratch
high-water and capacity. Arena measurements include copied typed host input,
reconstructed typed nodes, output, and temporaries until reset; Python source
preparation is outside them. Capacity stays at the largest workload and is not
live heap. This ABI marshals typed nodes and has different allocation costs
from Motoko's direct native-heap visitor. Both embeddings enforce a tested
256 MiB scratch limit.

## Workloads and reproducibility

The four block workloads have a `nat64` timestamp, optional 32-byte parent
hash, transactions with a public principal, `nat` amount `2^200 + index`,
optional byte memo, and an absent optional text note. Small uses four
transactions and 32-byte memos; records-128 uses 128 and 32-byte memos;
blob-64k uses one with a 65,536-byte memo; records-1024 uses 1,024 and empty
memos. Every third transaction has a present memo; the binary case always does.

| Workload | Ordinary bytes (Rust fixture) | Canonical bytes |
| --- | ---: | ---: |
| small | 305 | 296 |
| records-128 | 5,875 | 5,866 |
| blob-64k | 65,676 | 65,667 |
| records-1024 | 35,262 | 35,253 |

Canonical bytes agree across backends and original-value decoder recovery is
checked outside timed loops. Ordinary headers can differ between languages.
These sizes are not an ICRC-3 `Value` comparison or a universal compression
claim. Extra canister probes use 4,096 Nat64s, 64 KiB ASCII, and 64 KiB UTF-8
containing 16,384 globe characters.

The three final timing runs use identical source fingerprints and run
sequentially without competing project builds or tests. Seven trials per
phase retain their full ranges. The Linux x86_64 host has no exclusive-host
or CPU-frequency guarantee; deployment capacity needs its own measurements.

Build the optional compiler integration described in [wasm/](../wasm/README.md).

```shell
.venv/bin/python benchmarks/run.py --label direct-final-1 --moc /path/to/moc --core /path/to/core/src --wasm-moc wasm/moc
.venv/bin/python benchmarks/run.py --label direct-final-2 --moc /path/to/moc --core /path/to/core/src --wasm-moc wasm/moc
.venv/bin/python benchmarks/run.py --label direct-final-3 --moc /path/to/moc --core /path/to/core/src --wasm-moc wasm/moc
.venv/bin/python benchmarks/summarize.py --prefix direct _out/benchmarks-direct-final-1.json _out/benchmarks-direct-final-2.json _out/benchmarks-direct-final-3.json
.venv/bin/python benchmarks/runtime_sources.py heap --output _out/heap-rts.mo
wasm/moc --package core /path/to/core/src _out/heap-rts.mo -o _out/heap-rts.wasm
node benchmarks/heap.mjs --wasm _out/heap-rts.wasm --source _out/heap-rts.mo --direct true --stress-phase canonical_encode_direct --server /path/to/pocket-ic --label direct-local --trials 3
node benchmarks/ts_memory.mjs _out/bench-rust-direct-final-1.json local
.venv/bin/python benchmarks/wasm_memory.py --typed _out/bench-rust-direct-final-1.json
cargo run --release --locked --manifest-path rust/Cargo.toml --example profile -- _out/bench-rust-direct-final-1.json
```

Captured final timing: [run 1](results-direct-1.json),
[run 2](results-direct-2.json), [run 3](results-direct-3.json), and
[aggregate](results-direct-summary.json). Historical adapters, earlier
optimizations, and restoration patches remain documented separately in
[performance-adapters.md](performance-adapters.md). Apply restoration patches
only in disposable copies: those baselines are research controls, not current
conformance implementations.
