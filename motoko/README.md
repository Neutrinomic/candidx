# CandidX in native Motoko

A temporary standalone library implemented entirely in Motoko. It needs
`motoko-core` and uses compiler primitives for byte/array conversion, with no
Rust/Wasm foreign codec and no SHA-256 inside the library.

```motoko
import CandidX "../candidx/motoko/src/lib";

type Block = { a : Text; b : ?Text };
let block : Block = { a = "abc"; b = null };
let encoded = CandidX.canonicalize(to_candid(block));
switch encoded {
  case (#ok bytes) {
    // Store bytes or hash them using the application's hashing library.
    let decoded : ?Block = from_candid(bytes);
    assert decoded == ?block;
  };
  case (#err message) { /* explicit unsupported/malformed/resource error */ };
};
```

The API is `canonicalize : Blob -> { #ok : Blob; #err : Text }`, with a public
`profile : Text`. Use an ordinary typed record and the existing `to_candid`.
A library cannot reflect an arbitrary Motoko record's static type to implement
a generic `to_candid_canonical` operator; that remains a proposed compiler
builtin. There is no hashing primitive proposal in this package.

For a local package import, point the `candidx` package to this directory's
`src` and provide `core`, for example:

```shell
moc --package candidx /path/to/candidx/motoko/src --package core /path/to/motoko-core/src app.mo
```

Then import `CandidX "mo:candidx"`. `mops.toml` supplies metadata for future/local
Mops packaging; it does not imply publication. The library was checked with
the source compiler 1.16.1 and installed 0.16.3. It requires a compiler/core
combination supporting the imported core APIs; minimum metadata is 0.16.1.

Normalizing byte vectors uses input spans, avoiding per-byte value objects and
temporary blob copies. Packed fixed-width vectors also retain input spans;
text is validated directly, and a sizing pass allocates one indexed output
array. The current core growable vector is `List`; it is used where values can
be omitted, while small records use compact arrays. Pure helpers are private
module functions. Extracting all captured parsing/writing helpers reduced
some allocation but increased instruction cost in measured workloads, so the
hot stateful helpers remain local.

After full validation, already canonical input may be returned as the same
immutable Blob. This avoids the output array/copy for aligned text and packed
scalar payloads. Noncanonical inputs still emit new bytes; a measured float
array with a noncanonical NaN only at its end pays an extra comparison pass.
The performance report includes that case.

This library uses a runtime parse/derive/emit adapter, considerably slower
than compiler-generated ordinary Candid. See
[performance.md](../benchmarks/performance.md) before choosing it for large
block workloads. The separate [Wasm backend](../wasm/README.md) includes a
supplied optional compiler patch that adds `to_candid_canonical`. The patched
compiler traverses original typed values and directly emits canonical bytes
using Rust inside the actual Motoko runtime. Its performance is measured
separately from this native Motoko library.

Verify original canonical bytes before `from_candid` into a smaller schema.
The `cx` rules and conformance vectors define a fixed encoding contract.
`profile` is metadata and is not included in encoded bytes.
