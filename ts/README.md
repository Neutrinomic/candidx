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
is rejected explicitly.

Native SDK `IDL.decode` works for the tested nonproblematic schemas. Neither
decoder preserves present fields that the expected schema omits; retain the
original bytes for hash verification.

Run `npm test`. For a local application, a path dependency such as
`"candidx": "file:../candidx/ts"` works after building `dist/`; package exports
include JavaScript and TypeScript declarations. The `cx` rules and conformance
vectors define a fixed encoding contract; its name is not included in bytes.
