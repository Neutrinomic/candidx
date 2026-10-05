# CandidX for OCaml

Encode native OCaml values directly with typed witnesses. Record fields use
getters and variant cases use selectors, so applications retain their own
record and sum types. The encoder derives the used finite shapes in one pass
and emits ordinary canonical DIDL in another; it does not first serialize or
parse an ordinary message, and it builds no generic Candid value tree.

```ocaml
type block = { a : string; b : string option }

let block = Candidx.record [
  Candidx.field "a" Candidx.text (fun r -> r.a);
  Candidx.field "b" (Candidx.option Candidx.text) (fun r -> r.b);
]

let bytes = match Candidx.encode block { a = "abc"; b = None } with
| Ok canonical_bytes -> canonical_bytes
| Error message -> invalid_arg message
```

This has the same bytes as a record that declares just `a`. `None` optional
fields disappear, while `Some None` keeps its outer wrapper. Array and list
elements share their joined value-derived wire type, including mixed optional
fields and selected variant cases. `array nat8`, `list nat8`, and `blob` retain
their binary kind even when empty. Other empty vectors use `vec empty`.

`encode_args [arg witness value; ...]` handles heterogeneous argument lists.
`recursive` ties recursive witnesses for finite values. `contramap` adapts a
native representation, for example `contramap Z.of_int nat` for a nonnegative
OCaml `int` field. Getters, selectors, and conversions must be pure, and callers
must keep data unchanged during encoding. A used shape that changes between
the two passes is rejected. No additional pass checks scalar equality.

`nat`, `int`, and `nat64` use Zarith `Z.t`. `nat8`, `nat16`, `int8`, and `int16`
use OCaml `int`; `nat32` uses `int64`; signed 32/64-bit kinds use `int32`/`int64`.
Values outside the Candid range are rejected. `float32` rounds an OCaml float
to binary32, while `float32_bits` and `float64_bits` accept raw native bit
patterns. Every NaN is canonicalized; all other bits are preserved. Text must
be exact valid UTF-8. Public principal values are raw `bytes`, of length at
most 29.

Named fields and cases use Candid's numeric label hash. `field_num`/`case_num`
take explicit `int64` IDs for tuples and protocols with assigned IDs. Schema
construction rejects out-of-range IDs and duplicate/colliding labels, including
named/numeric collisions. Source label text and unused declarations do not
enter the output; numeric Candid IDs remain encoded.

The separate `canonicalize : bytes -> (bytes, string) result` utility accepts a
complete inbound ordinary Candid message and normalizes every actual wire
field before receiver projection. There is no hashing API or compiler
dependency. Applications hash the resulting bytes using their chosen digest.

Requires OCaml 4.14+, Dune 3.10+, and Zarith 1.12+. The tested 64-bit host
represents the full Candid `u32` field-ID range. From this directory:

```shell
dune build
dune runtest
dune build -p candidx
```

Link with `(libraries candidx)` in a Dune application. The included
`candidx.opam` supports installing this directory as a local package; no package
registry publication is assumed. Library builds treat warnings as errors.

`direct_probe.exe <corpus.json>` runs the shared typed schema/value corpus;
`direct_tests.exe` exercises native records, variants, recursive values, limits,
and scalar bits. `direct_bench.exe` measures direct encoding of four native
block workloads with allocation and GC statistics. Witness and input setup
occurs outside its timed loops. The audited Motoko compiler's OCaml Candid
frontend has no separate ordinary binary data encoder and decoder, so there is
no OCaml ordinary-codec benchmark baseline. `probe.exe` and `bench.exe` remain
line-oriented inbound normalization tooling. The Motoko compiler and runtime
timings are reported separately.

The `cx` rules and conformance vectors define a fixed encoding contract.
The profile name is not included in encoded bytes.
