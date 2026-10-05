# CandidX specification

Start with [canonical values and encoding](CanonicalValues.md). The
[Candid appendix](CandidAppendix.md) restates the profile in the notation and
style of `dfinity/candid/spec/Candid.md`, including an application-side hashing
recipe. Neither document is an adopted Candid or ICRC standard.

The profile is `cx`. Its value identity rules and frozen conformance vectors
define a fixed encoding contract. An incompatible change requires a different
profile; applications must not reinterpret historical `cx` blocks under new
rules. The profile name is metadata and is never serialized into the message.

There is no schema identifier in the bytes and no hashing API. The byte
normalizers do not require a hash function. Direct typed encoders reuse existing
SDK type descriptions while producing canonical bytes from the original values.
Output is an ordinary `DIDL` message whose wire type
is derived from the data. The type table is still necessary to decode Candid.
