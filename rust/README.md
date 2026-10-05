# CandidX for Rust

Local package; publishing is disabled. Add a path dependency:

```toml
[dependencies]
candidx = { path = "../candidx/rust" }
candid = "0.10.37"
```

Use existing `CandidType` records:

```rust
let bytes = candidx::encode_one(&block)?;
let block: Block = candid::decode_one(&bytes)?;
```

`encode_one` visits the existing `CandidType` serializer callbacks twice. The
first visit derives only the value-used canonical shapes; the second writes
the canonical header and typed payload directly. It does not build an
ordinary Candid message, an intermediate payload tree, or parse wire bytes.
`encode_args((first, second))` directly encodes an ordered argument sequence.
It supports standard tuples of up to 16 members and the empty tuple.

`encode_args` uses `candidx::ArgumentEncoder`. Standard tuple calls remain the
same. A custom upstream `candid::utils::ArgumentEncoder` implementation is tied
to `IDLBuilder` and must implement this direct trait instead.

Custom `CandidType` serializers must report an accurate source type and remain
stable between visits. The emitted shapes are checked against the first
visit; changing shapes, malformed callbacks, and swallowed callback errors
are rejected. Changes to scalar values of the same kind are outside that
check, so purity remains part of the custom serializer contract.

For already typed dynamic values, `encode_one_with_type` and
`encode_args_with_types` accept explicit `TypeEnv`/`Type` metadata. Annotate
dynamic `IDLValue`s at their source types first so record callback order and
source variant indices are correct; annotation does not serialize bytes.

`candidx::canonicalize(&received_bytes)` is the separate inbound-message
normalizer. Output is `Vec<u8>`; unsupported, malformed, or over-limit input
returns `candidx::Error`.

For standalone normalization without linking the upstream Candid crate:

```toml
candidx = { path = "../candidx/rust", default-features = false }
```

The standalone core supports `no_std` with `alloc` and has no crate
dependencies. Its direct runtime API `encode_direct_args(&[DirectValue])`
accepts typed values, source optional-field flags, and the binary-vector kind.
Integer inputs are unsigned little-endian magnitudes plus an Int sign;
scalar LEB encoding and NaN normalization happen directly in the writer.
It constructs no intermediate ordinary message. The inbound normalizer
borrows payload spans and parses bounded received metadata separately.
Neither API implements hashing.

`encode_source_args` visits original native values through the no_std
`DirectSource` trait, retaining only shape metadata between its two visits.
This is the compiler/RTS integration interface; scalar payload conversion
happens during emission. Source visitors must remain stable between visits.
Both low-level direct APIs require each vector to originate from one uniform
source element schema. Retained record-field presence may differ only through
absent optional fields; selected variant cases may differ. They do not define
encoding for arbitrary heterogeneous record schemas.
Application hashing covers the entire canonical message before any smaller
receiver schema projection. A `Vec` is ordered; sort map entries explicitly if
your block protocol intends map order to be irrelevant.

```shell
cargo test --locked
cargo test --locked --no-default-features
cargo clippy --locked --all-targets --features fuzz -- -D warnings
cargo run --release --locked --example bench
```

The benchmark reports direct typed encoding, the explicit ordinary-encode
pipeline, inbound normalization, and ordinary decoding separately. To measure
requested allocations with the same fixtures, save its JSON output to
`bench.json`, then run `cargo run --release --locked --example profile -- bench.json`.
Fixture construction and schema-cache warmup happen outside allocation
counters; each measured encoder's own schema allocations remain included.

The fuzz example requires `--features fuzz` and is test tooling only. This
workspace tested the direct callbacks with Candid 0.10.37. The `cx` rules and
conformance vectors define a fixed encoding contract. The profile name is not
included in encoded bytes.
