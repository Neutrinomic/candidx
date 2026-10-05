#!/usr/bin/env python3
"""Benchmark canonical float vectors and late noncanonical NaN fallback."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from run import native_motoko, command, ROOT, OUT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=ROOT / 'motoko/src/lib.mo')
    parser.add_argument('--moc', type=Path, default=ROOT.parent / 'motoko_candid/bin/moc')
    parser.add_argument('--core', type=Path, default=ROOT.parent / 'motoko-core/src')
    parser.add_argument('--label', default='local')
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    base = (ROOT / 'motoko/test/benchmark.mo').read_text()
    prefix = base[:base.index('for ((name, count, memoSize)')].replace('import Principal "mo:core/Principal";\n', '')
    prefix = prefix[:prefix.index('type Tx =')] + prefix[prefix.index('func canonical('):prefix.index('func decode(')] + prefix[prefix.index('var checksum ='):]
    library = args.library.resolve()
    source = OUT / f'identity-floats-{args.label}.mo'
    path = os.path.relpath(library.with_suffix(''), source.parent)
    body = r'''
let values = Array.tabulate<Float>(4096, func(i) { [0.0, -0.0, 1.0, -1.0][i % 4] });
let encoded = to_candid(values);
let offset = Prim.abs((encoded.size() : Int) - 4096 * 8);
func withNaN(position : Nat, nan : Blob) : Blob {
  Prim.arrayToBlob(Array.tabulate<Nat8>(encoded.size(), func(i) {
    let start = offset + position * 8;
    if (i >= start and i < start + 8) nan[Prim.abs((i : Int) - start)] else encoded[i]
  }))
};
let data = switch (Prim.decodeUtf8(Prim.arrayToBlob(Array.tabulate<Nat8>(65536, func(i) { Prim.natToNat8(65 + i % 26) })))) {
  case (?s) s;
  case null Prim.trap("text creation")
};
for ((name, raw) in [
  ("float64-canonical", withNaN(4095, "\00\00\00\00\00\00\f8\7f")),
  ("float64-nan-first", withNaN(0, "\01\00\00\00\00\00\f0\7f")),
  ("float64-nan-last", withNaN(4095, "\bc\9a\78\56\34\12\f8\ff")),
  ("text-64k", to_candid(data))
].values()) {
  let canon = canonical(raw);
  assert canonical(canon) == canon;
  Prim.debugPrint("case " # name # " " # Nat.toText(raw.size()) # " " # Nat.toText(canon.size()));
  bench(name, "canonicalize_preencoded", 10, func() { canonical(raw).size() })
};
Prim.debugPrint("checksum " # Nat.toText(checksum));
'''
    source.write_text(prefix.replace('../src/lib', path) + body)
    artifact = source.with_suffix('.wasm')
    command([args.moc, '--package', 'core', args.core, '-wasi-system-api', '-o', artifact, source])
    report, _ = native_motoko(artifact)
    report['codec_source_sha256'] = sha256(library.read_bytes()).hexdigest()
    report['harness_source_sha256'] = sha256(Path(__file__).read_bytes()).hexdigest()
    (OUT / f'identity-floats-{args.label}.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
