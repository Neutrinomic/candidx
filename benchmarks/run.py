#!/usr/bin/env python3
"""Local reproducible timing. External wall clock and Wasm fuel, never IC cycles."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import struct
import subprocess
import sys
import time
from runtime_sources import runtime_source

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_out"


def command(argv, cwd=ROOT, env=None, input_text=None):
    p = subprocess.run([str(a) for a in argv], cwd=cwd, env=env,
                       input=input_text, text=True, capture_output=True)
    if p.returncode:
        raise RuntimeError(f"{argv}:\n{p.stderr}\n{p.stdout}")
    return p.stdout


def native_motoko(path):
    import wasmtime
    config = wasmtime.Config()
    config.wasm_memory64 = True
    config.wasm_threads = True
    config.consume_fuel = True
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, str(path))
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    linker.allow_shadowing = True
    store = wasmtime.Store(engine)
    store.set_fuel(10**14)
    store.set_wasi(wasmtime.WasiConfig())
    samples, metadata, starts, pending = {}, {}, {}, bytearray()
    lines = []

    def write(caller, fd, iovs, iovs_len, written):
        memory = caller.get("memory")
        total = 0
        for i in range(iovs_len):
            pointer, size = struct.unpack("<II", memory.read(caller, iovs + i*8, iovs + i*8 + 8))
            pending.extend(memory.read(caller, pointer, pointer + size))
            total += size
        memory.write(caller, struct.pack("<I", total), written)
        while b"\n" in pending:
            line, _, rest = pending.partition(b"\n")
            pending[:] = rest
            text = line.decode("utf-8")
            lines.append(text)
            fields = text.split()
            if fields[0] == "case":
                metadata[fields[1]] = {"ordinary_bytes": int(fields[2]), "canonical_bytes": int(fields[3])}
            elif fields[0] == "start":
                key = tuple(fields[1:4])
                starts[key] = (time.perf_counter_ns(), store.get_fuel(), int(fields[4]))
            elif fields[0] == "end":
                key = tuple(fields[1:4])
                started, fuel, count = starts.pop(key)
                elapsed = time.perf_counter_ns() - started
                used = fuel - store.get_fuel()
                samples.setdefault(key[:2], []).append({"us": elapsed / 1000 / count,
                    "fuel": used / count, "allocated": int(fields[4]) / count, "iterations": count})
        return 0

    linker.define_func("wasi_snapshot_preview1", "fd_write",
        wasmtime.FuncType([wasmtime.ValType.i32()] * 4, [wasmtime.ValType.i32()]), write, access_caller=True)
    instance = linker.instantiate(store, module)
    try:
        instance.exports(store)["_start"](store)
    except wasmtime.ExitTrap as e:
        if e.code:
            raise
    assert not starts and not pending
    result = []
    for name, info in metadata.items():
        case = {"name": name, **info}
        for (case_name, phase), values in samples.items():
            if case_name != name:
                continue
            assert len(values) == 7
            case[phase] = {"median_us": statistics.median(v["us"] for v in values),
                "min_us": min(v["us"] for v in values), "max_us": max(v["us"] for v in values),
                "median_fuel": statistics.median(v["fuel"] for v in values),
                "allocated_bytes_per_op": statistics.median(v["allocated"] for v in values),
                "iterations_per_trial": values[0]["iterations"], "trials": len(values)}
        result.append(case)
    assert len(result) == 4
    return {"language": "Motoko", "runtime": "WASI / Wasmtime with fuel instrumentation; fuel is not IC cycles", "cases": result}, lines


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--label", default="current")
    p.add_argument("--moc", type=Path, default=ROOT.parent / "motoko_candid/bin/moc")
    p.add_argument("--core", type=Path, default=ROOT.parent / "motoko-core/src")
    p.add_argument("--motoko-repo", type=Path, default=ROOT.parent / "motoko_candid")
    p.add_argument("--rust-toolchain", default="1.98.1")
    p.add_argument("--wasm-moc", type=Path, default=ROOT / "wasm/moc")
    p.add_argument("--languages", nargs="+", choices=["rust", "ts", "ocaml", "motoko", "motoko-wasm"], default=["rust", "ts", "ocaml", "motoko", "motoko-wasm"])
    args = p.parse_args()
    OUT.mkdir(exist_ok=True)
    results = {}
    if "rust" in args.languages or "ocaml" in args.languages:
        rustc = command(["rustup", "which", "--toolchain", args.rust_toolchain, "rustc"]).strip()
        rustdoc = command(["rustup", "which", "--toolchain", args.rust_toolchain, "rustdoc"]).strip()
        command(["rustup", "run", args.rust_toolchain, "cargo", "build", "--release", "--locked", "--example", "bench", "--manifest-path", ROOT / "rust/Cargo.toml"], env={**os.environ, "RUSTC": rustc, "RUSTDOC": rustdoc})
        rust = json.loads(command([ROOT / "rust/target/release/examples/bench"]))
        if "rust" in args.languages:
            results["rust"] = rust
            (OUT / f"bench-rust-{args.label}.json").write_text(json.dumps(rust, indent=2) + "\n")
    if "ts" in args.languages:
        command(["npm", "run", "build"], cwd=ROOT / "ts")
        ts = json.loads(command(["node", "--expose-gc", ROOT / "ts/test/benchmark.mjs"]))
        results["ts"] = ts
        (OUT / f"bench-ts-{args.label}.json").write_text(json.dumps(ts, indent=2) + "\n")
    if "ocaml" in args.languages:
        if shutil.which("dune") and shutil.which("ocamlc"):
            command(["dune", "build"], cwd=ROOT / "ocaml")
        else:
            command(["nix", "develop", "--command", "dune", "build", "--root", ROOT / "ocaml"], cwd=args.motoko_repo)
        results["ocaml"] = {"language": "OCaml", "ordinary_codec": "No standalone OCaml ordinary binary Candid codec in the audited repositories", "cases": []}
        for case in rust["cases"]:
            timing = json.loads(command([ROOT / "ocaml/_build/default/bench.exe"], input_text=case["raw_hex"] + "\n"))
            results["ocaml"]["cases"].append({"name": case["name"], "ordinary_bytes": case["ordinary_bytes"], "canonical_bytes": case["canonical_bytes"], "canonicalize_preencoded": timing})
        direct = json.loads(command([ROOT / "ocaml/_build/default/direct_bench.exe"]))
        for case in results["ocaml"]["cases"]:
            typed = next(c for c in direct["cases"] if c["name"] == case["name"])
            expected = next(c for c in rust["cases"] if c["name"] == case["name"])
            assert typed["canonical_hex"] == expected["canonical_hex"], f"OCaml direct: {case['name']}"
            case["canonical_hex"] = typed["canonical_hex"]
            case["canonical_encode_direct"] = typed["canonical_encode_direct"]
        (OUT / f"bench-ocaml-{args.label}.json").write_text(json.dumps(results["ocaml"], indent=2) + "\n")
    for lang in ("motoko", "motoko-wasm"):
        if lang not in args.languages:
            continue
        source = ROOT / "motoko/test/benchmark.mo"
        compiler = args.moc
        if lang == "motoko-wasm":
            compiler = args.wasm_moc
            source = OUT / "benchmark-rts.mo"
            source.write_text(runtime_source("benchmark"))
        artifact = OUT / f"benchmark-{lang}.wasm"
        command([compiler, "--package", "core", args.core, "-wasi-system-api", "-o", artifact, source])
        mo, lines = native_motoko(artifact)
        if lang == "motoko-wasm":
            mo["language"] = "Motoko generated typed Wasm"
        mo["wasm_sha256"] = sha256(artifact.read_bytes()).hexdigest()
        mo["compiler"] = command([compiler, "--version"]).strip()
        results[lang] = mo
        (OUT / f"bench-{lang}-{args.label}.json").write_text(json.dumps(mo, indent=2) + "\n")
        (OUT / f"bench-{lang}-{args.label}.stdout").write_text("\n".join(lines) + "\n")
    if "rust" in results and "ts" in results:
        for a, b in zip(results["rust"]["cases"], results["ts"]["cases"]):
            assert a["name"] == b["name"] and a["canonical_hex"] == b["canonical_hex"]
    report = {"recorded_utc": datetime.now(timezone.utc).isoformat(), "label": args.label,
        "platform": platform.platform(), "machine": platform.machine(), "method": "7 trials; median wall time; Rust optimized; TS warmed V8; Wasm fuel instrumentation; no hashes timed",
        "source_fingerprints": {str(f.relative_to(ROOT)): sha256(f.read_bytes()).hexdigest()
            for base in ["rust/src", "rust/examples", "ts/src", "ts/test", "ocaml", "motoko/src", "motoko/test", "wasm/src"]
            for f in (ROOT / base).rglob("*.*")
            if f.is_file() and f.suffix not in {".install", ".pyc", ".pyo"}
            and f.name != ".DS_Store" and not f.name.endswith("~")
            and not any(part in {"_build", "target", "dist", "node_modules", "__pycache__"} for part in f.relative_to(ROOT).parts)}
            | {str(f.relative_to(ROOT)): sha256(f.read_bytes()).hexdigest() for f in [Path(__file__), ROOT / "benchmarks/runtime_sources.py", ROOT / "wasm/motoko-compiler.patch",
                ROOT / "rust/Cargo.toml", ROOT / "rust/Cargo.lock", ROOT / "ts/package.json", ROOT / "ts/package-lock.json", ROOT / "wasm/artifacts/manifest.json"]},
        "results": results}
    path = OUT / f"benchmarks-{args.label}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    for lang, result in results.items():
        for case in result["cases"]:
            primary = "canonical_encode_direct" if "canonical_encode_direct" in case else "canonical_encode_pipeline"
            print(lang, case["name"], "canonicalize_us", round(case["canonicalize_preencoded"]["median_us"], 2),
                primary, round(case[primary]["median_us"], 2),
                "canonical/ordinary", round(case[primary]["median_us"] / case["ordinary_encode"]["median_us"], 2) if "ordinary_encode" in case else "n/a", flush=True)
    print(path)


if __name__ == "__main__":
    main()
