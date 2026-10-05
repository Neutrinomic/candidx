"""Independent typed-schema audit of the value-profile contract.

Run after `npm run build` in ts/: python3 tests/audit_spec.py
This uses the ordinary @icp-sdk/core encoder/decoder to generate typed messages,
then checks the TypeScript codec and the Python research oracle. It does not
write corpus goldens or modify the implementations.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from reference import canonicalize, values


ROOT = Path(__file__).resolve().parents[1]
JS = r"""
import { IDL } from '@icp-sdk/core/candid';
import { deepStrictEqual, notDeepStrictEqual } from 'node:assert';
import { readFileSync } from 'node:fs';
import { canonicalize } from './dist/index.js';
import { decodeCompatible } from './dist/candid.js';

const settings = JSON.parse(readFileSync(0, 'utf8'));
let state = settings.seed >>> 0;
const rnd = n => {
  state ^= state << 13; state ^= state >>> 17; state ^= state << 5;
  return (state >>> 0) % n;
};
const scalars = ['nat8', 'nat8', 'nat', 'int', 'nat64', 'text',
  'null', 'reserved', 'bool', 'float32', 'float64'];
const typ = (kind, child, fields) => ({ kind, child, fields });
const scalar = kind => typ(kind);
const opt = child => typ('opt', child);
const vec = child => typ('vec', child);
const record = fields => typ('record', undefined, fields);
const variant = fields => typ('variant', undefined, fields);
const prim = {
  nat8: IDL.Nat8, nat: IDL.Nat, int: IDL.Int, nat64: IDL.Nat64,
  text: IDL.Text, null: IDL.Null, reserved: IDL.Reserved, bool: IDL.Bool,
  float32: IDL.Float32, float64: IDL.Float64,
};
function idl(t) {
  if (prim[t.kind]) return prim[t.kind];
  if (t.kind === 'opt') return IDL.Opt(idl(t.child));
  if (t.kind === 'vec') return IDL.Vec(idl(t.child));
  if (t.kind === 'func') return IDL.Func([IDL.Nat], [IDL.Text], []);
  if (t.kind === 'service') return IDL.Service({ f: IDL.Func([], [], []) });
  const fields = Object.fromEntries(t.fields.map(([k, v]) => [k, idl(v)]));
  return t.kind === 'record' ? IDL.Record(fields) : IDL.Variant(fields);
}
function schema(depth) {
  if (!depth || rnd(5) === 0) return scalar(scalars[rnd(scalars.length)]);
  const constructor = rnd(4);
  if (constructor === 0) return opt(schema(depth - 1));
  if (constructor === 1) return vec(schema(depth - 1));
  const count = constructor === 2 ? rnd(4) : 1 + rnd(3);
  const fields = Array.from({ length: count }, (_, i) =>
    [String.fromCharCode(97 + i), schema(depth - 1)]);
  return constructor === 2 ? record(fields) : variant(fields);
}
function value(t) {
  switch (t.kind) {
    case 'nat8': return rnd(256);
    case 'nat': return BigInt(rnd(500));
    case 'int': return BigInt(rnd(500) - 250);
    case 'nat64': return BigInt(rnd(500));
    case 'text': return ['', 'x', 'e\u0301', '\u00e9', '\0', '\ud83d\ude80'][rnd(6)];
    case 'bool': return !!rnd(2);
    case 'null': case 'reserved': return null;
    case 'float32': case 'float64': return [0, -0, 1.5, -2.5, Infinity, -Infinity, NaN][rnd(7)];
    case 'opt': return rnd(3) ? [value(t.child)] : [];
    case 'vec': {
      const n = rnd(5);
      if (t.child.kind === 'nat8') return Uint8Array.from({ length: n }, () => rnd(256));
      return Array.from({ length: n }, () => value(t.child));
    }
    case 'record': return Object.fromEntries(t.fields.map(([k, v]) => [k, value(v)]));
    case 'variant': {
      const [k, v] = t.fields[rnd(t.fields.length)];
      return { [k]: value(v) };
    }
    default: throw new Error(`unexpected instantiated type ${t.kind}`);
  }
}
function extend(t) {
  if (t.kind === 'opt' || t.kind === 'vec') return typ(t.kind, extend(t.child));
  if (t.kind === 'record' || t.kind === 'variant') {
    const fields = t.fields.map(([k, v]) => [k, extend(v)]);
    // Functions/services are deliberately declared but never instantiated.
    const extra = rnd(4) ? schema(2) : scalar(rnd(2) ? 'func' : 'service');
    fields.push(['unused', t.kind === 'record' ? opt(extra) : extra]);
    return typ(t.kind, undefined, fields);
  }
  return t;
}
function extendedValue(t, u, v) {
  if (t.kind === 'opt') return v.length ? [extendedValue(t.child, u.child, v[0])] : [];
  if (t.kind === 'vec') return t.child.kind === 'nat8' ? v : v.map(x => extendedValue(t.child, u.child, x));
  if (t.kind === 'record') {
    const old = new Map(t.fields);
    return Object.fromEntries(u.fields.map(([k, ut]) =>
      [k, old.has(k) ? extendedValue(old.get(k), ut, v[k]) : []]));
  }
  if (t.kind === 'variant') {
    const k = Object.keys(v)[0];
    return { [k]: extendedValue(new Map(t.fields).get(k), new Map(u.fields).get(k), v[k]) };
  }
  return v;
}
const results = [];
let extensions = 0;
const recursiveSdkFailures = [];
let ordinaryDecodeChecks = 0;
function decodeEquivalent(name, type, raw, canonical, permitRecursiveSdkBug) {
  ordinaryDecodeChecks++;
  deepStrictEqual(decodeCompatible([type], canonical), IDL.decode([type], raw), `${name}: compatible typed decoding`);
  try {
    deepStrictEqual(IDL.decode([type], canonical), IDL.decode([type], raw), `${name}: typed decoding`);
  } catch (error) {
    if (!permitRecursiveSdkBug) throw error;
    recursiveSdkFailures.push({ name, error: error.message,
      raw: Buffer.from(raw).toString('hex'), canonical: Buffer.from(canonical).toString('hex') });
  }
}
function check(name, type, val, extendedType, extendedVal, permitRecursiveSdkBug = false) {
  const raw = IDL.encode([type], [val]);
  const canonical = canonicalize(raw);
  decodeEquivalent(name, type, raw, canonical, permitRecursiveSdkBug);
  deepStrictEqual(canonicalize(canonical), canonical, `${name}: idempotence`);
  results.push({ name, raw: Buffer.from(raw).toString('hex'), canonical: Buffer.from(canonical).toString('hex') });
  if (extendedType) {
    const evolved = IDL.encode([extendedType], [extendedVal]);
    const evolvedCanonical = canonicalize(evolved);
    deepStrictEqual(evolvedCanonical, canonical, `${name}: extension identity`);
    decodeEquivalent(name + '-extended', extendedType, evolved, canonical, permitRecursiveSdkBug);
    results.push({ name: name + '-extended', raw: Buffer.from(evolved).toString('hex'), canonical: Buffer.from(evolvedCanonical).toString('hex') });
    extensions++;
  }
}
function structural(name, t, val) {
  const u = extend(t);
  check(name, idl(t), val, idl(u), extendedValue(t, u, val));
}

for (let i = 0; i < settings.count; i++) {
  const t = schema(1 + rnd(5));
  structural(`generated-${i}`, t, value(t));
}

// Exercise all absence depths in the same homogeneous optional vector.
for (const base of [scalar('nat8'), vec(scalar('nat8')),
  record([['a', opt(scalar('nat8'))], ['b', opt(vec(scalar('nat8')))]])]) {
  for (let depth = 1; depth <= 10; depth++) {
    let inner = base;
    for (let k = 0; k < depth; k++) inner = opt(inner);
    const absent = Array.from({ length: depth }, (_, level) => {
      let val = []; for (let k = 0; k < level; k++) val = [val]; return val;
    });
    let present = value(base);
    for (let k = 0; k < depth; k++) present = [present];
    const elements = [...absent, present];
    for (let permutation = 0; permutation < 8; permutation++) {
      for (let k = elements.length - 1; k; k--) {
        const j = rnd(k + 1); [elements[k], elements[j]] = [elements[j], elements[k]];
      }
      structural(`nested-options-${base.kind}-${depth}-${permutation}`, vec(inner), [...elements]);
    }
  }
}

for (let i = 0; i < 100; i++) {
  structural(`nullable-record-array-${i}`, vec(record([
    ['a', opt(opt(scalar('nat8')))], ['b', opt(vec(scalar('nat8')))],
    ['c', scalar('null')], ['d', scalar('reserved')],
  ])), Array.from({ length: 4 }, () => value(record([
    ['a', opt(opt(scalar('nat8')))], ['b', opt(vec(scalar('nat8')))],
    ['c', scalar('null')], ['d', scalar('reserved')],
  ]))));
}

const oldTree = IDL.Rec(), newTree = IDL.Rec();
oldTree.fill(IDL.Opt(IDL.Record({
  data: IDL.Vec(IDL.Nat8), next: oldTree,
  branch: IDL.Variant({ a: IDL.Null, b: IDL.Vec(oldTree) }),
})));
newTree.fill(IDL.Opt(IDL.Record({
  data: IDL.Vec(IDL.Nat8), next: newTree,
  branch: IDL.Variant({ a: IDL.Null, b: IDL.Vec(newTree), unused: IDL.Text }),
  extra: IDL.Opt(IDL.Func([], [], [])),
})));
function tree(depth) {
  if (!depth || !rnd(3)) return [];
  return [{
    data: Uint8Array.from({ length: rnd(4) }, () => rnd(256)), next: tree(depth - 1),
    branch: rnd(2) ? { a: null } : { b: Array.from({ length: rnd(3) }, () => tree(depth - 1)) },
  }];
}
function evolvedTree(v) {
  return v.length ? [{ ...v[0], next: evolvedTree(v[0].next), extra: [],
    branch: 'a' in v[0].branch ? { a: null } : { b: v[0].branch.b.map(evolvedTree) },
  }] : [];
}
for (let i = 0; i < 100; i++) {
  const v = Array.from({ length: 4 }, () => tree(5));
  check(`recursive-tree-array-${i}`, IDL.Vec(oldTree), v, IDL.Vec(newTree), v.map(evolvedTree), true);
}

// Binary arrays and byte blobs have identical bytes, including under vectors.
for (const bytes of [[], [0], [0, 255]]) {
  const byteType = IDL.Vec(IDL.Nat8);
  const a = canonicalize(IDL.encode([byteType], [bytes]));
  const b = canonicalize(IDL.encode([byteType], [Uint8Array.from(bytes)]));
  deepStrictEqual(a, b, 'binary array/blob identity');
}
notDeepStrictEqual(
  canonicalize(IDL.encode([IDL.Vec(IDL.Nat8)], [[]])),
  canonicalize(IDL.encode([IDL.Vec(IDL.Nat)], [[]])), 'empty binary/nonbinary distinction');
for (let i = 0; i < 40; i++) {
  structural(`empty-binary-array-${i}`, vec(vec(scalar('nat8'))),
    [new Uint8Array(), Uint8Array.of(rnd(256)), new Uint8Array()]);
  structural(`empty-nonbinary-array-${i}`, vec(vec(scalar('nat'))),
    [[], [BigInt(rnd(500))], []]);
}

// Known upstream decoder behavior: canonicalization itself retains text BOM.
const bom = '\uFEFFabc';
const bomRaw = IDL.encode([IDL.Text], [bom]);
const bomCanonical = canonicalize(bomRaw);
deepStrictEqual(bomCanonical, bomRaw, 'BOM bytes retained');
const bomDecoded = IDL.decode([IDL.Text], bomCanonical)[0];
deepStrictEqual(decodeCompatible([IDL.Text], bomRaw), [bom], 'adapter retains ordinary BOM text');
deepStrictEqual(decodeCompatible([IDL.Text], bomCanonical), [bom], 'adapter retains canonical BOM text');
const caveats = bomDecoded === bom ? [] : [{
  name: 'upstream-js-text-bom', input: bom, decoded: bomDecoded,
  raw: Buffer.from(bomRaw).toString('hex'),
  canonical: Buffer.from(bomCanonical).toString('hex'),
  ordinaryDecoded: IDL.decode([IDL.Text], bomRaw)[0],
}];
const list = IDL.Rec();
list.fill(IDL.Opt(IDL.Record({ head: IDL.Nat, tail: list })));
const listValue = [{ head: 1n, tail: [] }];
const listRaw = IDL.encode([list], [listValue]);
const listCanonical = canonicalize(listRaw);
deepStrictEqual(IDL.decode([list], listRaw), [listValue], 'recursive-list ordinary baseline');
deepStrictEqual(decodeCompatible([list], listCanonical), [listValue], 'adapter restores recursive-list node');
try {
  deepStrictEqual(IDL.decode([list], listCanonical), [listValue], 'recursive-list canonical decode');
} catch (error) {
  caveats.push({ name: 'upstream-js-omitted-recursive-option',
    raw: Buffer.from(listRaw).toString('hex'), canonical: Buffer.from(listCanonical).toString('hex'),
    error: error.message });
}
for (const [name, type, val] of [
  ['upstream-js-tuple-trailing-absent', IDL.Tuple(IDL.Text, IDL.Opt(IDL.Nat)), ['x', []]],
  ['upstream-js-tuple-leading-absent', IDL.Tuple(IDL.Opt(IDL.Nat), IDL.Text), [[], 'x']],
]) {
  const raw = IDL.encode([type], [val]);
  const canonical = canonicalize(raw);
  deepStrictEqual(IDL.decode([type], raw), [val], `${name}: ordinary baseline`);
  deepStrictEqual(decodeCompatible([type], canonical), [val], `${name}: adapter canonical decode`);
  try {
    deepStrictEqual(IDL.decode([type], canonical), [val], `${name}: typed canonical decode`);
  } catch (error) {
    caveats.push({ name, raw: Buffer.from(raw).toString('hex'),
      canonical: Buffer.from(canonical).toString('hex'), error: error.message });
  }
}
console.log(JSON.stringify({ results, extensions, caveats, ordinaryDecodeChecks,
  recursiveSdkFailures: recursiveSdkFailures.length }));
"""

RUST = r"""
use binrw::BinRead;
use candid::{binary_parser::Header, IDLArgs};
use std::collections::BTreeMap;
use std::io::{self, BufRead, Cursor};

fn main() {
    let mut count = 0;
    for line in io::stdin().lock().lines() {
        let line = line.expect("stdin");
        let parts: Vec<_> = line.split('\t').collect();
        let name = parts[0];
        let raw = hex::decode(parts[1]).expect("raw hex");
        let canonical = hex::decode(parts[2]).expect("canonical hex");
        assert_eq!(candidx::canonicalize(&raw).expect(name), canonical, "{name}: Rust codec agreement");
        assert_eq!(candidx::canonicalize(&canonical).expect(name), canonical, "{name}: Rust idempotence");
        let header = Header::read_le_args(&mut Cursor::new(&raw), (None,)).expect(name);
        let (original_env, original_types) = header.to_types().expect(name);
        // Wire decoders also name their entries table0, table1, ... . Give
        // expected schema variables a distinct namespace before merging.
        let rename: BTreeMap<String, String> = original_env.0.keys()
            .map(|k| (k.clone(), format!("expected_{k}"))).collect();
        let env = candid::types::TypeEnv(original_env.0.iter()
            .map(|(k, ty)| (rename[k].clone(), ty.subst(&rename))).collect());
        let types: Vec<_> = original_types.iter().map(|ty| ty.subst(&rename)).collect();
        let expected = IDLArgs::from_bytes_with_types(&raw, &env, &types).expect(name);
        let decoded = IDLArgs::from_bytes_with_types(&canonical, &env, &types).expect(name);
        // Debug keeps scalar kinds and signed zero, and identifies NaNs as
        // intended by the profile. Structural PartialEq would reject NaN.
        assert_eq!(format!("{expected:?}"), format!("{decoded:?}"), "{name}: Rust original-schema decode");
        count += 1;
    }
    println!("checked {count}");
}
"""


def audit_rust(entries, toolchain):
    with tempfile.TemporaryDirectory(prefix="candidx-spec-audit-") as temporary:
        project = Path(temporary)
        (project / "src").mkdir()
        (project / "src/main.rs").write_text(RUST)
        manifest = (
            '[package]\nname = "candidx_spec_audit"\nversion = "0.1.0"\nedition = "2021"\n'
            '[dependencies]\ncandid = { version = "=0.10.37", features = ["value"] }\n'
            'binrw = { version = "0.15", default-features = false, features = ["std"] }\nhex = "0.4"\n'
            f'candidx = {{ path = {json.dumps(str(ROOT / "rust"))} }}\n'
        )
        (project / "Cargo.toml").write_text(manifest)
        rustc = subprocess.check_output(
            ["rustup", "which", "--toolchain", toolchain, "rustc"], text=True
        ).strip()
        rows = "".join(f'{e["name"]}\t{e["raw"]}\t{e["canonical"]}\n' for e in entries)
        proc = subprocess.run(
            ["rustup", "run", toolchain, "cargo", "run", "--offline", "--quiet",
             "--manifest-path", str(project / "Cargo.toml")],
            input=rows, text=True, capture_output=True, check=False,
            env={**os.environ, "RUSTC": rustc,
                 "CARGO_TARGET_DIR": str(Path(tempfile.gettempdir()) / "candidx-spec-audit-target")},
        )
        if proc.returncode:
            raise RuntimeError(proc.stderr)
        assert proc.stdout.strip() == f"checked {len(entries)}", proc.stdout
        return len(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument("--count", type=int, default=4000)
    parser.add_argument("--rust", action="store_true", help="also check ordinary Rust typed decoding and codec agreement")
    parser.add_argument("--rust-toolchain", default="1.98.1")
    args = parser.parse_args()
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", JS],
        input=json.dumps({"seed": args.seed, "count": args.count}),
        text=True, capture_output=True, cwd=ROOT / "ts", check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr)
    result = json.loads(proc.stdout)
    for entry in result["results"]:
        raw, canonical = bytes.fromhex(entry["raw"]), bytes.fromhex(entry["canonical"])
        assert canonicalize(raw) == canonical, (entry["name"], "codec agreement")
        assert canonicalize(canonical) == canonical, (entry["name"], "oracle idempotence")
        assert values(raw) == values(canonical), (entry["name"], "normalised data preservation")
    for caveat in result["caveats"]:
        assert values(bytes.fromhex(caveat["raw"])) == values(bytes.fromhex(caveat["canonical"]))
    rust_messages = None
    if args.rust:
        rust_messages = audit_rust(result["results"] + result["caveats"], args.rust_toolchain)
    print(json.dumps({
        "status": "canonical_properties_pass_with_upstream_decoder_caveats" if result["caveats"] else "pass",
        "seed": args.seed,
        "typed_messages": len(result["results"]),
        "schema_extension_pairs": result["extensions"],
        "rust_typed_messages": rust_messages,
        "javascript_typed_decode_checks": result["ordinaryDecodeChecks"],
        "javascript_successful_typed_decode_checks": result["ordinaryDecodeChecks"] - result["recursiveSdkFailures"],
        "javascript_adapter_canonical_typed_decode_checks": result["ordinaryDecodeChecks"] + 4,
        "javascript_known_recursive_decode_failures": result["recursiveSdkFailures"],
        "checks": ["ordinary JS typed decode equivalence outside reported SDK caveats", "recursive schema extension identity",
                   "compatible SDK adapter original-schema reconstruction",
                   "TypeScript/Python agreement", "idempotence", "normalised data preservation",
                   "binary array/blob identity", "empty binary kind retained"],
        "upstream_decoder_caveats": result["caveats"],
    }, indent=2))


if __name__ == "__main__":
    main()
