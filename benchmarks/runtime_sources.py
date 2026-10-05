#!/usr/bin/env python3
"""Generate matched direct-runtime probes from the native Motoko fixtures."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Native probe changed; review the runtime adapter")
    return source.replace(old, new, 1)


def runtime_source(kind):
    source = (ROOT / f"motoko/test/{kind}.mo").read_text()
    source = replace_once(source, 'import CandidX "../src/lib";\n', '')
    if kind == "benchmark":
        source = replace_once(source, '''func canonical(raw : Blob) : Blob = switch (CandidX.canonicalize(raw)) {
  case (#ok bytes) bytes;
  case (#err e) Prim.trap(e);
};''', 'func canonical(raw : Blob) : Blob { Prim.canonicalizeCandid(raw) };')
        source = replace_once(source, '  let raw = to_candid(block);\n  let canon = canonical(raw);',
            '  let canon = to_candid_canonical(block);\n  let raw = to_candid(block);\n  assert canon == canonical(raw);')
        marker = '  bench(name, "ordinary_decode", fast, func() { (decode(raw)).tx.size() });'
        source = replace_once(source, marker,
            '  bench(name, "canonical_encode_direct", fast, func() { (to_candid_canonical(block)).size() });\n' + marker)
    elif kind == "heap":
        source = replace_once(source, '''  func canonical(raw : Blob) : Blob = switch (CandidX.canonicalize(raw)) {
    case (#ok bytes) bytes;
    case (#err e) Prim.trap(e)
  };''', '  func canonical(raw : Blob) : Blob { Prim.canonicalizeCandid(raw) };')
        marker = '  func decode(raw : Blob) {'
        source = replace_once(source, marker, '''  func encodeCanonical() : Blob {
    if (kind == 1) return to_candid_canonical(numbers);
    if (kind >= 2) return to_candid_canonical(textValue);
    switch block { case (?b) to_candid_canonical(b); case null Prim.trap("configure first") }
  };
''' + marker)
        source = replace_once(source, '    ordinary := encode();\n    canonicalInput := canonical(ordinary);',
            '    canonicalInput := encodeCanonical();\n    ordinary := encode();\n    assert canonicalInput == canonical(ordinary);')
        source = replace_once(source, '        case 5 {}; // Control for measurement bookkeeping.',
            '        case 5 {}; // Control for measurement bookkeeping.\n        case 6 { output := encodeCanonical() };')
    else:
        raise ValueError(kind)
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['benchmark', 'heap'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(runtime_source(args.kind))


if __name__ == '__main__':
    main()
