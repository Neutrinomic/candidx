# Performance and heap measurements

This document preserves historical adapter measurements and the previous
compiler integration. The current generated Motoko encoder and matched
comparison are described in [performance.md](performance.md) and
[minimal_integration.md](../research/minimal_integration.md). Its compiler
exposes only `to_candid_canonical`; the old Prim inbound normalizer is removed.
Old runtime reproduction commands require the earlier CandidX revision
`2ab5573b2f6951553d4b4d5c00a5f402aefb16f2`.

The older raw `gc_instructions` fields subtract last-message collector counters
and cannot be interpreted as cumulative GC cost. Current captures read and
sum each cleanup/increment interval; allocation, occupied-heap and cumulative
reclaimed-byte measurements retain their stated meaning.

Historical encode-and-normalize measurements, captured before the direct typed encoders. Source fingerprints in the reports identify the measured implementations. Current direct-encoding measurements are in [performance.md](performance.md).

These measurements compare existing ordinary typed Candid codecs with the
fixed `cx` profile's **encode then normalize** adapters. They do not time
hashing or a future direct typed canonical emitter. All five backends appear
below. The Rust/Wasm backend reuses Rust's algorithm; it is not a fifth
independent codec.

## Adapter encoding overhead

Ratios compare whole canonical encoding with ordinary encoding within the same
backend. Each result is the median of three run ratios, with seven trials per
phase per run. `2.0x` means twice the ordinary encoding wall time. The OCaml
column explicitly reports normalization microseconds: the audited repositories
have no standalone ordinary OCaml binary encoder/decoder for a ratio.

| Workload | Rust total | TypeScript total | OCaml normalize, µs | Native Motoko total | Rust/Wasm RTS total |
| --- | --- | --- | --- | --- | --- |
| small | 1.55x | 1.47x | 8.87 | 33.67x | 3.29x |
| records-128 | 2.40x | 1.49x | 113.15 | 13.35x | 1.85x |
| blob-64k | 1.17x | 1.43x | 34.64 | 31.27x | 3.74x |
| records-1024 | 2.62x | 1.65x | 1030.70 | 12.58x | 1.66x |

Rust totals are about 1.2–2.6 times ordinary encoding on these blocks;
TypeScript medians are about 1.4–1.7 times. Native Motoko totals are about
13–34 times, while the actual linked Rust/Wasm runtime path is about 1.7–3.7
times in this WASI harness. Absolute times across runtimes are not a language
ranking. TypeScript is noisy: the 64 KiB whole-adapter ratios were 0.66x, 1.88x,
and 1.43x across the three runs. That variation does not establish a speedup
over ordinary encoding; all per-trial ranges are retained.

## Encode and decode times

Microseconds per operation, median of three seven-trial run medians.
Normalization uses a preencoded message. Phases are timed separately, so
their medians need not sum exactly. `motoko-wasm` is the optimized Rust codec
linked into the actual Motoko memory64 runtime.

| Backend | Workload | Ordinary encode | Normalize | Whole adapter | Ordinary decode | Decode canonical |
| --- | --- | --- | --- | --- | --- | --- |
| rust | small | 5.28 | 2.75 | 8.14 | 7.81 | 7.39 |
| rust | records-128 | 21.79 | 29.42 | 51.87 | 113.84 | 112.38 |
| rust | blob-64k | 43.44 | 3.29 | 50.47 | 204.77 | 202.67 |
| rust | records-1024 | 136.30 | 221.55 | 357.91 | 801.14 | 799.87 |
| ts | small | 215.96 | 83.47 | 315.14 | 107.33 | 90.48 |
| ts | records-128 | 2086.63 | 994.26 | 3039.12 | 1215.49 | 1190.87 |
| ts | blob-64k | 445.63 | 168.76 | 659.24 | 1364.28 | 1174.61 |
| ts | records-1024 | 13114.83 | 7953.47 | 21802.15 | 9405.43 | 9133.75 |
| ocaml | small | — | 8.87 | — | — | — |
| ocaml | records-128 | — | 113.15 | — | — | — |
| ocaml | blob-64k | — | 34.64 | — | — | — |
| ocaml | records-1024 | — | 1030.70 | — | — | — |
| motoko | small | 5.36 | 178.12 | 180.22 | 9.26 | 9.15 |
| motoko | records-128 | 143.08 | 1767.09 | 1899.28 | 251.56 | 254.96 |
| motoko | blob-64k | 63.19 | 1917.12 | 1975.90 | 33.46 | 33.40 |
| motoko | records-1024 | 1091.15 | 12637.07 | 13687.57 | 1993.28 | 1983.00 |
| motoko-wasm | small | 5.35 | 12.54 | 17.36 | 9.31 | 9.14 |
| motoko-wasm | records-128 | 142.19 | 119.16 | 262.58 | 252.01 | 254.75 |
| motoko-wasm | blob-64k | 61.28 | 167.74 | 228.82 | 33.33 | 33.55 |
| motoko-wasm | records-1024 | 1094.85 | 743.43 | 1817.38 | 2002.47 | 1997.80 |

Rust and Motoko use their existing typed decoders for both encodings. The
ordinary SDK decoder works for these benchmark schemas. Its separate optional
compatibility adapter addresses the audited BOM/tuple/recursive-option cases:

| Workload | SDK decode canonical, µs | decodeCompatible, µs |
| --- | --- | --- |
| small | 90.48 | 108.49 |
| records-128 | 1190.87 | 1192.57 |
| blob-64k | 1174.61 | 1212.20 |
| records-1024 | 9133.75 | 8310.19 |

Motoko's OCaml compiler emits its existing encoder/decoder as Wasm and uses
helpers from its own Rust RTS; it does not use `dfinity/candid`'s Rust serializer.
Those generated ordinary codecs are the baseline for both Motoko paths.
OCaml's independent normalizer is measured without inventing a binary baseline.

## Actual local canister instruction measurements

The [heap driver](heap.mjs) executes real canister Wasm through PocketIC 10.0.0
on a local application subnet. It uses `performanceCounter(0)` around the
operation, with three fresh canisters per workload. These are IC instructions,
**not cycle charges**. RPC/installation/query checks and hashing are outside
the measured operation. The ratios compare the whole encode/normalize pipeline
with generated ordinary encoding in the same canister.

| Workload | Native normalize instructions | Native total / ordinary | RTS normalize instructions | RTS total / ordinary |
| --- | --- | --- | --- | --- |
| small | 1,121,559 | 30.28x | 64,432 | 2.67x |
| records-128 | 10,836,853 | 10.50x | 725,550 | 1.64x |
| blob-64k | 18,707,973 | 132.12x | 179,566 | 2.26x |
| records-1024 | 78,028,816 | 9.60x | 5,446,352 | 1.60x |
| nat64-4096 | 80,516 | 1.17x | 2,770,664 | 6.76x |
| text-64k | 7,382,735 | 56.65x | 243,989 | 2.84x |
| unicode-64k | 11,232,975 | 85.67x | 1,857,442 | 15.00x |

The block workloads use about 1.6–2.7 times ordinary instructions with the
Rust/Wasm runtime path. Native Motoko is materially more expensive for those
blocks. Its packed-vector/aligned-input fast path is particularly effective
for an already canonical `vec nat64`; the two paths have different allocation
strategies, so neither wins every workload. ASCII and multibyte text are shown
separately because validation costs differ.

## Allocation, occupied heap, and GC

The canister harness retains its source data and output, clears previous
results, forces four GC increments, measures one synchronous operation, then
forces four more increments. It records total allocation, operation-start/end
occupied heap, reclaimed bytes, GC instructions, and reserved linear memory.
It asserts no reclamation inside the single operation, so its occupied-heap
increase also measures that operation's temporary heap peak above its starting
occupancy.

The table subtracts the measured 72-byte statistics-record control allocation
from every single-operation allocation delta. Raw reports retain the
unsubtracted values. Before means the codecs immediately before the latest
Rust/native Motoko optimization, rather than the earliest naive implementation.

| Workload | Native before → final, bytes | RTS before → final, bytes |
| --- | --- | --- |
| small | 76,760 → 58,632 | 17,344 → 4,768 |
| records-128 | 776,984 → 368,248 | 264,784 → 44,992 |
| blob-64k | 1,173,728 → 640,704 | 338,816 → 134,816 |
| records-1024 | 5,611,056 → 2,505,728 | 2,024,040 → 314,048 |
| nat64-4096 | 3,821,392 → 7,184 | 559,736 → 197,904 |
| text-64k | 3,415,984 → 4,168 | 197,392 → 131,752 |
| unicode-64k | 3,415,984 → 4,168 | 197,392 → 131,752 |

For 1,024 transactions, native Motoko allocates 55% less and the linked RTS
allocates 84% less than those baselines. The retained canonical block is only
35,253 bytes; allocation totals include temporary objects and output, not just
the final bytes. Rust scratch uses GC-managed RTS allocations and drops its
Rust objects before publishing the output Blob.

**Post-GC occupied heap is not exact live-object memory.** Motoko's collector
leaves garbage in the current 64 MiB allocation partition and allocates
bookkeeping/mark data. Small workloads can show no heap reduction after forced
GC despite unreachable objects. Repeated normalization batches therefore
target 72 MiB allocation, capped at 20,000 calls and five billion estimated
instructions. Every batch that crossed a partition recorded reclamation, and
query checks redecoded retained output/ordinary bytes and compared them with
retained typed source values after collection. Low-allocation text batches
reached the instruction cap before crossing a partition; they are not claimed
as separate reclamation proofs.

The raw reports include absolute occupied heap before/end/after forced GC and
Wasm memory capacity. A fresh Motoko canister retained 128 MiB linear-memory
capacity in the single-operation captures, and stress commonly grew it to
192 MiB; these capacities include runtime reservations and are not codec live
heap. The harness does not report process RSS.

Native Rust uses a separate requested-allocation profiler:

| Workload | Requested bytes before → final | Peak live requested bytes before → final | Allocation requests before → final |
| --- | --- | --- | --- |
| small | 12,968 → 3,574 | 8,696 → 3,318 | 166 → 36 |
| records-128 | 207,036 → 34,264 | 78,208 → 34,008 | 2,118 → 201 |
| blob-64k | 270,235 → 68,337 | 203,481 → 68,081 | 118 → 32 |
| records-1024 | 1,601,212 → 245,251 | 571,076 → 244,995 | 15,819 → 1,396 |

The profiler counts allocator requests, including full realloc requests, and
tracks peak live requested bytes during normalization. It excludes existing
input/fixtures and allocator metadata; it does not measure RSS. Its source is
[rust/examples/profile.rs](../rust/examples/profile.rs). Rust normalization
medians fell about 3.6–5.1 times in the isolated before/after capture while
requested bytes fell 72–85%.

OCaml's `Gc.allocated_bytes` counters report 19,192 / 289,697 / 339,800 /
2,213,125 newly allocated bytes per normalization for small / records-128 /
blob-64k / records-1024. These are allocation totals, not live or peak heap.
V8 heap allocation is not separately quantified here.

The import-free memory32 module uses a resettable arena:

| Workload | Arena high-water including input, bytes | Retained linear memory, bytes |
| --- | --- | --- |
| small | 2,398 | 1,114,112 |
| records-128 | 26,096 | 1,114,112 |
| blob-64k | 132,833 | 1,245,184 |
| records-1024 | 175,671 | 1,245,184 |

Each fresh instance repeated its workload 100 times with identical scratch
high-water and memory capacity. The arena high-water includes copied input
and all temporary allocations until reset; capacity stays at the largest
workload and is not live heap. It uses no garbage collector. Both embeddings
also have a tested 256 MiB scratch limit.

## What changed and measured tradeoffs

Rust borrows scalar/text/binary spans, uses sorted record vectors and linear
owned joins, writes bounded metadata on the stack, interns borrowed shapes,
and skips per-element shape construction when the joined shape already covers
an element. Its standalone `no_std` core has no crate dependencies.

Native Motoko indexes the immutable input Blob directly, retains scalar/text
spans, packs fixed-width vectors, validates UTF-8 without copying slices, uses
compact record arrays, and sizes output before one indexed array allocation.
The current core growable vector is `List`; it avoids preallocating storage for
wide records whose optional fields disappear. Already canonical input returns
the original immutable Blob after full validation. That last change reduced
the measured Nat64-vector allocation by 97.6% and instructions by 99.1% versus
the preceding optimized version; 64 KiB text allocation fell 99.3%.

Private pure helpers are used where they fit. Extracting every stateful local
helper saved about 1.5 KiB per call but increased fuel 10–33% on the block
workloads, so that broad extraction was rejected. Record/Blob identity checks
add 72 bytes and less than 0.7% IC instructions compared with the preceding
version.

Packed floats still need exact NaN checks. The canonical 4,096-element float64
case fell from 304,336 to 6,640 allocated bytes and from 9,022,551 to 1,210,542
fuel. A deliberately noncanonical NaN only at the last element requires a late
comparison failure followed by output emission: measured fuel rose 12.88%
versus the preceding implementation, with 72 additional allocated bytes.
A noncanonical NaN at the first element added 0.035% fuel. This is a measured
adversarial case, not a universal worst-case bound.

The earliest implementation created per-byte value objects and repeatedly
converted arbitrary-precision integers. Its initial 64 KiB bottleneck was
removed without changing the frozen wire expectations:

| Language | Initial 64 KiB normalization, µs | Final normalization, µs | Descriptive speedup |
| --- | --- | --- | --- |
| rust | 7231.46 | 3.29 | 2196.7x |
| ts | 28838.74 | 168.76 | 170.9x |
| ocaml | 7465.00 | 34.64 | 215.5x |
| motoko | 113655.55 | 1917.12 | 59.3x |

These initial figures are one seven-trial run and the initial codecs are
historical benchmark baselines, not conformance implementations. They show
that per-byte intermediary allocation was avoidable; they do not establish an
intrinsic cost for canonical Candid. A future direct typed emitter could avoid
parsing the intermediate ordinary message, but needs its own measurements.

## Workloads and limits

All four block workloads encode a `nat64` timestamp, optional 32-byte parent
hash, transactions containing a public principal, `nat` amount `2^200 + index`,
optional byte memo, and an absent optional text note. Small has four transactions
with 32-byte memos; records-128 has 128 with 32-byte memos; blob-64k has one
with 65,536 memo bytes; records-1024 has 1,024 with empty memos. Every third
transaction has a present memo; the 64 KiB case always does.

| Workload | Ordinary bytes | Canonical bytes |
| --- | --- | --- |
| small | 305 | 296 |
| records-128 | 5875 | 5866 |
| blob-64k | 65676 | 65667 |
| records-1024 | 35262 | 35253 |

Canonical messages are asserted byte-identical and typed reconstruction passes
outside timed loops. Ordinary headers can differ across languages. This is not
an ICRC-3 `Value` size comparison and establishes no universal compression
claim. Extra canister probes use 4,096 Nat64 elements, 64 KiB ASCII text, and
64 KiB UTF-8 comprising 16,384 globe characters.

Rust uses an optimized release build and `black_box`. TypeScript uses warmed
V8, observable checksums, and requested GC between trials. OCaml uses its native
build. WASI timing uses Wasmtime 49.0.0 with fuel enabled, excludes
instantiation/JIT, and includes small loop-marker instrumentation overhead.
**Wasm fuel is not IC instructions or cycles.** This shared Linux x86_64
workspace has no exclusive-host or CPU-frequency guarantee; benchmark an
intended deployment before making capacity claims.

## Reproduce and captured data

Build [wasm/](../wasm/README.md) for the optional compiler/runtime path.

```shell
.venv/bin/python benchmarks/run.py --label final-1 --moc /path/to/moc --core /path/to/core/src --wasm-moc wasm/moc
.venv/bin/python benchmarks/run.py --label final-2 --moc /path/to/moc --core /path/to/core/src --wasm-moc wasm/moc
.venv/bin/python benchmarks/run.py --label final-3 --moc /path/to/moc --core /path/to/core/src --wasm-moc wasm/moc
python3 benchmarks/summarize.py _out/benchmarks-final-1.json _out/benchmarks-final-2.json _out/benchmarks-final-3.json
moc --package core /path/to/core/src motoko/test/heap.mo -o _out/heap.wasm
node benchmarks/heap.mjs --wasm _out/heap.wasm --server /path/to/pocket-ic --label local --trials 3
.venv/bin/python benchmarks/wasm_memory.py _out/bench-rust-final-1.json
.venv/bin/python benchmarks/identity_floats.py --label local
cargo run --release --locked --manifest-path rust/Cargo.toml --example profile -- _out/bench-rust-final-1.json
```

To measure the runtime canister path, replace the native library adapter with
`Prim.canonicalizeCandid(raw)` and compile with `wasm/moc`; the shared heap
driver needs no change. `--languages` can select `rust ts ocaml motoko
motoko-wasm` in the timing runner. Package examples run independently.

Captured timing: [initial](results-before.json), final
[runs 1](results-final-1.json), [2](results-final-2.json), [3](results-final-3.json),
and [aggregate](results-summary.json).
Canister heap/GC: native [before](results-heap-native-before.json) /
[final](results-heap-native-final.json), runtime [before](results-heap-rts-before.json) /
[final](results-heap-rts-final.json).
Native Rust allocation: [before](results-rust-before-allocations.json) /
[after](results-rust-after-allocations.json).
Standalone [arena report](results-wasm-memory.json);
focused float [before](results-motoko-identity-frozen.json) /
[after](results-motoko-identity-candidate.json).

[baseline.patch](baseline.patch) restores the four earliest codecs and their
required Rust dependencies. [rust-pre-optimization.patch](rust-pre-optimization.patch),
[motoko-pre-optimization.patch](motoko-pre-optimization.patch), and
[motoko-pre-identity.patch](motoko-pre-identity.patch) restore the focused
comparison baselines. Apply only in disposable copies and run the same harness;
these are local research restoration patches, not upstream changes.
