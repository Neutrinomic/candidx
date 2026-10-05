# Canonical transaction bytes in a TypeScript client

The server can keep returning `transactions : vec Transaction`. The client
uses `transactions: IDL.Vec(CanonicalBytes)` in its return decoder and gets
`Uint8Array[]`. Each element is a complete, independently decodable canonical
Candid message for that single transaction. It matches the producer's
`to_candid_canonical(transaction)`, Rust `encode_one(&transaction)`, and
TypeScript `encodeCanonical([Transaction], [transaction])`.

This preserves newly present fields before an older schema projects them away.
Hash each element's entire bytes, then decode its known fields. For a previous
hash chain, compare the hash of transaction `i` with transaction `i + 1`'s
`phash`. The transaction's own `phash` contributes to its hash; surrounding
reply metadata and other transactions do not. If an old transaction decoder
rejects a new variant case, a decoder for common record fields such as `phash`
can still read the link while retaining the complete canonical bytes.

See the [client usage examples](../ts/README.md#capture-canonical-transactions-before-projection),
[implementation](../ts/src/canonical-bytes.ts), and
[chain regression](../ts/test/canonical-bytes.test.mjs).

## Integration

`CanonicalBytes` is a decode-only SDK type, not a `.did` type or a blob sent by
the server. Use it inside `decodeCompatible`, or wrap each outer actor return
type with `decoderType`. Normal SDK actor calls then return these byte arrays
without changing the arguments or transport. Both helpers are exported from
`candidx/candid`; the root package remains SDK-free.

The SDK supplies the actual incoming wire type. The marker interns its type
graph once per reply, reads the selected payload without SDK JavaScript value
decoding, and uses the shared CandidX normalizer to derive its standalone header
and payload. This rebuild is necessary: a payload slice plus a prefix would
retain type references and variant indices belonging to the enclosing reply.
The marker uses the SDK's zero-copy stream view instead of copying the remaining
reply for every transaction. Typed producer encoding remains direct and has
not acquired a serialize/decode/normalize path.

The root wrapper propagates selected-value failures even when the SDK's
optional coercion catches an inner decoder error. Limits apply to each selected
value; the functional decoder also checks the complete input byte limit.
Parsing the enclosing header and ordinary surrounding fields remains the
SDK's responsibility. Its header parser can itself copy the input during
signed LEB decoding; the capture guard only claims that the marker adds no
such copies. Actual selected function/service references remain unsupported.

## Local evidence

The current code passed:

| Check | Result |
| --- | --- |
| TypeScript suite | 49 tests, including a normal actor call and `phash` chain tampering |
| Three seeds, 500 generated records per seed | 3,390 typed cases, including unused schema extension pairs |
| Root/envelope captures in seeded cases | 67,800 checks |
| Captures of all backends' canonical outputs | 40,680 checks |
| Directed scalar/recursive/zero- and multiple-argument cases | 11,250 additional capture checks |
| Actual ordinary Rust/TS/Motoko replies and directed cases | 29,370 capture checks |
| Permuted/duplicated/unused wire type declarations | 183 messages, 522 capture checks |
| Local extracted package | SDK-free root import, public adapter, and TypeScript declarations pass |

That is 149,622 fuzz/replay capture checks, in addition to the unit tests.
Each capture must match standalone direct producer bytes and an external
SHA-256, and decode to independently constructed original typed values. Saved
ordinary messages are also checked against the independent source-value Python
oracle before client replay. OCaml and standalone Wasm provide canonical
encoder output; they do not supply an ordinary value decoder. The Wasm codec
embeds Rust and is an integration path, not an independent language codec.

The chain regression includes a newer present field unknown to the old client.
Hashing the captured bytes validates the links; hashing the old projected
object fails. Tampering only with that unknown field leaves the old decoded
object unchanged but fails the next `phash` comparison.

Guards disable SDK value decoders during capture, and forbid the copying
`Pipe.buffer` accessor while the marker runs. Other cases cover unknown present
optional fields, unused declarations, variant-index remapping, recursive values,
array joins, BOMs and exact UTF-8, integer widths and boundaries, NaN bit
patterns, signed zero, principals, malformed payloads, resource limits, failed
optional decodes, input immutability, and sliced/pooled input buffers.

The saved [summary](results/client-summary.json), three seeded reports,
[ordinary-input replay](results/client-inbound.json), and
[package check](results/client-packaging.json) identify the tested sources.
Codec/artifact fingerprints agree across the runs. The explicit chain
regression and replay harness were added after the seeded runs; their later
snapshots retain that distinction. These checks do not establish a formal
correctness proof or supply new performance measurements. The SDK's malformed
ordinary output for one recursive alias case is rejected, rather than accepted
to satisfy a round trip.

## Reproduction

With dependencies installed as described in [tests/README.md](../tests/README.md):

```shell
npm test --prefix ts
.venv/bin/python tests/fuzz.py --seed 0xcad1d --cases 500 --runtime-moc wasm/moc --out _out/client-fuzz-cad1d-500
.venv/bin/python tests/fuzz.py --seed 0x135288c --cases 500 --runtime-moc wasm/moc --out _out/client-fuzz-135288c-500
.venv/bin/python tests/fuzz.py --seed 0x13579bdf --cases 500 --runtime-moc wasm/moc --out _out/client-fuzz-13579bdf-500
.venv/bin/python tests/client_replay.py _out/client-fuzz-cad1d-500 _out/client-fuzz-135288c-500 _out/client-fuzz-13579bdf-500 --out _out/client-inbound
.venv/bin/python tests/packaging.py --out _out/client-packaging --report _out/client-packaging/report.json
```

The replay tool verifies that the saved reports passed with stable sources and
that codec/artifact sources still match. It verifies saved bytes against the
original typed witnesses before invoking the current client decoder. All
commands are local; no package, forum post, or issue is published.
