#!/usr/bin/env python3
"""Build cx.wasm and an isolated Motoko compiler/RTS integration."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import re

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "wasm"
BASE = "9384e1076bf71c1c44b78627b9191011d8543deb"


def command(argv, cwd=ROOT, env=None):
    subprocess.run([str(item) for item in argv], cwd=cwd, env=env, check=True)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def rust_sources(directory):
    paths = sorted((directory / "src").rglob("*.rs"))
    paths += [directory / "Cargo.toml", directory / "Cargo.lock"]
    return {str(path.relative_to(directory)): digest(path) for path in paths if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--motoko-repo", type=Path, default=ROOT.parent / "motoko_candid")
    parser.add_argument("--compiler-dir", type=Path, default=HERE / "_build/motoko")
    parser.add_argument("--rust-toolchain", default="1.98.1")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--standalone-only", action="store_true")
    group.add_argument("--compiler-only", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--prepared", action="store_true",
                        help="Build an existing patched compiler checkout without Git operations")
    args = parser.parse_args()
    artifacts = HERE / "artifacts"
    artifacts.mkdir(exist_ok=True)
    manifest_path = artifacts / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update({"profile":"cx", "package_version":"1.0.0", "license":"MIT (CandidX source); see THIRD_PARTY_NOTICES.md for linked upstream artifacts",
                     "rust_source_sha256":digest(ROOT / "rust/src/lib.rs"),
                     "rust_sources_sha256":rust_sources(ROOT / "rust"),
                     "wasm_sources_sha256":rust_sources(HERE)})
    manifest.setdefault("artifacts", {})
    if not args.compiler_only:
        env = dict(os.environ)
        for variable, binary in [("RUSTC", "rustc"), ("RUSTDOC", "rustdoc")]:
            env[variable] = subprocess.check_output(
                ["rustup", "which", "--toolchain", args.rust_toolchain, binary], text=True).strip()
        env["RUSTFLAGS"] = "-C link-arg=-zstack-size=1048576 -C link-arg=--max-memory=536870912"
        command(["rustup", "run", args.rust_toolchain, "cargo", "build", "--locked",
                 "--manifest-path", HERE / "Cargo.toml", "--target", "wasm32-unknown-unknown", "--release"], env=env)
        shutil.copyfile(HERE / "target/wasm32-unknown-unknown/release/cx_wasm.wasm", artifacts / "cx.wasm")
        manifest["standalone_toolchain"] = args.rust_toolchain
    if not args.standalone_only:
        repository = args.motoko_repo.resolve()
        compiler = args.compiler_dir.resolve()
        patch = HERE / "motoko-compiler.patch"
        if args.prepared:
            source = compiler / "src/codegen/compile.ml"
            if not source.is_file() or "cx_encode_typed_heap" not in source.read_text():
                raise RuntimeError("--prepared requires an existing direct CandidX compiler checkout")
        else:
            if not compiler.exists():
                compiler.parent.mkdir(parents=True, exist_ok=True)
                command(["git", "worktree", "add", "--detach", compiler, BASE], cwd=repository)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=compiler, text=True).strip()
            if head != BASE:
                raise RuntimeError(f"Compiler worktree must be at {BASE}; found {head}")
            applied = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)],
                                     cwd=compiler, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if not applied:
                command(["git", "apply", "--check", patch], cwd=compiler)
                command(["git", "apply", patch], cwd=compiler)
        # The same crate is reused, not counted as a fifth independent codec.
        if (compiler / "rts/candidx/src").exists():
            shutil.rmtree(compiler / "rts/candidx/src")
        shutil.copytree(ROOT / "rust", compiler / "rts/candidx", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("target", "_out", "__pycache__"))
        assert rust_sources(ROOT / "rust") == rust_sources(compiler / "rts/candidx")
        # OCaml 5.4 includes Bigarray in stdlib; its removed separate archive
        # needs local findlib metadata in this environment. No source change.
        compat = HERE / "_build/ocaml-compat/bigarray"
        compat.mkdir(parents=True, exist_ok=True)
        (compat / "META").write_text('version = "5.4.1"\ndescription = "Bigarray is included in stdlib"\nrequires = ""\n')
        shell = "set -e\nexport OCAMLPATH=" + shlex.quote(str(compat.parent)) + '${OCAMLPATH:+:$OCAMLPATH}\n'
        command(["nix", "develop", repository, "--command", "bash", "-c", shell
                 + "make -C rts mo-rts.wasm mo-rts-debug.wasm WASM_LD=wasm-ld\n"
                 + "make -C src exes\ndune build --root src --profile=release\n"], cwd=compiler)
        # Production JS builds embed the RTS (as the upstream Nix build does).
        # Its virtual filesystem cannot load a physical RTS pathname. Restore
        # the development loader afterward; no generated byte strings enter
        # the integration patch or its source fingerprints.
        rts_loader = compiler / "src/rts/rts.ml"
        development_loader = rts_loader.read_bytes()
        try:
            js_shell = shell + "src/rts/gen.sh " + shlex.quote(str(compiler / "rts")) + "\n"
            js_shell += "dune build --root src --profile=release js/moc_js.bc.js js/moc_interpreter.bc.js\n"
            command(["nix", "develop", repository, "--command", "bash", "-c", js_shell], cwd=compiler)
            manifest["compiler_js_embedded_rts_source_sha256"] = digest(rts_loader)
        finally:
            rts_loader.write_bytes(development_loader)
        if not args.skip_tests:
            test_shell = shell + "test-runner -b -f 'cx-candid|cx-direct|idl-ops|idl-decoding-bug|leb-random' test/run test/fail test/trap\n"
            test_shell += "CX_MOTOKO_DIR=" + shlex.quote(str(compiler)) + " node " + shlex.quote(str(HERE / "test_compiler_js.cjs")) + "\n"
            command(["nix", "develop", repository, "--command", "bash", "-c", test_shell], cwd=compiler)
        for name in ["mo-rts.wasm", "mo-rts-debug.wasm"]:
            shutil.copyfile(compiler / "rts" / name, artifacts / name)
        manifest["motoko_base_commit"] = BASE
        manifest["compiler_worktree"] = str(compiler)
        manifest["compiler_patch_sha256"] = digest(patch)
        changed = re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), flags=re.MULTILINE)
        manifest["compiler_sources_sha256"] = {
            path: digest(compiler / path) for path in sorted(set(changed)) if (compiler / path).is_file()}
        built_paths = ["src/_build/default/exes/moc.exe",
                       "src/_build/default/js/moc_js.bc.js",
                       "src/_build/default/js/moc_interpreter.bc.js"]
        manifest["compiler_build_artifacts"] = {
            path: {"sha256":digest(compiler / path), "bytes":(compiler / path).stat().st_size}
            for path in built_paths}
        manifest["compiler_typed_operator"] = "to_candid_canonical"
        manifest["compiler_inbound_primitive"] = "Prim.canonicalizeCandid"
    for name in ["cx.wasm", "mo-rts.wasm", "mo-rts-debug.wasm"]:
        path = artifacts / name
        if path.exists():
            manifest["artifacts"][name] = {"sha256":digest(path), "bytes":path.stat().st_size}
    (artifacts / "manifest.json").write_text(json.dumps(manifest, indent=2) + '\n')
    print(artifacts / "manifest.json")


if __name__ == "__main__":
    main()
