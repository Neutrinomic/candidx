#!/usr/bin/env python3
"""Build and differentially verify CandidX locally. Never publishes anything."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from cases import perturbed_vectors
from reference import canonicalize

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_out"


def command(args, name, cwd=ROOT, env=None, input_text=None):
    result = subprocess.run([str(x) for x in args], cwd=cwd, env=env,
                            input=input_text, text=True, capture_output=True)
    (OUT / f"{name}.stdout").write_text(result.stdout)
    (OUT / f"{name}.stderr").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")
    return result.stdout


def wasm(path, name, repetitions=1):
    import wasmtime
    config = wasmtime.Config()
    config.wasm_memory64 = True
    config.wasm_threads = True
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, str(path))
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    outputs = []
    for i in range(repetitions):
        store = wasmtime.Store(engine)
        wasi = wasmtime.WasiConfig()
        stdout, stderr = OUT / f"{name}-{i}.stdout", OUT / f"{name}-{i}.stderr"
        wasi.stdout_file, wasi.stderr_file = str(stdout), str(stderr)
        store.set_wasi(wasi)
        instance = linker.instantiate(store, module)
        try:
            instance.exports(store)["_start"](store)
        except wasmtime.ExitTrap as error:
            if error.code:
                raise
        outputs.append(stdout.read_text())
        if stderr.read_text():
            raise RuntimeError(stderr.read_text())
    if len(set(outputs)) != 1:
        raise AssertionError("native Motoko changed across fresh Wasm instances")
    return outputs[0]


def parse_probe(text, motoko=False):
    result, chunks = {}, {}
    for line in text.splitlines():
        name, raw = line.split(" ", 1)
        if motoko:
            name, chunk = name.rsplit(":", 1)
            assert raw.startswith('"') and raw.endswith('"')
            assert int(chunk) == chunks.get(name, 0)
            chunks[name] = int(chunk) + 1
            result[name] = result.get(name, b"") + bytes(int(x, 16) for x in re.findall(r"\\([a-fA-F0-9]{2})", raw))
        else:
            result[name] = bytes.fromhex(raw)
    return result


def mo_blob(hex_string):
    return '"' + ''.join(f"\\{b:02x}" for b in bytes.fromhex(hex_string)) + '"'


def motoko_corpus(cases):
    import_path = os.path.relpath(ROOT / "motoko/src/lib", OUT)
    lines = ['import Prim "mo:⛔";',
             f'import CandidX "{import_path}";']
    for i, case in enumerate(cases):
        literal = mo_blob(case["raw"])
        if case.get("error"):
            lines.append(f'switch (CandidX.canonicalize({literal})) {{ case (#err(_)) {{}}; case (#ok(_)) Prim.trap("accepted invalid case {i}: {case["id"]}"); }};')
        else:
            expected = mo_blob(case["canonical"])
            lines.append(f'if (CandidX.canonicalize({literal}) != #ok({expected})) Prim.trap("bytes differ at case {i}: {case["id"]}");')
    lines.append(f'Prim.debugPrint("native-motoko-corpus-passed {len(cases)}");')
    path = OUT / "corpus.mo"
    path.write_text('\n'.join(lines) + '\n')
    return path


def verify_cli(args, cases, name):
    input_text = ''.join(c["raw"] + '\n' for c in cases)
    lines = command(args, name, input_text=input_text).splitlines()
    assert len(lines) == len(cases), (name, len(lines), len(cases))
    for line, case in zip(lines, cases):
        if case.get("error"):
            assert line.startswith("error "), (name, case["id"], line)
        else:
            assert line == "ok " + case["canonical"], (name, case["id"], line)


def source_fingerprints():
    fingerprints = {}
    generated_dirs = {"_out", "target", "node_modules", "dist", "_build", "__pycache__", ".venv", ".mops"}
    for folder in ("spec", "ts/src", "ts/test", "rust/src", "rust/tests", "rust/examples", "ocaml", "motoko", "tests"):
        for path in sorted((ROOT / folder).rglob("*")):
            generated = (any(part in generated_dirs for part in path.parts)
                         or path.suffix in (".install", ".pyc", ".pyo")
                         or path.name == ".DS_Store" or path.name.endswith("~"))
            if path.is_file() and path.name != "collect_initial.py" and not generated:
                fingerprints[str(path.relative_to(ROOT))] = sha256(path.read_bytes()).hexdigest()
    for path in (ROOT / "rust/Cargo.lock", ROOT / "ts/package-lock.json"):
        fingerprints[str(path.relative_to(ROOT))] = sha256(path.read_bytes()).hexdigest()
    for path in sorted((ROOT / "wasm").glob("src/*.*")) + [ROOT / "wasm/runtime.py", ROOT / "wasm/artifacts/cx.wasm", ROOT / "rust/Cargo.toml",
            ROOT / "wasm/motoko-compiler.patch", ROOT / "wasm/moc", ROOT / "wasm/artifacts/mo-rts.wasm", ROOT / "wasm/artifacts/mo-rts-debug.wasm",
            ROOT / "wasm/artifacts/manifest.json", ROOT / "wasm/test_architecture.py"]:
        if path.is_file():
            fingerprints[str(path.relative_to(ROOT))] = sha256(path.read_bytes()).hexdigest()
    return fingerprints


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--moc", type=Path, default=ROOT.parent / "motoko_candid/bin/moc")
    parser.add_argument("--core", type=Path, default=ROOT.parent / "motoko-core/src")
    parser.add_argument("--rust-toolchain", default="1.98.1")
    parser.add_argument("--motoko-repo", type=Path, default=ROOT.parent / "motoko_candid")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--wasm-artifact", type=Path, default=ROOT / "wasm/artifacts/cx.wasm")
    parser.add_argument("--skip-wasm", action="store_true")
    parser.add_argument("--runtime-moc", type=Path,
                        help="also exercise direct to_candid_canonical with this compiler")
    parser.add_argument("--out", type=Path, default=OUT,
                        help="directory for logs, generated programs, and direct corpus captures")
    parser.add_argument("--report", type=Path,
                        help="JSON report path (defaults to --out/results.json)")
    args = parser.parse_args()
    OUT = args.out.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    args.report = args.report or OUT / "results.json"
    starting_sources = source_fingerprints()
    corpus = json.loads((ROOT / "tests/vectors.json").read_text())
    assert (ROOT / "rust/tests/vectors.json").read_bytes() == (ROOT / "tests/vectors.json").read_bytes(), "Rust package fixture differs from the shared corpus"
    rustc = command(["rustup", "which", "--toolchain", args.rust_toolchain, "rustc"], "rustc-path").strip()
    rustdoc = command(["rustup", "which", "--toolchain", args.rust_toolchain, "rustdoc"], "rustdoc-path").strip()
    rust_env = {**os.environ, "RUSTC": rustc, "RUSTDOC": rustdoc}
    cargo = ["rustup", "run", args.rust_toolchain, "cargo"]
    manifest = ROOT / "rust/Cargo.toml"
    if not args.skip_build:
        command(cargo + ["test", "--locked", "--all-features", "--manifest-path", manifest], "rust-tests", env=rust_env)
        command(cargo + ["test", "--locked", "--no-default-features", "--manifest-path", manifest], "rust-standalone-tests", env=rust_env)
        command(cargo + ["clippy", "--locked", "--all-targets", "--all-features", "--manifest-path", manifest, "--", "-D", "warnings"], "rust-clippy", env=rust_env)
        command(cargo + ["build", "--locked", "--features", "fuzz", "--examples", "--manifest-path", manifest], "rust-build", env=rust_env)
        command(["npm", "test"], "typescript-tests", cwd=ROOT / "ts")
        if shutil.which("dune") and shutil.which("ocamlc"):
            command(["dune", "build"], "ocaml-build", cwd=ROOT / "ocaml")
            command(["dune", "runtest", "--force"], "ocaml-tests", cwd=ROOT / "ocaml")
        else:
            command(["nix", "develop", "--command", "dune", "build", "--root", ROOT / "ocaml"], "ocaml-build", cwd=args.motoko_repo)
            command(["nix", "develop", "--command", "dune", "runtest", "--force", "--root", ROOT / "ocaml"], "ocaml-tests", cwd=args.motoko_repo)
    command([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], "oracle-tests")
    # Import after defining helpers: fuzz imports this module for its subprocess
    # and Wasm runner, while the direct corpus derives expected bytes only from
    # explicit source witness/value pairs.
    import fuzz
    fuzz.verify.OUT = OUT
    direct_typed = fuzz.run_direct_corpus(OUT, name="direct-conformance", compiler=args.runtime_moc,
                                        core=args.core, repetitions=3,
                                        wasm_artifact=args.wasm_artifact if not args.skip_wasm else None)
    import roundtrip_guard
    roundtrip_comparisons = roundtrip_guard.run(OUT, compiler=args.moc, core=args.core)
    import direct_guard
    direct_guard.verify.OUT = OUT
    guards = direct_guard.run(OUT, args.rust_toolchain, args.motoko_repo)
    compiler_architecture = None
    if args.runtime_moc and args.runtime_moc.resolve() == (ROOT / "wasm/moc").resolve():
        architecture_report = OUT / "compiler-architecture.json"
        command([sys.executable, ROOT / "wasm/test_architecture.py", "--report", architecture_report],
                "compiler-architecture")
        compiler_architecture = json.loads(architecture_report.read_text())
    moc = [args.moc, "--package", "core", args.core, "-wasi-system-api"]
    command(moc + ["-o", OUT / "evolution.wasm", ROOT / "motoko/test/evolution.mo"], "motoko-evolution-build")
    sources = {
        "motoko": parse_probe(wasm(OUT / "evolution.wasm", "motoko-evolution", 3), True),
        "rust": parse_probe(command([ROOT / "rust/target/debug/examples/evolution"], "rust-evolution")),
        "ts": parse_probe(command(["node", ROOT / "ts/test/evolution.mjs"], "ts-evolution")),
    }
    assert len({tuple(sorted(s)) for s in sources.values()}) == 1
    lookup = {e["id"]: e for e in corpus["vectors"]}
    for lang, output in sources.items():
        for key, data in output.items():
            prefix, name = key.split(":", 1)
            expected = lookup[name]
            target = expected["raw"][lang] if prefix == "raw" else expected["canonical"]
            assert data.hex() == target, (lang, name, prefix)
    for group in corpus["equal_groups"]:
        assert len({lookup[k]["canonical"] for k in group}) == 1
        assert len({lookup[k]["application_sha256"] for k in group}) == 1
    for a, b in corpus["distinct_pairs"]:
        assert lookup[a]["canonical"] != lookup[b]["canonical"]

    cases = []
    for entry in corpus["vectors"]:
        for language, raw in entry["raw"].items():
            cases.append({"id": f'{entry["id"]}-{language}', "raw": raw, "canonical": entry["canonical"]})
        cases.append({"id": entry["id"] + "-idempotent", "raw": entry["canonical"], "canonical": entry["canonical"]})
        assert sha256(bytes.fromhex(entry["canonical"])).hexdigest() == entry["application_sha256"]
    cases += corpus["positive_edges"] + perturbed_vectors(corpus["vectors"]) + corpus["negative_cases"]
    for case in cases:
        if case.get("error"):
            try:
                canonicalize(bytes.fromhex(case["raw"]))
            except (ValueError, UnicodeError):
                pass
            else:
                raise AssertionError(case["id"])
        else:
            assert canonicalize(bytes.fromhex(case["raw"])).hex() == case["canonical"], case["id"]
    verify_cli([ROOT / "rust/target/debug/examples/probe"], cases, "rust-differential")
    verify_cli(["node", ROOT / "ts/test/probe.mjs"], cases, "ts-differential")
    verify_cli([ROOT / "ocaml/_build/default/probe.exe"], cases, "ocaml-differential")
    wasm_backend = None
    if not args.skip_wasm and args.wasm_artifact.exists():
        sys.path.insert(0, str(ROOT))
        from wasm.runtime import Canonicalizer
        runtime = Canonicalizer(args.wasm_artifact)
        for case in cases:
            try:
                actual = runtime.canonicalize(bytes.fromhex(case["raw"]))
            except ValueError:
                assert case.get("error"), ("Rust/Wasm", case["id"])
            else:
                assert not case.get("error"), ("Rust/Wasm", "accepted invalid input", case["id"])
                assert actual.hex() == case["canonical"], ("Rust/Wasm", case["id"])
        wasm_backend = {"status": "pass", "messages": len(cases), "shared_codec": "Rust",
                        "artifact_sha256": sha256(args.wasm_artifact.read_bytes()).hexdigest()}
    source = motoko_corpus(cases)
    command(moc + ["-o", OUT / "corpus.wasm", source], "motoko-corpus-build")
    assert f"native-motoko-corpus-passed {len(cases)}" in wasm(OUT / "corpus.wasm", "motoko-corpus", 3)
    optimization_regressions = None
    optimization_source = ROOT / "motoko/test/optimization.mo"
    if optimization_source.exists():
        command(moc + ["-o", OUT / "optimization.wasm", optimization_source], "motoko-optimization-build")
        output = wasm(OUT / "optimization.wasm", "motoko-optimization", 3)
        assert "optimization-regressions-passed" in output, output
        optimization_regressions = output.strip()
    fingerprints = source_fingerprints()
    report = {"status": "pass", "profile": corpus["profile"], "completed_utc": datetime.now(timezone.utc).isoformat(),
              "typed_cases": len(corpus["vectors"]), "typed_evolution_cases": 27,
              "differential_cases_per_implementation": len(cases),
              "type_table_perturbations": len(perturbed_vectors(corpus["vectors"])),
              "malformed_unsupported_or_limited_cases": len(corpus["negative_cases"]),
              "identity_groups": len(corpus["equal_groups"]), "distinct_data_pairs": len(corpus["distinct_pairs"]),
              "implementations": ["Rust", "TypeScript", "OCaml", "native Motoko"],
              "oracle": "independent Python research normalizer",
              "motoko_fresh_instances_per_program": 3,
              "moc": command([args.moc, "--version"], "moc-version").strip(),
              "rustc": command([rustc, "--version"], "rustc-version").strip(),
              "node": command(["node", "--version"], "node-version").strip(),
              "wasmtime": importlib.metadata.version("wasmtime"),
              "vectors_sha256": sha256((ROOT / "tests/vectors.json").read_bytes()).hexdigest(),
              "source_sha256": starting_sources,
              "sources_changed_during_run": fingerprints != starting_sources,
              "wasm_backend": wasm_backend,
              "direct_typed_encoders": direct_typed,
              "roundtrip_comparison_guards": roundtrip_comparisons,
              "direct_path_guards": guards,
              "compiler_architecture": compiler_architecture,
              "native_motoko_adapter": "temporary ordinary to_candid plus canonicalize fallback",
              "motoko_optimization_regressions": optimization_regressions,
              "limits": "fixed cx contract; recorded cases and toolchain versions; no publication"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != "source_sha256"}, indent=2))


if __name__ == "__main__":
    main()
