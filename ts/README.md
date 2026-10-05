# CandidX for TypeScript / JavaScript

Standalone ESM normalizer with a separate optional `@icp-sdk/core/candid`
adapter. Build locally with `npm ci && npm run build`. The package is private;
no published package or install-from-registry command is assumed.

```ts
import { canonicalize } from 'candidx';
const canonicalBytes = canonicalize(ordinaryCandidBytes);
```

The root export has no SDK dependency. Input accepts `Uint8Array` or
`ArrayBuffer`; output is a new `Uint8Array`. Malformed, unsupported, or
over-limit inputs throw `Error`. The normalizer traverses the full wire data
before receiver projection.

For existing typed schemas:

```ts
import { IDL } from '@icp-sdk/core/candid';
import { encodeCanonical, decodeCompatible } from 'candidx/candid';

const Block = IDL.Record({ a: IDL.Text, b: IDL.Opt(IDL.Text) });
const bytes = encodeCanonical([Block], [{ a: 'abc', b: [] }]);
// Hash / verify bytes in the application before projecting.
const [block] = decodeCompatible([Block], bytes);
```

`encodeCanonical` directly visits the SDK's current types and values. A first
visit derives and joins the used wire shapes; a second writes the canonical
header and payload to one growable buffer. It creates no ordinary intermediate
message and parses no serialized values. The public SDK `Type` visitor supplies
field names, tuple components, option/vector children, and recursive types;
the adapter does not call the SDK's serialization methods. Decoder schemas are
ordinary SDK schemas, not generic block values.

Both encoders share SDK-free shape interning, joins, header ordering, and a
writer. The typed path applies the same value/collection, nesting, scalar-size,
and 32 MiB output limits; the 1,024-entry type limit applies to its reachable
value-derived output table, since there is no input type table. Byte vectors
retain bulk copying while charging each byte against the value traversal limit.
Typed-array vector inputs must be genuine integer views of the declared
width. Ordinary JavaScript arrays are also accepted, including arrays of floats.
Signed and unsigned views of that width are accepted when every element
fits the declared Candid kind; values are never reinterpreted to repair an
invalid range. `Buffer` and `Uint8ClampedArray` are accepted byte sources.
Float views, `DataView`, and spoofed vector metadata are rejected. Principal
byte lengths and vector spans use native storage metadata, so overridden
properties cannot hide bytes or add zero padding.
Text values must contain Unicode scalar values. Unpaired UTF-16 surrogates in
JavaScript strings are rejected instead of being replaced during UTF-8 encoding;
valid surrogate pairs and literal replacement characters keep their bytes.
`IDL.Float32` accepts JavaScript numbers converted to IEEE float32, matching
the SDK binding. Its round-trip value is `Math.fround(input)`; float64 retains
the original non-NaN bits, including signed zero. NaNs use the specified
canonical pattern for their width.

`decodeCompatible` is an optional **projecting** decoder for SDK 6.1.0. It clones
expected schemas privately and fixes the tested leading text BOM, omitted
optional tuple component, and omitted recursive optional field cases without
mutating caller schemas or SDK globals. It also reads ordinary Candid. The
encoder output itself is valid ordinary Candid; these workarounds address
existing SDK decoder behavior. See [audit_spec.md](../research/audit_spec.md).
The adapter supports original encodable schemas; decoding-only `IDL.Unknown`
is rejected explicitly, except for the CandidX markers described below.

## Capture canonical transactions before projection

Replace a selected subtree in the **client's decoder** with `CanonicalBytes`:

```ts
import { IDL } from '@icp-sdk/core/candid';
import { CanonicalBytes, decodeCompatible } from 'candidx/candid';

const Reply = IDL.Record({
  first_index: IDL.Nat,
  transactions: IDL.Vec(CanonicalBytes),
});
const [reply] = decodeCompatible([Reply], rawReplyBytes) as [{
  first_index: bigint;
  transactions: Uint8Array[];
}];
for (const bytes of reply.transactions) {
  // Verify the hash/signature over bytes here, using your application's hash.
  const [transaction] = decodeCompatible([OldTransaction], bytes);
}
```

Each result is a new `Uint8Array` containing **one complete standalone canonical
Candid message**, including its `DIDL` header and value-derived type table. Hash
the entire result. Do not prepend a prefix or hash a slice of the enclosing
reply. The marker reads the actual incoming wire type and value, preserving
present fields and selected cases unknown to `OldTransaction`. It normalizes
the selected value independently of other transactions and surrounding reply
metadata. `transactions` is an array of independently decodable messages, not
one message for the entire array. Each element matches encoding that transaction
alone: `encodeCanonical([Transaction], [transaction])` or Motoko's
`to_candid_canonical(transaction)`. It accepts both ordinary and canonical
Candid replies.

For a `phash` chain, compute the hash of each **individual element** and compare
it with the following transaction's `phash`. Read `phash` from the decoded
transaction after preserving its canonical bytes. The enclosing array and
reply metadata do not contribute to an individual transaction's hash; the
transaction's own present `phash` field does.

For normal SDK actor calls, wrap each **outer return type** with `decoderType`:

```ts
import { Actor } from '@icp-sdk/core/agent';
import { CanonicalBytes, decoderType } from 'candidx/candid';

const idlFactory = ({ IDL }) => IDL.Service({
  get_transactions: IDL.Func([Request], [decoderType(IDL.Record({
    first_index: IDL.Nat,
    transactions: IDL.Vec(CanonicalBytes),
  }))], ['query']),
});
const actor = Actor.createActor(idlFactory, { agent, canisterId });
const reply = await actor.get_transactions(request);
```

`Request`, `OldTransaction`, `agent`, and `canisterId` come from your application.
Replace the return decoder in an existing actor factory; generated client
return declarations should describe these selected fields as `Uint8Array`.
The canister's `.did`, method arguments, and transport remain ordinary Candid.
`CanonicalBytes` and `decoderType` are client-only, decode-only types; they
cannot be used to encode requests or define a server schema. They also work
with `IDL.decode([decoderType(Reply)], bytes)`.

Use `decodeCompatible` or the root `decoderType` wrapper whenever a marker is
present. The wrapper ensures an unsupported, malformed, or over-limit selected
value fails the call, even inside an option where the SDK would otherwise
catch decoder errors and return absence. Surrounding fields still use the
usual typed decoding and projection; the marker only preserves its subtree.
Selected actual function/service references remain outside the `cx` data
profile. The value limits apply to each captured subtree, with the wire graph
cached per reply; `decodeCompatible` also bounds the complete input to 32 MiB.

Hash verification works even when an older schema cannot decode a newly used
variant case. In that case retain the canonical bytes and handle the unknown
transaction at application level. Old typed decoding can discard new fields;
re-encoding that projected object cannot reproduce the original hash.
Sending each canonical transaction as a `blob` remains another valid API design.

Native SDK `IDL.decode` works for the tested nonproblematic ordinary schemas.
Without a marker, both decoders project fields absent from the expected schema.

Run `npm test`. The [client validation report](../research/client_canonical_bytes.md)
includes the individual `phash` chain regression and reproducible cross-backend
capture checks. For a local application, a path dependency such as
`"candidx": "file:../candidx/ts"` works after building `dist/`; package exports
include JavaScript and TypeScript declarations. The `cx` rules and conformance
vectors define a fixed encoding contract; its name is not included in bytes.
