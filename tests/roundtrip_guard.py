"""Mutation controls for independent original-value round-trip assertions.

Comparator probes construct native originals and mutants independently, without
serialization. Decoder probes receive valid wire values from the source-value
oracle; unchanged originals must pass and changed in-schema data must reject.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import direct

ROOT = Path(__file__).resolve().parent.parent


def cases():
    result = []

    def roots(name, schemas, values, mutant_schemas, mutant_values, equal=False,
              decoder_expected=None, native=True):
        result.append({"id": name, "schemas": deepcopy(schemas), "values": deepcopy(values),
                       "decoded_schemas": deepcopy(mutant_schemas), "decoded_values": deepcopy(mutant_values),
                       "expected_equal": equal, "decoder_expected": decoder_expected,
                       "native": native})

    def one(name, schema, original, mutant, equal=False):
        roots(name, [schema], [original], [schema], [mutant], equal, equal)

    one("bool", ["bool"], False, True)
    huge = 1 << 1023
    one("huge-nat", ["nat"], str(huge), str(huge + 1))
    one("negative-int", ["int"], str(-huge), str(-huge + 1))
    for kind, original, mutant in [("nat8", 255, 254), ("nat16", 65535, 65534),
                                  ("nat32", 4294967295, 4294967294),
                                  ("int8", -128, -127), ("int16", -32768, -32767),
                                  ("int32", -2147483648, -2147483647),
                                  ("nat64", str((1 << 64) - 1), str((1 << 64) - 2)),
                                  ("int64", str(-(1 << 63)), str(-(1 << 63) + 1))]:
        one(kind, [kind], original, mutant)
    for width, sign, nan, other_nan, finite in [(32, "0x80000000", "0x7fc00001", "0xff800001", "0x3fc00000"),
                                             (64, "0x8000000000000000", "0x7ff8000000000001", "0xfff0000000000001", "0x3ff8000000000000")]:
        schema = [f"float{width}bits"]
        one(f"float{width}-signed-zero", schema, sign, "0x0")
        one(f"float{width}-finite", schema, finite, "0x0")
        one(f"float{width}-subnormal", schema, "0x1", "0x0")
        one(f"float{width}-nan-to-finite", schema, nan, finite)
        one(f"float{width}-nan-class", schema, nan, other_nan, True)
        one(f"float{width}-same-zero", schema, sign, sign, True)
    for name, original, mutant in [("text", "original", "changed"),
                                  ("text-bom", "\uFEFFabc", "abc"),
                                  ("text-unicode", "é", "e\u0301")]:
        one(name, ["text"], original, mutant)
    nested = ["opt", ["opt", ["null"]]]
    one("option-none-some-none", nested, [], [[]])
    one("option-some-none-none", nested, [[]], [])
    one("option-some-none-some-some", nested, [[]], [[None]])
    one("option-child", ["opt", ["nat"]], ["123"], ["124"])
    one("vector-order", ["vec", ["nat8"]], [1, 2, 3], [2, 1, 3])
    one("vector-length", ["vec", ["nat8"]], [1, 2, 3], [1, 2])
    one("vector-element", ["vec", ["nat8"]], [1, 2, 3], [1, 2, 4])
    one("blob-order", ["blob"], [0, 128, 255], [0, 255, 128])
    one("blob-length", ["blob"], [0, 128, 255], [0, 128])
    one("vector-options", ["vec", nested], [[], [[]]], [[[]], []])
    record = ["record", ["a", ["nat8"]], ["b", ["opt", ["nat8"]]]]
    one("record-scalar", record, {"a": 7, "b": []}, {"a": 8, "b": []})
    one("record-presence", record, {"a": 7, "b": []}, {"a": 7, "b": [0]})
    choice = ["variant", ["a", ["nat8"]], ["b", ["null"]]]
    one("variant-tag", choice, {"a": 7}, {"b": None})
    one("variant-payload", choice, {"a": 7}, {"a": 8})
    tuple_schema = ["tuple", ["nat8"], ["text"]]
    one("tuple-field", tuple_schema, [7, "a"], [7, "b"])
    roots("tuple-arity-missing", [tuple_schema], [[7, "a"]], [["tuple", ["nat8"]]], [[7]],
          decoder_expected=False, native=False)
    roots("tuple-arity-extra", [tuple_schema], [[7, "a"]], [["tuple", ["nat8"], ["text"], ["null"]]],
          [[7, "a", None]], native=False)
    one("principal", ["principal"], [1, 2, 3], [1, 3, 2])
    linked = ["list", ["nat"], False]
    one("recursive-list-presence", linked, [], [{"item": "1", "next": []}])
    one("recursive-list-item", linked, [{"item": "1", "next": []}], [{"item": "2", "next": []}])
    one("recursive-chain-tail", ["chain"], {"head": "1", "tail": []},
        {"head": "1", "tail": [{"head": "2", "tail": []}]})
    roots("root-order", [["nat8"], ["nat8"]], [1, 2], [["nat8"], ["nat8"]], [2, 1], decoder_expected=False)
    roots("root-extra", [["nat8"]], [7], [["nat8"], ["null"]], [7, None], decoder_expected=False, native=False)
    roots("root-missing-optional", [["opt", ["nat8"]]], [[]], [], [], decoder_expected=False, native=False)
    roots("root-missing-reserved", [["reserved"]], [{"ignored": 1}], [], [], decoder_expected=False, native=False)
    roots("zero-roots-extra", [], [], [["null"]], [None], decoder_expected=False, native=False)
    one("same-null", ["null"], None, None, True)
    one("same-huge-nat", ["nat"], str(huge), str(huge), True)
    one("same-text-bom", ["text"], "\uFEFF\0é🧪", "\uFEFF\0é🧪", True)
    one("same-empty-vector", ["vec", ["nat"]], [], [], True)
    one("same-some-none", nested, [[]], [[]], True)
    one("reserved-ignored-value", ["reserved"], {"ignored": 1}, {"ignored": 2}, True)
    one("reserved-field", ["record", ["a", ["nat8"]], ["r", ["reserved"]]],
        {"a": 7, "r": 1}, {"a": 7, "r": "ignored"}, True)
    more_choice = ["variant", *choice[1:], ["unused", ["text"]]]
    roots("unused-variant-case", [choice], [{"a": 7}], [more_choice], [{"a": 7}], True, True)
    roots("optional-field-native-arity", [record], [{"a": 7, "b": []}],
          [["record", ["a", ["nat8"]]]], [{"a": 7}], False, True, False)
    roots("same-zero-roots", [], [], [], [], True, True)
    return result


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def _execute(out, name, command):
    result = subprocess.run([str(x) for x in command], cwd=ROOT, capture_output=True, text=True)
    (out / f"{name}.stdout").write_text(result.stdout)
    (out / f"{name}.stderr").write_text(result.stderr)
    return result


def _rows(result, wanted, name, exit_code):
    if result.returncode != exit_code:
        raise AssertionError(f"{name}: exit {result.returncode}, expected {exit_code}: {result.stderr}\n{result.stdout}")
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != len(wanted) or {row['id'] for row in rows} != {case['id'] for case in wanted}:
        raise AssertionError(f"{name}: missing or duplicate mutation results")
    return {row['id']: row for row in rows}


def _native(out, probes, compiler, core):
    import fuzz
    renderer, bodies, selected = fuzz.MotokoSource(), [], []
    for case in probes:
        if not case['native']:
            continue
        if len(case['schemas']) != len(case['decoded_schemas']):
            continue
        declarations, checks = [], []
        for i, (schema, original, mutant) in enumerate(zip(case['schemas'], case['values'], case['decoded_values'])):
            typename = renderer.ty(schema)
            original_literal, mutant_literal = renderer.val(schema, original), renderer.val(schema, mutant)
            if schema[0] == 'reserved':
                original_literal, mutant_literal = '0', '"different ignored value"'
            declarations += [f"let a{i} : {typename} = {original_literal};",
                             f"let b{i} : {typename} = {mutant_literal};"]
            checks.append(f"{renderer.equal(schema)}(a{i}, b{i})")
        expression = ' and '.join(checks) or 'true'
        expected = 'true' if case['expected_equal'] else 'false'
        bodies.append("do { " + ' '.join(declarations) +
                      f" if (({expression}) != {expected}) Prim.trap(\"native comparator {case['id']}\"); }};")
        selected.append(case['id'])
    source, wasm = out / 'roundtrip-native.mo', out / 'roundtrip-native.wasm'
    source.write_text('\n'.join(['import Prim "mo:⛔";', fuzz.PRINT_MO.split('func printBlob', 1)[0],
                                 *renderer.defs, *renderer.functions, *bodies,
                                 f'Prim.debugPrint("native comparator controls passed: {len(selected)}");']) + '\n')
    command = [compiler]
    if core is not None:
        command += ['--package', 'core', core]
    command += ['-wasi-system-api', '-o', wasm, source]
    built = _execute(out, 'roundtrip-native-build', command)
    if built.returncode:
        if 'type error [M0029], unbound type Float32' in built.stderr:
            def has_float32(schema):
                kind = schema[0]
                if kind in ('float32', 'float32bits'):
                    return True
                if kind in ('opt', 'vec', 'list'):
                    return has_float32(schema[1])
                if kind == 'tuple':
                    return any(has_float32(child) for child in schema[1:])
                if kind in ('record', 'variant'):
                    return any(has_float32(child) for _, child in schema[1:])
                return False
            skipped = [case['id'] for case in probes if case['native'] and
                       any(has_float32(schema) for schema in case['schemas'])]
            if skipped:
                (out / 'roundtrip-native-unavailable-float32.stderr').write_text(built.stderr)
                remaining = [case for case in probes if case['id'] not in skipped]
                report = _native(out, remaining, compiler, core)
                report.update({'unavailable_kinds': ['float32'], 'skipped_count': len(skipped),
                               'skipped_cases': skipped,
                               'skip_diagnostic': 'type error [M0029], unbound type Float32'})
                return report
        raise AssertionError(f"native comparator build failed: {built.stderr}")
    import wasmtime
    config = wasmtime.Config()
    config.wasm_memory64 = True
    config.wasm_threads = True
    engine = wasmtime.Engine(config)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store, wasi = wasmtime.Store(engine), wasmtime.WasiConfig()
    stdout, stderr = out / 'roundtrip-native.stdout', out / 'roundtrip-native.stderr'
    wasi.stdout_file, wasi.stderr_file = str(stdout), str(stderr)
    store.set_wasi(wasi)
    instance = linker.instantiate(store, wasmtime.Module.from_file(engine, str(wasm)))
    try:
        instance.exports(store)['_start'](store)
    except wasmtime.ExitTrap as error:
        if error.code:
            raise
    if stderr.read_text():
        raise AssertionError(f"native comparator failed: {stderr.read_text()}")
    if stdout.read_text().strip() != f"native comparator controls passed: {len(selected)}":
        raise AssertionError('native comparator completion marker missing')
    return {'status': 'pass', 'cases': len(selected),
            'positive_cases': sum(case['expected_equal'] for case in probes if case['id'] in selected),
            'negative_cases': sum(not case['expected_equal'] for case in probes if case['id'] in selected),
            'statically_incompatible_cases': [case['id'] for case in probes if not case['native']],
            'source_sha256': sha256(source.read_bytes()).hexdigest(),
            'compiler_sha256': sha256(Path(compiler).read_bytes()).hexdigest()}


def run(out, compiler=None, core=None):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    probes = cases()
    comparison = out / 'roundtrip-comparison.json'
    controls = out / 'roundtrip-decoder-controls.json'
    negatives = out / 'roundtrip-decoder-negatives.json'
    hashes = {'comparison': _write(comparison, {'profile': 'cx', 'comparison_only': True, 'cases': probes})}
    control_cases, negative_cases = [], []
    for case in probes:
        original = {'id': case['id'], 'schemas': case['schemas'], 'values': case['values']}
        wire = direct.expected_bytes(original).hex()
        control_cases.append({**original, 'decode_inputs': {'original-source-oracle': wire}})
        if case['decoder_expected'] is None:
            continue
        mutant = {'schemas': case['decoded_schemas'], 'values': case['decoded_values']}
        changed_wire = direct.expected_bytes(mutant).hex()
        tested = {**original, 'id': 'mutant-' + case['id'], 'decode_inputs': {'mutant-source-oracle': changed_wire}}
        if case['decoder_expected']:
            control_cases.append(tested)
        else:
            if changed_wire == wire:
                raise AssertionError(f"{case['id']}: decoder negative has unchanged canonical data")
            negative_cases.append(tested)
    hashes['decoder_controls'] = _write(controls, {'profile': 'cx', 'decode_only': True, 'cases': control_cases})
    hashes['decoder_negatives'] = _write(negatives, {'profile': 'cx', 'decode_only': True, 'cases': negative_cases})
    commands = {'rust': [ROOT / 'rust/target/debug/examples/fuzz'], 'typescript': ['node', ROOT / 'ts/test/fuzz.mjs']}
    backends = {}
    for backend, command in commands.items():
        name = f'roundtrip-{backend}-comparison'
        rows = _rows(_execute(out, name, [*command, comparison]), probes, name, 0)
        for case in probes:
            if rows[case['id']].get('comparison_equal') is not case['expected_equal']:
                raise AssertionError(f"{name}: comparator accepted/rejected the wrong mutant {case['id']}: {rows[case['id']]}")
        name = f'roundtrip-{backend}-controls'
        rows = _rows(_execute(out, name, [*command, controls]), control_cases, name, 0)
        for case in control_cases:
            if rows[case['id']].get('roundtrips') != {key: 'exact' for key in case['decode_inputs']}:
                raise AssertionError(f"{name}: original/equivalent control failed {case['id']}: {rows[case['id']]}")
        name = f'roundtrip-{backend}-negatives'
        rows = _rows(_execute(out, name, [*command, negatives]), negative_cases, name, 1)
        for case in negative_cases:
            if not rows[case['id']].get('error'):
                raise AssertionError(f"{name}: changed original data escaped {case['id']}: {rows[case['id']]}")
        backends[backend] = {'status': 'pass', 'comparator_cases': len(probes),
                             'comparator_positive_cases': sum(case['expected_equal'] for case in probes),
                             'comparator_negative_cases': sum(not case['expected_equal'] for case in probes),
                             'decoder_positive_cases': len(control_cases), 'decoder_negative_cases': len(negative_cases)}
    native = _native(out, probes, compiler, core) if compiler is not None else {'status': 'not-requested'}
    report = {'status': 'pass', 'method': 'Independent original/native mutant witnesses; valid decoder inputs from the source-value oracle.',
              'cases': len(probes), 'backends': backends, 'native_motoko': native, 'corpus_sha256': hashes,
              'guard_source_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
              'driver_sha256': {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
                               for path in [ROOT / 'rust/examples/fuzz.rs', ROOT / 'ts/test/fuzz.mjs', ROOT / 'tests/fuzz.py', ROOT / 'tests/direct.py']}}
    _write(out / 'roundtrip-guard.json', report)
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / '_out/roundtrip-guard')
    parser.add_argument('--compiler', type=Path)
    parser.add_argument('--core', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.out, args.compiler, args.core), indent=2))
