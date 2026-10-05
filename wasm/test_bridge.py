#!/usr/bin/env python3
"""Check direct typed compiler encoding, interpreter parity, and value recovery.

Expected bytes come from the independent source-value oracle. The native
Motoko CandidX library supplies a secondary ordinary-encoding control; the
compiler exposes only to_candid_canonical and never an inbound normalizer.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import direct
import fuzz
import verify


def run(command, out, name, expect_error=False):
    result = subprocess.run([str(item) for item in command], cwd=ROOT,
                            text=True, capture_output=True)
    (out / f"{name}.stdout").write_text(result.stdout)
    (out / f"{name}.stderr").write_text(result.stderr)
    output = result.stdout + result.stderr
    if expect_error:
        assert result.returncode != 0, (name, "expected a resource trap")
        assert "CandidX error:" in output, output
        assert "RTS panicked" not in output, output
    elif result.returncode:
        raise RuntimeError(f"{name}:\n{result.stdout}\n{result.stderr}")
    return result


def resource_sources():
    # Every input is built natively before the first encoding call. In
    # particular, ordinary serialization cannot warm the large-int literal.
    return {
        "direct-nat-limit": f"let _ = to_candid_canonical(0x{1 << (4096 * 7):x} : Nat);\n",
        "direct-int-limit": f"let _ = to_candid_canonical(0x{1 << (4096 * 7 - 1):x} : Int);\n",
        "direct-negative-int-limit": f"let _ = to_candid_canonical(-0x{1 << (4096 * 8):x} : Int);\n",
        "direct-negative-int-below-limit": f"let _ = to_candid_canonical(-0x{(1 << (4096 * 7 - 1)) + 1:x} : Int);\n",
        "direct-text-limit": 'var text = "é"; var i = 0;\n'
            'while (i < 20) { text #= text; i += 1 };\n'
            'let _ = to_candid_canonical(text);\n',
        "direct-collection-limit": 'import Prim "mo:⛔";\n'
            'let values = Prim.Array_tabulate<Null>(2_000_001, func(_) { null });\n'
            'let _ = to_candid_canonical(values);\n',
        "direct-step-limit": 'import Prim "mo:⛔";\n'
            'let values = Prim.Array_tabulate<Null>(2_000_000, func(_) { null });\n'
            'let _ = to_candid_canonical(values);\n',
        "direct-depth-limit": 'type List = ?{ head : Nat; tail : List };\n'
            'var value : List = null; var i = 0;\n'
            'while (i < 130) { value := ?{ head = i; tail = value }; i += 1 };\n'
            'let _ = to_candid_canonical(value);\n',
        "direct-output-limit": 'import Prim "mo:⛔";\n'
            'var text = "x"; var i = 0;\n'
            'while (i < 20) { text #= text; i += 1 };\n'
            'let values = Prim.Array_tabulate<Text>(32, func(_) { text });\n'
            'let _ = to_candid_canonical(values);\n',
        "direct-binary-depth-limit": depth_source(binary=True, nonempty=True),
    }


def focused_cases():
    return [{"id": "bridge-rope-text", "schema": ["text"],
             "value": "é" + "\ufeff🧪\0東京" * 128},
            {"id": "bridge-nested-option-array", "schema": ["vec", ["opt", ["opt", ["text"]]]],
             "value": [[], [[]], [[""]], [["é"]]]}]


def typed_source(cases, path, compiled_controls):
    fuzz.motoko_direct_source(cases, path, compiled_controls=compiled_controls)
    source = path.read_text()
    for index, case in enumerate(cases):
        if case["id"] != "bridge-rope-text":
            continue
        original = f"  let value0 : DirectCase{index}Root0 = {fuzz.mo_literal(case['value'])};"
        replacement = f'''  var rope = "é"; var segment = 0;
  while (segment < 128) {{ rope #= {fuzz.mo_literal(chr(0xfeff) + '🧪' + chr(0) + '東京')}; segment += 1 }};
  let value0 : DirectCase{index}Root0 = rope;'''
        assert source.count(original) == 1
        source = source.replace(original, replacement, 1)
    path.write_text(source)


def depth_source(binary, nonempty=False):
    # Root depth zero; the empty vector is at depth256. Its unused element
    # type contributes no native value visit. A real byte at257 must reject.
    scalar = "Nat8" if binary else "Nat"
    lines = [f"type D0 = [{scalar}];"]
    lines.extend(f"type D{i} = ?D{i-1};" for i in range(1, 257))
    lines.append("let v0 : D0 = [0];" if nonempty else "let v0 : D0 = [];")
    lines.extend(f"let v{i} : D{i} = ?v{i-1};" for i in range(1, 257))
    lines.append("let encoded = to_candid_canonical(v256);")
    if not nonempty:
        schema, value = ["vec", [scalar.lower()]], []
        for _ in range(256):
            schema, value = ["opt", schema], [value]
        expected = direct.expected_bytes({"schema": schema, "value": value})
        lines.append(f"assert encoded == {verify.mo_blob(expected.hex())};")
    return "\n".join(lines) + "\n"


def integer_boundaries(recover=True):
    cases = [("nat", "Nat", (1 << (4096 * 7)) - 1,
              bytes.fromhex("4449444c00017d") + b"\xff" * 4095 + b"\x7f"),
             ("int-negative", "Int", -(1 << (4096 * 7 - 1)),
              bytes.fromhex("4449444c00017c") + b"\x80" * 4095 + b"\x40"),
             ("int-positive", "Int", (1 << (4096 * 7 - 1)) - 1,
              bytes.fromhex("4449444c00017c") + b"\xff" * 4095 + b"\x3f")]
    source = []
    for name, typ, value, expected in cases:
        literal = ("-" if value < 0 else "") + f"0x{abs(value):x}"
        recovery = f"  assert (from_candid(encoded) : ?{typ}) == ?original;\n" if recover else ""
        source.append(f'''do {{
  let original = {literal} : {typ};
  let encoded = to_candid_canonical(original);
  assert encoded == {verify.mo_blob(expected.hex())};
{recovery}\
}};''')
    return "\n".join(source) + "\n", len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", action="append", type=Path, default=[],
                        help="additional typed cases with schema/value or schemas/values")
    parser.add_argument("--moc", type=Path, default=ROOT / "wasm/moc")
    parser.add_argument("--core", type=Path, default=ROOT.parent / "motoko-core/src")
    parser.add_argument("--wasmtime", default="wasmtime")
    parser.add_argument("--out", type=Path, default=ROOT / "_out/wasm-minimal-bridge-tests")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--resource-stress", action="store_true")
    args = parser.parse_args()
    assert args.batch_size > 0 and args.repetitions > 0
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    moc = [args.moc, "--package", "core", args.core]
    shared_count = len(direct.directed_cases())
    cases = direct.directed_cases() + focused_cases()
    for path in args.corpus:
        document = json.loads(path.read_text())
        additions = document["cases"] if isinstance(document, dict) else document
        if not all("schema" in case or "schemas" in case for case in additions):
            raise ValueError(f"{path}: supply typed corpus.json, not serialized records.json")
        cases.extend(additions)
    assert len({case["id"] for case in cases}) == len(cases), "duplicate typed case IDs"
    frozen = direct.check_frozen_sources()
    expected = {case["id"]: direct.expected_bytes(case) for case in cases}
    hashes = {}
    sources = {}
    for batch, start in enumerate(range(0, len(cases), args.batch_size)):
        subset = cases[start:start + args.batch_size]
        source = out / f"direct-{batch}.mo"
        typed_source(subset, source, compiled_controls=True)
        interpreter_source = out / f"direct-{batch}-interpreter.mo"
        typed_source(subset, interpreter_source, compiled_controls=False)
        assert "canonicalizeCandid" not in source.read_text()
        sources[source.name] = sha256(source.read_bytes()).hexdigest()
        sources[interpreter_source.name] = sha256(interpreter_source.read_bytes()).hexdigest()
        interpreted = run(moc + ["-r", interpreter_source], out, f"direct-{batch}-interpreter")
        target = source.with_suffix(".wasm")
        run(moc + ["-wasi-system-api", source, "-o", target], out, f"direct-{batch}-build")
        outputs = [("interpreter", interpreted.stdout)]
        for repetition in range(args.repetitions):
            result = run([args.wasmtime, "-W", "memory64", target], out,
                         f"direct-{batch}-wasm-{repetition}")
            outputs.append((f"wasm-{repetition}", result.stdout))
        assert len({output for _, output in outputs[1:]}) == 1, "fresh-instance output drift"
        for runtime, output in outputs:
            decoded = verify.parse_probe(output, motoko=True)
            for case in subset:
                actual = decoded["canonical:" + case["id"]]
                assert actual == expected[case["id"]], (runtime, case["id"])
                digest = sha256(actual).hexdigest()
                if case["id"] in frozen:
                    assert digest == frozen[case["id"]]["application_sha256"]
                hashes[case["id"]] = digest
    # Separate smoke starts with a cold large-int literal and asserts fixed
    # bytes and ordinary decoder recovery before any normalization control.
    smoke = ROOT / "wasm/smoke.mo"
    target = out / "smoke.wasm"
    run(moc + ["-wasi-system-api", smoke, "-o", target], out, "smoke-build")
    run([args.wasmtime, "-W", "memory64", target], out, "smoke-wasm")
    resources = resource_sources() if args.resource_stress else {}
    boundary_count = 0
    depth_count = 0
    if args.resource_stress:
        source, boundary_count = integer_boundaries()
        path = out / "direct-integer-boundaries.mo"
        path.write_text(source)
        sources[path.name] = sha256(path.read_bytes()).hexdigest()
        interpreter_source = out / "direct-integer-boundaries-interpreter.mo"
        interpreter_source.write_text(integer_boundaries(recover=False)[0])
        sources[interpreter_source.name] = sha256(interpreter_source.read_bytes()).hexdigest()
        run(moc + ["-r", interpreter_source], out, "integer-boundaries-interpreter")
        target = path.with_suffix(".wasm")
        run(moc + ["-wasi-system-api", path, "-o", target], out, "integer-boundaries-build")
        run([args.wasmtime, "-W", "memory64", target], out, "integer-boundaries-wasm")
        for binary in (True, False):
            name = "empty-byte-vec-depth256" if binary else "empty-nat-vec-depth256"
            path = out / f"{name}.mo"
            path.write_text(depth_source(binary))
            sources[path.name] = sha256(path.read_bytes()).hexdigest()
            run(moc + ["-r", path], out, name + "-interpreter")
            target = path.with_suffix(".wasm")
            run(moc + ["-wasi-system-api", path, "-o", target], out, name + "-build")
            run([args.wasmtime, "-W", "memory64", target], out, name + "-wasm")
            depth_count += 1
    for name, source in resources.items():
        path = out / f"{name}.mo"
        path.write_text(source)
        sources[path.name] = sha256(path.read_bytes()).hexdigest()
        run(moc + ["-r", path], out, name + "-interpreter", expect_error=True)
        target = path.with_suffix(".wasm")
        run(moc + ["-wasi-system-api", path, "-o", target], out, name + "-build")
        run([args.wasmtime, "-W", "memory64", target], out, name + "-wasm", expect_error=True)
    report = {
        "status": "pass", "profile": "cx", "public_api": "to_candid_canonical",
        "typed_cases_per_runtime": len(cases), "frozen_golden_cases": len(frozen),
        "shared_typed_corpus_cases": shared_count, "focused_native_value_cases": len(focused_cases()),
        "original_value_recoveries": {"compiler_wasm": len(cases), "interpreter": "ordinary decoder not available"},
        "ordinary_original_value_recoveries": {"compiler_wasm": len(cases), "interpreter": "ordinary encoder/decoder not available"},
        "wasm_fresh_instances_per_batch": args.repetitions,
        "direct_resource_traps_per_runtime": len(resources),
        "cold_integer_boundary_positives_per_runtime": boundary_count,
        "integer_boundary_original_recoveries_compiler_wasm": boundary_count,
        "depth256_empty_vector_byte_positives_per_runtime": depth_count,
        "resource_cases": list(resources), "typed_recovery_smoke_compiler_wasm": True,
        "runtimes": ["native OCaml interpreter", "compiler-emitted memory64 Wasm/RTS"],
        "expected_bytes": "independent typed source-value oracle and unchanged frozen goldens",
        "ordinary_control": "to_candid followed by the existing native Motoko CandidX library",
        "case_sha256": hashes, "generated_source_sha256": sources,
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
