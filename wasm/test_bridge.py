#!/usr/bin/env python3
"""Check the actual compiler/RTS path, interpreter parity, and typed recovery."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_out" / "wasm-bridge-tests"


def blob(hexadecimal):
    return '"' + ''.join('\\' + hexadecimal[i:i+2] for i in range(0, len(hexadecimal), 2)) + '"'


def run(command, expect_error=False):
    result = subprocess.run([str(item) for item in command], cwd=ROOT,
                            text=True, capture_output=True)
    if expect_error:
        assert result.returncode != 0, command
        assert "CandidX error:" in result.stdout + result.stderr, result.stdout + result.stderr
        assert "RTS panicked" not in result.stdout + result.stderr
    elif result.returncode:
        raise RuntimeError(f"{command}:\n{result.stdout}\n{result.stderr}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", action="append", type=Path, default=[])
    parser.add_argument("--moc", type=Path, default=ROOT / "wasm/moc")
    parser.add_argument("--wasmtime", default="wasmtime")
    parser.add_argument("--resource-stress", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    shared = json.loads((ROOT / "tests/vectors.json").read_text())
    positives = shared["vectors"] + shared["positive_edges"]
    for path in args.corpus:
        positives.extend(json.loads(path.read_text()))
    source = ['import Prim "mo:⛔";']
    messages = 0
    for case in positives:
        raws = case["raw"]
        for raw in raws.values() if isinstance(raws, dict) else [raws]:
            source.append(f'assert Prim.canonicalizeCandid({blob(raw)}) == {blob(case["canonical"])};')
            messages += 1
        source.append(f'assert Prim.canonicalizeCandid({blob(case["canonical"])}) == {blob(case["canonical"])};')
    path = OUT / "positive.mo"
    path.write_text('\n'.join(source) + '\n')
    # Complete literal messages exercise the separate inbound normalizer in
    # both interpreter and compiled modes; smoke.mo checks the typed operator.
    run([args.moc, "-r", path])
    run([args.moc, "-wasi-system-api", path, "-o", OUT / "positive.wasm"])
    run([args.wasmtime, "-W", "memory64", OUT / "positive.wasm"])
    run([args.moc, "-wasi-system-api", ROOT / "wasm/smoke.mo", "-o", OUT / "smoke.wasm"])
    run([args.wasmtime, "-W", "memory64", OUT / "smoke.wasm"])
    for case in shared["negative_cases"]:
        path = OUT / f'{case["id"]}.mo'
        path.write_text('import Prim "mo:⛔";\nlet _ = Prim.canonicalizeCandid(' + blob(case["raw"]) + ');\n')
        run([args.moc, "-r", path], expect_error=True)
        target = path.with_suffix(".wasm")
        run([args.moc, "-wasi-system-api", path, "-o", target])
        run([args.wasmtime, "-W", "memory64", target], expect_error=True)
    if args.resource_stress:
        # Direct conversion rejects oversized scalar payloads before magnitude
        # allocation or text-rope flattening inside the RTS visitor.
        scalar_sources = {
            "direct-nat-limit": f'let _ = to_candid_canonical(0x{1 << (4096 * 7):x} : Nat);\n',
            "direct-int-limit": f'let _ = to_candid_canonical(0x{1 << (4096 * 7 - 1):x} : Int);\n',
            "direct-text-limit": 'var text = "x"; var i = 0;\n'
                'while (i < 21) { text #= text; i += 1 };\n'
                'let _ = to_candid_canonical(text);\n',
        }
        for name, source in scalar_sources.items():
            path = OUT / f'{name}.mo'
            path.write_text(source)
            run([args.moc, "-r", path], expect_error=True)
            target = path.with_suffix(".wasm")
            run([args.moc, "-wasi-system-api", path, "-o", target])
            run([args.wasmtime, "-W", "memory64", target], expect_error=True)
        # 30,303 records of 65 explicit nulls consume 1,999,999 traversal
        # steps and only a tiny payload, but exceed the RTS scratch budget.
        count = 30_303
        length = bytearray()
        while count >= 128:
            length.append((count & 127) | 128)
            count >>= 7
        length.append(count)
        fields = b''.join(bytes([label, 127]) for label in range(65))
        raw = b"DIDL\x02\x6c\x41" + fields + b"\x6d\0\x01\x01" + length
        path = OUT / "scratch-limit.mo"
        path.write_text('import Prim "mo:⛔";\nlet _ = Prim.canonicalizeCandid(' + blob(raw.hex()) + ');\n')
        target = path.with_suffix(".wasm")
        run([args.moc, "-wasi-system-api", path, "-o", target])
        failure = run([args.wasmtime, "-W", "memory64", target], expect_error=True)
        assert "scratch byte limit" in failure.stdout + failure.stderr
    print(json.dumps({"raw_messages_per_runtime": messages,
        "idempotence_checks_per_runtime":len(positives),
        "malformed_trap_checks_per_runtime":len(shared["negative_cases"]),
        "runtimes":["native OCaml interpreter", "Motoko compiler-emitted memory64 Wasm/RTS"],
        "typed_recovery_smoke":True, "rts_scratch_limit_trap":args.resource_stress,
        "direct_scalar_resource_traps_per_runtime":3 if args.resource_stress else 0}))


if __name__ == "__main__":
    main()
