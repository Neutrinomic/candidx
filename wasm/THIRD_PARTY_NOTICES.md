# Third-party components

CandidX source, the standalone ABI, and the CandidX additions in the compiler
patch are licensed under [MIT](LICENSE). Linked upstream runtime binaries and
upstream context in the patch retain their own licenses. The artifacts are
not represented as containing exclusively MIT-licensed code.

* Motoko compiler/RTS base:
  `9384e1076bf71c1c44b78627b9191011d8543deb`, Apache License 2.0. The unchanged
  upstream license is included as [Motoko-Apache-2.0.txt](licenses/Motoko-Apache-2.0.txt).
  This applies to `mo-rts*.wasm` and upstream compiler/RTS patch context.
* Rust `core`, `alloc`, and compiler support: retained contributor copyrights,
  principally MIT or Apache 2.0, with component exceptions described in the
  toolchain's [standard-library notices](licenses/Rust-standard-library.html).
  The [MIT](licenses/Rust-MIT.txt), [Apache 2.0](licenses/Rust-Apache-2.0.txt),
  and [LLVM exception](licenses/Rust-LLVM-exception.txt) texts are included.
  The notice inventory covers the toolchain; it does not assert every listed
  component is linked into these no_std artifacts.
* `libm` 0.2.16 in the Motoko RTS: MIT with retained component notices in
  [libm-LICENSE.txt](licenses/libm-LICENSE.txt).
* LibTomMath v1.2.0 in the Motoko RTS: public-domain dedication/Unlicense,
  included as [LibTomMath-Unlicense.txt](licenses/LibTomMath-Unlicense.txt).
* Historical local pre-optimization RTS artifacts additionally contain `num-bigint`,
  `num-integer`, and `num-traits`, available under MIT or Apache 2.0. The final
  CandidX no_std core has no external crate dependencies. Those frozen files
  are ignored benchmark build products, not part of the final integration. Their
  retained MIT notices are included for
  [num-bigint](licenses/num-bigint-0.4.8-MIT.txt),
  [num-integer](licenses/num-integer-0.1.47-MIT.txt), and
  [num-traits](licenses/num-traits-0.2.19-MIT.txt).

The compiler's native OCaml executable also uses the dependencies selected by
the upstream Motoko Nix environment, with their own licensing. The embedded
reference codec uses the already-present Num library; no new numeric runtime
is added to the compiler distribution. Native compiler binaries remain local
build products under `_build`; this directory supplies the Wasm artifacts and
source integration patch.
