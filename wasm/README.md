# CandidX Wasm and Motoko compiler integration

Version 1.0.0, profile `cx`. The standalone `cx.wasm` artifact embeds the Rust
typed encoder and incoming-message normalizer. The Motoko compiler patch adds
the direct `to_candid_canonical` operator, using generated Wasm and a small RTS
shape helper. Both produce canonical ordinary Candid bytes, with no hashing API
or application schema identifier. CandidX source is MIT; [third-party
notices](THIRD_PARTY_NOTICES.md) cover linked upstream components.

## Direct Motoko compiler encoding

The supplied compiler extension adds a variadic typed operator:

```motoko
let canonical : Blob = to_candid_canonical(block);
let recovered : ?Block = from_candid(canonical);
let arguments : Blob = to_candid_canonical(block, "memo");
```

The AST and IR retain each argument's source type. The compiler generates Wasm
specialized to those types. It reads the original values to derive their used
shapes, measures the exact header and payload size, and allocates one final
Motoko Blob. It then writes the type header and payload directly into that
Blob, including the joined record fields and remapped variant indices required
by arrays.

Rust RTS code only interns shapes, computes joins, and writes the type header.
The generated Wasm handles payload traversal, sizing, and emission. This path
uses no full CandidX Rust crate, payload tree, `CXTY` heap descriptor, binary
parser, or complete Rust output byte vector. It performs no ordinary
serialization or normalization before writing the result.

The operator accepts the same shared source types as `to_candid`. Actual
function and service values, Candid field ID collisions, and exceeded limits
trap with `CandidX error:`. Valid unused optional fields and unselected variant
cases do not affect the canonical result. The ordinary `from_candid` decoder
recovers supported values under their original schema.

`to_candid_canonical` is the integration's only public addition. It requires no
new compiler flags and preserves ordinary `to_candid` and `from_candid`
behavior. The encoding runs synchronously; the RTS shape context holds only
temporary shape and header state until the operation completes.

Build from the CandidX root using the Motoko Nix environment and Rust toolchain.
Pass the path to a local Motoko source checkout; the build creates its isolated
patched checkout inside this repository. Enter `nix develop` from the Motoko
checkout, then change to the CandidX root for these commands:

```shell
python3 wasm/build.py --motoko-repo /path/to/motoko
mkdir -p _out
wasm/moc -wasi-system-api wasm/smoke.mo -o _out/cx-smoke.wasm
wasmtime -W memory64 _out/cx-smoke.wasm
```

The build uses an isolated checkout at the pinned compiler base. Exact source,
patch, compiler-base, and artifact fingerprints are recorded in
[artifacts/manifest.json](artifacts/manifest.json). `--prepared` rebuilds an
already patched checkout without Git operations. `wasm/moc` selects the
patched compiler and RTS; `CX_MOTOKO_DIR` selects another isolated checkout.
Normal compiler flags and package mappings remain available. This operator is
provided by the integration patch, not by the pinned upstream compiler base.
The patch's changelog uses `(#NNNN)` as a placeholder for an eventual upstream
PR number. No PR has been created; the `cx` wire rules and package version are
fixed.
The JS build embeds both RTS variants using the upstream build generator,
then restores the compiler's development loader source.

Native source, IR, lowered IR, compiled Wasm, and the JS interpreter have direct
fixtures. The native Motoko library remains a portable fallback using ordinary
encoding followed by normalization.

To select another prepared compiler directory, set the wrapper's override:

```shell
CX_MOTOKO_DIR=/path/to/patched-checkout wasm/moc -wasi-system-api wasm/smoke.mo -o _out/cx-smoke.wasm
```

## Standalone typed memory32 ABI

[artifacts/cx.wasm](artifacts/cx.wasm) requires no host imports. Its exported
linear memory and functions work with ordinary Wasm hosts. `wasm/runtime.py`
provides two host interfaces: `TypedEncoder.encode_args(trees)` for direct
values and `Canonicalizer.canonicalize(raw)` for inbound bytes.

```python
# From the CandidX root:
from wasm.runtime import TypedEncoder
canonical = TypedEncoder().encode_args([
    {"kind": "record", "fields": [
        {"id": 7, "optional": False, "value": {"kind": "nat", "value": 255}},
        {"id": 8, "optional": True, "value": {"kind": "opt", "value": None}},
    ]}
])
```

| Export | Contract |
| --- | --- |
| `cx_reset()` | Start an operation; invalidate earlier spans. |
| `cx_alloc(length: u32) -> u32` | Allocate a span; zero rejects an excessive allocation length or missing reset. |
| `cx_encode_typed(nodes_ptr, node_count, roots_ptr, root_count) -> u32` | Encode typed memory nodes directly; zero succeeds, one returns error text. |
| `cx_canonicalize(pointer, length) -> u32` | Normalize a complete ordinary message; same status convention. |
| `cx_result_ptr()`, `cx_result_len()` | Canonical bytes or UTF-8 error text, valid until reset. |
| `cx_scratch_used()` | Arena bytes allocated since reset, including host input. |
| `cx_memory_pages()` | Current 64 KiB memory pages. |

A typed node is a 24-byte little-endian record
`{kind: i32, flags: u32, a: u32, b: u32, reserved0: u32, reserved1: u32}`.
Both reserved words must be zero. Roots are an allocated array of u32 node
indices. Child references are node indices, not pointers.

| Kind | Node contents |
| --- | --- |
| Null `-1`, Reserved `-16` | `a = b = flags = 0`. |
| Bool `-2` | `a` is 0 or 1; `b = flags = 0`. |
| Nat `-3`, Int `-4` | `a/b` are pointer/length of unsigned little-endian magnitude bytes; Int flag bit 0 means negative. |
| Nat8…Nat64 `-5`…`-8`, Int8…Int64 `-9`…`-12` | Low/high scalar bits in `a/b`; signed values use their declared-width two's complement. |
| Float32 `-13`, Float64 `-14` | Exact IEEE bits in `a/b`, including NaN payloads. |
| Text `-15`, Principal `-24`, Blob `-26` | `a/b` are pointer/byte length; Text must be valid UTF-8. |
| Option `-18` | `a` is child index or `0xffffffff` for None; `b = flags = 0`. |
| Vector `-19` | `a/b` are pointer/count of u32 child indices; flag bit 0 marks source Nat8 elements, including an empty binary vector. |
| Record `-20` | `a/b` are pointer/count of 12-byte edges `{field_id, child_index, optional}`; IDs strictly increase and optional is 0 or 1. |
| Variant `-21` | `flags` is selected u32 Candid case ID; `a` is child index and `b = 0`. |

The Python wrapper sorts record field metadata by numeric ID without removing
fields. It supports `float32bits` and `float64bits` tree kinds for exact bits.
Input trees represent typed Candid values: elements of each vector share one
source schema. Record field presence may differ through absent optional
fields; selected variant cases and option contents may also differ.
The low-level ABI validates pointers, indices, flags, record ordering, UTF-8,
source optionality, numeric ranges, and reachable-value limits. Cyclic child
references fail the depth bound. No ordinary Candid input is required.

An instance serves one operation at a time. Read/copy the result before the
next reset. Equal workloads reuse the arena; memory capacity retains the
largest workload. Hosts may impose additional instruction/fuel limits.

## Bounds and verification

Canonical output is limited to 32 MiB, collections/traversal to 2,000,000,
derived type tables to 1,024, value depth to 256, and scalar LEB output to 4,096
bytes. Text and binary lengths are limited to 2,000,000 bytes. The inbound
normalizer also limits messages to 32 MiB and declared type tables to 1,024.
Each standalone host allocation is at most 32 MiB; total host input and scratch
is at most 256 MiB, with linear memory capped at 512 MiB. The compiler RTS shape
context accounts for temporary allocations and caps them at 256 MiB per call.
Scratch exhaustion or host memory/fuel exhaustion traps and produces no
partial canonical value. IC trapping rolls back the message.

Run the compiler architecture, JS compiler and bridge checks in that same Nix
environment; the JS-built Wasm fixture also requires its Wasmtime executable.

```shell
python3 -m venv _build/wasm-venv
_build/wasm-venv/bin/pip install wasmtime==49.0.0
_build/wasm-venv/bin/python wasm/test_typed.py
_build/wasm-venv/bin/python wasm/test.py --resource-stress
python3 wasm/test_architecture.py --report _out/wasm-architecture.json
node wasm/test_compiler_js.cjs
python3 wasm/test_bridge.py --resource-stress
```

The shared direct conformance/fuzz tests compare typed values across ordinary
SDK encoders, direct CandidX encoders, decoders, all normalizers, and external
application-side SHA-256. Compiler fixtures also check literal golden bytes
before ordinary encoding or printing, covering cold literal big integers,
recursive declarations, optional fields, variants, arrays, binary values,
NaNs, signed zero, Unicode, and u32 field IDs. Source guards check that the
ordinary serializer routines remain identical to the pinned compiler base,
that generated Wasm sizes and writes the final Blob directly, and that the RTS
contains only shape and type-header support.

See the recorded configurations in [performance
measurements](../benchmarks/performance.md) and [the IC heap
probe](../benchmarks/heap.mjs) for occupied heap, allocation, retained values,
and GC measurements. Standalone incoming-message normalization and direct
compiler encoding are separate workloads.

The [minimal integration report](../research/minimal_integration.md) records
the final patch review, conformance runs, memory savings and remaining costs.
