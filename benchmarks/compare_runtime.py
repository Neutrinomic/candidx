#!/usr/bin/env python3
"""Compare two compiler implementations using exactly the same native source.

Use --prepare-only to generate the program without compiling or measuring.
Captures are confined to --out and never replace the historical benchmarks.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess

from run import native_motoko
from runtime_sources import runtime_source

ROOT = Path(__file__).resolve().parent.parent


def fingerprints(compiler):
    files = ["src/codegen/compile.ml", "src/prelude/prim.mo",
             "rts/motoko-rts/src/candid_canonical.rs", "rts/motoko-rts/src/candidx.rs",
             "src/_build/default/exes/moc.exe", "rts/mo-rts.wasm", "rts/mo-rts-debug.wasm"]
    return {name: sha256((compiler / name).read_bytes()).hexdigest()
            for name in files if (compiler / name).is_file()}


def command(argv, env, out, name):
    result = subprocess.run([str(arg) for arg in argv], cwd=ROOT,
                            env=env, capture_output=True, text=True)
    (out / f"{name}.stdout").write_text(result.stdout)
    (out / f"{name}.stderr").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"{name}:\n{result.stderr}\n{result.stdout}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / "wasm/_build/motoko")
    parser.add_argument("--candidate", type=Path, default=ROOT / "wasm/_build/motoko-minimal")
    parser.add_argument("--core", type=Path, default=ROOT.parent / "motoko-core/src")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--passes", type=int, default=3)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    assert args.passes > 0
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = out / "benchmark.mo"
    source.write_text(runtime_source("benchmark", source))
    assert "Prim.canonicalizeCandid" not in source.read_text()
    assert "CandidX.canonicalize(raw)" in source.read_text()
    source_hash = sha256(source.read_bytes()).hexdigest()
    compilers = {"baseline": args.baseline.resolve(), "candidate": args.candidate.resolve()}
    before = {name: fingerprints(path) for name, path in compilers.items()}
    report = {
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "status": "prepared" if args.prepare_only else "running",
        "method": "Same source and native CandidX control; sequential passes; 7 trials per phase; median wall time, Wasm fuel and allocation; fuel is not IC cycles",
        "source": str(source), "source_sha256": source_hash,
        "source_control": "Existing native CandidX library, ordinary to_candid and ordinary from_candid",
        "platform": platform.platform(),
        "compiler_dirs": {name: str(path) for name, path in compilers.items()},
        "compiler_fingerprints": before,
        "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "generator_sha256": sha256((ROOT / "benchmarks/runtime_sources.py").read_bytes()).hexdigest(),
    }
    if not args.prepare_only:
        report["runs"] = {name: [] for name in compilers}
        for name, compiler in compilers.items():
            env = {**os.environ, "CX_MOTOKO_DIR": str(compiler)}
            artifact = out / f"{name}.wasm"
            command([ROOT / "wasm/moc", "--package", "core", args.core,
                     "-wasi-system-api", source, "-o", artifact], env, out, name + "-build")
            report.setdefault("compiler_versions", {})[name] = command(
                [ROOT / "wasm/moc", "--version"], env, out, name + "-version").strip()
            report.setdefault("artifacts_sha256", {})[name] = sha256(artifact.read_bytes()).hexdigest()
        for trial in range(args.passes):
            for name in compilers:
                result, lines = native_motoko(out / f"{name}.wasm")
                report["runs"][name].append(result)
                (out / f"{name}-{trial}.stdout").write_text("\n".join(lines) + "\n")
        comparisons = []
        for original in report["runs"]["baseline"][0]["cases"]:
            row = {"name": original["name"], "ordinary_bytes": original["ordinary_bytes"],
                   "canonical_bytes": original["canonical_bytes"]}
            phase_names = [name for name, value in original.items() if isinstance(value, dict)]
            for phase in phase_names:
                values = {}
                for name in compilers:
                    cases = [next(case for case in result["cases"] if case["name"] == row["name"])
                             for result in report["runs"][name]]
                    assert all(case["ordinary_bytes"] == row["ordinary_bytes"] and
                               case["canonical_bytes"] == row["canonical_bytes"] for case in cases)
                    values[name] = {metric: statistics.median(case[phase][metric] for case in cases)
                                    for metric in ("median_us", "median_fuel", "allocated_bytes_per_op")}
                values["candidate_over_baseline"] = {
                    metric: values["candidate"][metric] / values["baseline"][metric]
                    for metric in values["candidate"] if values["baseline"][metric] != 0}
                row[phase] = values
            comparisons.append(row)
        report["comparisons"] = comparisons
        report["status"] = "pass"
    report["sources_changed_during_run"] = (
        sha256(source.read_bytes()).hexdigest() != source_hash or
        {name: fingerprints(path) for name, path in compilers.items()} != before)
    assert not report["sources_changed_during_run"], "compiler/source changed during capture"
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
