#!/usr/bin/env python3
"""Pack and consume local packages without publishing anything."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tarfile

import verify

ROOT = verify.ROOT


RUST_CONSUMER = r'''
use candid::{CandidType, Deserialize};

#[derive(CandidType, Deserialize, Debug, PartialEq)]
struct Wide { a: String, b: Option<String> }

fn main() {
    let value = Wide { a: "abc".into(), b: None };
    let bytes = candidx::encode_one(&value).unwrap();
    assert_eq!(hex::encode(&bytes), "4449444c016c016171010003616263");
    assert_eq!(candid::decode_one::<Wide>(&bytes).unwrap(), value);
    assert_eq!(hex::encode(candidx::encode_args((true, false, ())).unwrap()),
               "4449444c00037e7e7f0100");
    assert_eq!(hex::encode(candidx::encode_args(()).unwrap()), "4449444c0000");
    assert_eq!(hex::encode(candidx::encode_one(Vec::<u8>::new()).unwrap()),
               "4449444c016d7b010000");
    println!("packaged-rust-direct-native-values-passed");
}
'''


TS_CONSUMER = r'''
import assert from 'node:assert/strict';
import { IDL } from '@icp-sdk/core/candid';
import { encodeCanonical, decodeCompatible, CanonicalBytes, decoderType } from 'candidx/candid';

const text = IDL.Text, option = IDL.Opt(text);
const type = IDL.Record({a: text, b: option});
const value = {a:'abc', b:[]};
const methods = [];
for (const typ of [type, option, text]) {
  for (const key of ['encodeValue', 'buildTypeTable', '_buildTypeTable']) {
    const own = Object.getOwnPropertyDescriptor(typ, key);
    methods.push([typ, key, own]);
    Object.defineProperty(typ, key, {configurable: true, value() {throw new Error(`ordinary encoder ${key} invoked`);}});
  }
}
let bytes;
try { bytes = encodeCanonical([type], [value]); }
finally {
  for (const [typ, key, descriptor] of methods) {
    if (descriptor) Object.defineProperty(typ, key, descriptor);
    else delete typ[key];
  }
}
assert.equal(Buffer.from(bytes).toString('hex'), '4449444c016c016171010003616263');
assert.deepEqual(decodeCompatible([type], bytes), [value]);
assert.equal(Buffer.from(encodeCanonical([], [])).toString('hex'), '4449444c0000');
assert.equal(Buffer.from(encodeCanonical([IDL.Bool,IDL.Bool,IDL.Null], [true,false,null])).toString('hex'),
             '4449444c00037e7e7f0100');
const replyType = IDL.Record({ transactions: IDL.Vec(type), future_metadata: IDL.Text });
const reply = IDL.encode([replyType], [{ transactions: [{ a: 'abc', b: ['future field'] }, value], future_metadata: 'new' }]);
const clientType = IDL.Record({ transactions: IDL.Vec(CanonicalBytes) });
const expected = [{ transactions: [encodeCanonical([type], [{ a: 'abc', b: ['future field'] }]), bytes] }];
assert.deepEqual(decodeCompatible([clientType], reply), expected);
assert.deepEqual(IDL.decode([decoderType(clientType)], reply), expected);
console.log('packaged-typescript-direct-native-values-passed');
'''

TS_DECLARATIONS = r'''
import { IDL } from '@icp-sdk/core/candid';
import { CanonicalBytes, decoderType, decodeCompatible, encodeCanonical } from 'candidx/candid';
import { canonicalize, PROFILE } from 'candidx';
const marker: IDL.Type<Uint8Array> = CanonicalBytes;
const reply: IDL.Type<Uint8Array[]> = decoderType(IDL.Vec(marker));
const bytes: Uint8Array = encodeCanonical([IDL.Nat], [7n]);
const canonical: Uint8Array = canonicalize(bytes);
const decoded: unknown[] = decodeCompatible([reply], canonical);
const profile: 'cx' = PROFILE;
'''


OCAML_CONSUMER = r'''
type row = { a : string; b : string option }

let hex bytes =
  let out = Buffer.create (2 * Bytes.length bytes) in
  Bytes.iter (fun char -> Buffer.add_string out (Printf.sprintf "%02x" (Char.code char))) bytes;
  Buffer.contents out

let check expected = function
  | Error error -> failwith error
  | Ok bytes -> if hex bytes <> expected then failwith "packaged direct bytes differ"

let () =
  let witness = Candidx.record [
    Candidx.field "a" Candidx.text (fun row -> row.a);
    Candidx.field "b" (Candidx.option Candidx.text) (fun row -> row.b)] in
  check "4449444c016c016171010003616263" (Candidx.encode witness {a="abc"; b=None});
  check "4449444c00037e7e7f0100"
    (Candidx.encode_args [Candidx.arg Candidx.bool true; Candidx.arg Candidx.bool false; Candidx.arg Candidx.null ()]);
  check "4449444c0000" (Candidx.encode_args []);
  check "4449444c016d7b010000" (Candidx.encode Candidx.blob Bytes.empty);
  print_endline "packaged-ocaml-direct-native-values-passed"
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rust-toolchain', default='1.98.1')
    parser.add_argument('--motoko-repo', type=Path, default=ROOT.parent / 'motoko_candid')
    parser.add_argument('--out', type=Path, default=verify.OUT,
                        help='directory for logs and extracted package consumers')
    parser.add_argument('--report', type=Path, default=ROOT / 'research/results/packaging.json')
    args = parser.parse_args()
    verify.OUT = args.out.resolve()
    verify.OUT.mkdir(parents=True, exist_ok=True)
    starting_sources = verify.source_fingerprints()
    out = verify.OUT / 'package-cx-1.0.0'
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    cargo = ['rustup', 'run', args.rust_toolchain, 'cargo']
    env = dict(os.environ)
    for tool in ('rustc', 'rustdoc'):
        env[tool.upper()] = verify.command(['rustup', 'which', '--toolchain', args.rust_toolchain, tool], f'package-{tool}').strip()
    verify.command(cargo + ['package', '--locked', '--allow-dirty', '--offline', '--manifest-path', ROOT / 'rust/Cargo.toml'], 'package-rust', env=env)
    crate = ROOT / 'rust/target/package/candidx-1.0.0.crate'
    with tarfile.open(crate) as archive:
        archive.extractall(out, filter='data')
    packed = out / 'candidx-1.0.0'
    assert (packed / 'LICENSE').read_bytes() == (ROOT / 'LICENSE').read_bytes()
    for features in ('--no-default-features', '--all-features'):
        verify.command(cargo + ['test', '--locked', '--offline', features], f'package-rust-{features[2:]}', cwd=packed, env=env)
    rust_consumer = out / 'rust-consumer'
    (rust_consumer / 'src').mkdir(parents=True)
    (rust_consumer / 'Cargo.toml').write_text('''[package]
name = "cx-local-direct-consumer"
version = "1.0.0"
edition = "2021"

[dependencies]
candidx = { path = "../candidx-1.0.0" }
candid = "0.10"
serde = { version = "1", features = ["derive"] }
hex = "0.4"
''')
    (rust_consumer / 'src/main.rs').write_text(RUST_CONSUMER)
    verify.command(cargo + ['run', '--offline'], 'package-rust-direct-consumer', cwd=rust_consumer, env=env)

    verify.command(['npm', 'run', 'build'], 'package-ts-build', cwd=ROOT / 'ts')
    listing = json.loads(verify.command(['npm', 'pack', '--json', '--pack-destination', out], 'package-ts-pack', cwd=ROOT / 'ts'))[0]
    assert 'LICENSE' in {item['path'] for item in listing['files']}
    assert listing['version'] == '1.0.0'
    consumer = out / 'ts-consumer'
    consumer.mkdir()
    (consumer / 'package.json').write_text(json.dumps({'name': 'cx-local-package-check', 'version': '1.0.0', 'private': True, 'type': 'module'}) + '\n')
    verify.command(['npm', 'install', '--ignore-scripts', '--omit=optional', out / listing['filename']], 'package-ts-install', cwd=consumer)
    assert not (consumer / 'node_modules/@icp-sdk/core').exists()
    (consumer / 'standalone.mjs').write_text("import assert from 'node:assert/strict';\nimport { PROFILE, canonicalize } from 'candidx';\nassert.equal(PROFILE, 'cx');\nconst bytes = Uint8Array.from(Buffer.from('4449444c016c016171010003616263', 'hex'));\nassert.deepEqual(canonicalize(bytes), bytes);\n")
    verify.command(['node', 'standalone.mjs'], 'package-ts-standalone', cwd=consumer)
    verify.command(['npm', 'install', '--ignore-scripts', '@icp-sdk/core@6.1.0'], 'package-ts-sdk-install', cwd=consumer)
    (consumer / 'typed.mjs').write_text(TS_CONSUMER)
    verify.command(['node', 'typed.mjs'], 'package-ts-typed', cwd=consumer)
    (consumer / 'types.ts').write_text(TS_DECLARATIONS)
    verify.command(['node', ROOT / 'ts/node_modules/typescript/bin/tsc', '--noEmit', '--strict',
        '--target', 'ES2022', '--module', 'NodeNext', '--moduleResolution', 'NodeNext', '--skipLibCheck', 'types.ts'],
        'package-ts-declarations', cwd=consumer)
    if shutil.which('dune') and shutil.which('ocamlc'):
        verify.command(['dune', 'build', '-p', 'candidx'], 'package-ocaml', cwd=ROOT / 'ocaml')
    else:
        # Let Nix select the compiler environment, then enter the package root:
        # -p implies --root ., so a conflicting --root flag is invalid.
        verify.command(['nix', 'develop', '--command', 'python3', '-c',
            'import os,subprocess,sys; os.chdir(sys.argv[1]); subprocess.run(["dune","build","-p","candidx"],check=True)', ROOT / 'ocaml'],
            'package-ocaml', cwd=args.motoko_repo)
    install = (ROOT / 'ocaml/candidx.install').read_text()
    assert 'LICENSE' in install
    # Copy only the public installed package, then consume it from an isolated
    # project. No checkout source or private driver is visible to Dune.
    ocaml_prefix = out / 'ocaml-installed'
    library = ocaml_prefix / 'lib/candidx'
    library.parent.mkdir(parents=True)
    shutil.copytree(ROOT / 'ocaml/_build/install/default/lib/candidx', library, symlinks=False)
    ocaml_consumer = out / 'ocaml-consumer'
    ocaml_consumer.mkdir()
    (ocaml_consumer / 'dune-project').write_text('(lang dune 3.6)\n(name cx_local_direct_consumer)\n')
    (ocaml_consumer / 'dune').write_text('(executable (name consumer) (libraries candidx))\n')
    (ocaml_consumer / 'consumer.ml').write_text(OCAML_CONSUMER)
    ocaml_env = {**os.environ, 'OCAMLPATH': str(ocaml_prefix / 'lib') + os.pathsep + os.environ.get('OCAMLPATH', '')}
    if shutil.which('dune') and shutil.which('ocamlc'):
        verify.command(['dune', 'exec', '--root', ocaml_consumer, './consumer.exe'],
                       'package-ocaml-direct-consumer', cwd=ocaml_consumer, env=ocaml_env)
    else:
        verify.command(['nix', 'develop', '--command', 'python3', '-c',
            'import os,subprocess,sys; os.environ["OCAMLPATH"]=sys.argv[2]+os.pathsep+os.environ.get("OCAMLPATH",""); '
            'subprocess.run(["dune","exec","--root",sys.argv[1],"./consumer.exe"],check=True)',
            ocaml_consumer, ocaml_prefix / 'lib'], 'package-ocaml-direct-consumer', cwd=args.motoko_repo)
    versions = {'rust': '1.0.0', 'ts': listing['version'], 'ocaml': '1.0.0', 'motoko': '1.0.0', 'wasm': '1.0.0'}
    for package in versions:
        assert (ROOT / package / 'LICENSE').read_bytes() == (ROOT / 'LICENSE').read_bytes(), package
    report = {'status': 'pass', 'profile': 'cx', 'completed_utc': datetime.now(timezone.utc).isoformat(),
        'package_versions': versions, 'license': 'MIT',
        'rust_package': 'extracted crate tests --no-default-features/--all-features; isolated direct native-value consumer',
        'typescript_package': 'local npm tarball; SDK-free import; direct consumer with ordinary serializer callbacks disabled; CanonicalBytes client capture and public TypeScript declarations',
        'ocaml_package': 'copied installed package with LICENSE; isolated direct native-record and multi-argument consumer',
        'direct_consumer_goldens': ['evolution-record-old', 'bool-null', 'empty-args', 'empty-blob'],
        'crate_sha256': sha256(crate.read_bytes()).hexdigest(),
        'tarball_sha256': sha256((out / listing['filename']).read_bytes()).hexdigest(),
        'source_sha256': starting_sources,
        'sources_changed_during_run': verify.source_fingerprints() != starting_sources,
        'publication': 'none'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
