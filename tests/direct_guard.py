#!/usr/bin/env python3
"""Run direct APIs with ordinary serializers/normalizers/parsers disabled.

Copies are instrumented only under ignored _out. Production source is never
changed. Direct byte checks still use the unchanged golden/value oracle.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil

import direct
import verify

ROOT = verify.ROOT

RUST_MAIN = r'''
struct NativeGuard(u64);
impl candid::CandidType for NativeGuard {
    fn _ty() -> Type { TypeInner::Nat64.into() }
    fn idl_serialize<S: candid::types::Serializer>(&self, serializer: S) -> std::result::Result<(), S::Error> {
        if std::any::type_name::<S>().contains("candid::ser::") {
            return Err(<S::Error as serde::ser::Error>::custom("ordinary serializer invoked"));
        }
        serializer.serialize_nat64(self.0)
    }
}

fn main() {
    assert!(candid::encode_one(NativeGuard(1)).is_err(), "ordinary serializer guard did not fire");
    let expected = hex::decode("4449444c0001780100000000000000").unwrap();
    assert_eq!(candidx::encode_one(NativeGuard(1)).unwrap(), expected);
    assert_eq!(candidx::encode_args((NativeGuard(1),)).unwrap(), expected);
    let env = TypeEnv::new();
    let source: Type = TypeInner::Nat64.into();
    assert_eq!(candidx::encode_one_with_type(&NativeGuard(1), &env, &source).unwrap(), expected);
    assert_eq!(candidx::encode_args_with_types(&[NativeGuard(1)], &env, &[source]).unwrap(), expected);
    assert_eq!(candidx::encode_direct_args(&[candidx::DirectValue::Nat64(1)]).unwrap(), expected);
    let path = std::env::args().nth(1).expect("typed corpus path");
    let corpus: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    for c in corpus["cases"].as_array().unwrap() {
        let schemas = c["schemas"].as_array().cloned().unwrap_or_else(|| vec![c["schema"].clone()]);
        let values = c["values"].as_array().cloned().unwrap_or_else(|| vec![c["value"].clone()]);
        let mut env = TypeEnv::new();
        let mut counter = 0;
        let sources: Vec<_> = schemas.iter().map(|s| ty(s, &mut env, &mut counter)).collect();
        let values: Vec<_> = schemas.iter().zip(values).zip(&sources)
            .map(|((s,v),t)| val(s,&v).annotate_type(true,&env,t).unwrap()).collect();
        let canonical = candidx::encode_args_with_types(&values,&env,&sources).unwrap();
        println!("{}",json!({"id":c["id"],"canonical":hex::encode(canonical),"direct_path":"typed-values"}));
    }
}
'''


def check_output(text, cases, language):
    output = {row['id']: row for row in map(json.loads, text.splitlines())}
    assert set(output) == {case['id'] for case in cases}, (language, 'case IDs')
    for case in cases:
        row = output[case['id']]
        assert not row.get('error'), (language, case, row)
        expected = direct.expected_bytes(case)
        actual = bytes.fromhex(row['canonical'])
        assert actual == expected, (language, case['id'], row, expected.hex())
        assert sha256(actual).digest() == sha256(expected).digest(), (language, case['id'])


def run(out, rust_toolchain='1.98.1', motoko_repo=ROOT.parent / 'motoko_candid'):
    out = out.resolve()
    source_paths = [*sorted((ROOT / 'rust/src').glob('*.rs')), *sorted((ROOT / 'ts/src').glob('*.ts')),
                    ROOT / 'rust/examples/fuzz.rs', ROOT / 'ts/test/direct-guard.mjs',
                    ROOT / 'ts/test/direct-guard-loader.mjs', ROOT / 'tests/direct.py',
                    ROOT / 'ocaml/candidx.ml', ROOT / 'ocaml/candidx.mli',
                    ROOT / 'ocaml/direct_probe.ml', ROOT / 'ocaml/test_json.ml']
    fingerprints = {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in source_paths}
    scratch = out / 'direct-guards'
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    cases = direct.directed_cases()
    corpus = scratch / 'corpus.json'
    corpus.write_text(json.dumps({'profile': 'cx', 'cases': cases}, ensure_ascii=False, indent=2) + '\n')

    rust = scratch / 'rust'
    rust.mkdir()
    shutil.copytree(ROOT / 'rust/src', rust / 'src')
    for name in ('Cargo.toml', 'Cargo.lock'):
        shutil.copy2(ROOT / 'rust' / name, rust / name)
    source_path = rust / 'src/lib.rs'
    source = source_path.read_text()
    blocked = ['parse', 'read_value', 'canonicalize']
    # Guard each old parser/normalizer at its entry. No source changes in the
    # typed module are necessary for these entry points to be inaccessible.
    for name in blocked:
        match = re.search(r'^(?:pub )?fn ' + name + r"(?:<'[^>]+>)?\(", source, re.M)
        assert match, name
        opening = source.index('{', match.end()) + 1
        source = source[:opening] + f'\n    if core::hint::black_box(true) {{ return bad("direct guard: {name} invoked"); }}\n' + source[opening:]
    source_path.write_text(source)
    shutil.copytree(ROOT / 'rust/examples', rust / 'examples')
    prefix = (ROOT / 'rust/examples/fuzz.rs').read_text().split('fn main()', 1)[0]
    prefix = prefix.split('fn same_value(', 1)[0]
    prefix = prefix.replace('IDLArgs, ', '')
    (rust / 'examples/direct_guard.rs').write_text(prefix + RUST_MAIN)
    rust_env = dict(os.environ)
    for tool in ('rustc', 'rustdoc'):
        rust_env[tool.upper()] = verify.command(['rustup', 'which', '--toolchain', rust_toolchain, tool],
                                              'direct-guard-' + tool).strip()
    cargo = ['rustup', 'run', rust_toolchain, 'cargo']
    text = verify.command(cargo + ['run', '--locked', '--offline', '--features', 'fuzz', '--example',
        'direct_guard', '--', corpus], 'direct-guard-rust', cwd=rust, env=rust_env)
    check_output(text, cases, 'rust')

    ts_guard = ROOT / 'ts/test/direct-guard.mjs'
    assert ts_guard.is_file(), 'standalone TS direct serializer/normalizer guard is required'
    ts = verify.command(['node', ts_guard], 'direct-guard-ts')

    ocaml = scratch / 'ocaml'
    ocaml.mkdir()
    source = (ROOT / 'ocaml/candidx.ml').read_text()
    source = 'let direct_wire_guard = ref true\n' + source
    for name in ('parse', 'read_values', 'encode_values', 'canonicalize'):
        source, count = re.subn(r'(^let ' + name + r'[^\n]*=\n)',
            r'\1  if !direct_wire_guard then fail "direct guard: ' + name + r' invoked";\n', source, flags=re.M)
        assert count == 1, (name, count)
    (ocaml / 'candidx.ml').write_text(source)
    shutil.copy2(ROOT / 'ocaml/candidx.mli', ocaml / 'candidx.mli')
    shutil.copy2(ROOT / 'ocaml/direct_probe.ml', ocaml / 'direct_probe.ml')
    shutil.copy2(ROOT / 'ocaml/test_json.ml', ocaml / 'test_json.ml')
    (ocaml / 'dune-project').write_text('(lang dune 3.6)\n(name direct_wire_guard)\n')
    (ocaml / 'dune').write_text('(library (name candidx) (modules candidx) (libraries zarith))\n'
                              '(executable (name direct_probe) (modules direct_probe test_json) (libraries candidx))\n')
    command = ['dune', 'exec', '--root', ocaml, './direct_probe.exe', '--', corpus]
    if shutil.which('dune') and shutil.which('ocamlc'):
        text = verify.command(command, 'direct-guard-ocaml', cwd=ocaml)
    else:
        text = verify.command(['nix', 'develop', '--command', *command], 'direct-guard-ocaml', cwd=motoko_repo)
    check_output(text, cases, 'ocaml')
    return {'status': 'pass', 'directed_typed_cases': len(cases),
            'corpus_sha256': sha256(corpus.read_bytes()).hexdigest(),
            'vectors_sha256': sha256((ROOT / 'tests/vectors.json').read_bytes()).hexdigest(),
            'rust': {'typed_cases': len(cases), 'disabled': blocked, 'ordinary_serializer': 'rejected by native custom CandidType',
                     'exports': ['encode_one', 'encode_args', 'encode_one_with_type', 'encode_args_with_types', 'encode_direct_args']},
            'typescript': {'typed_cases': 6, 'disabled': ['IDL.encode', 'canonicalize', 'type encodeValue/encodeType/buildTypeTable'],
                           'standalone_guard_output': ts.strip()},
            'ocaml': {'typed_cases': len(cases), 'disabled': ['parse', 'read_values', 'encode_values', 'canonicalize']},
            'production_source_modified': False,
            'source_sha256': fingerprints,
            'sources_changed_during_run': fingerprints !=
                {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in source_paths}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=verify.OUT)
    parser.add_argument('--rust-toolchain', default='1.98.1')
    parser.add_argument('--motoko-repo', type=Path, default=ROOT.parent / 'motoko_candid')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    verify.OUT = args.out
    report = run(args.out, args.rust_toolchain, args.motoko_repo)
    (args.out / 'direct-guards-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
