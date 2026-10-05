#!/usr/bin/env python3
"""Guard the typed compiler/RTS call chain against encode-then-normalize."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def section(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin + len(start))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler-dir", type=Path, default=ROOT / "wasm/_build/motoko")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    paths = {
        "codegen": args.compiler_dir / "src/codegen/compile.ml",
        "rts": args.compiler_dir / "rts/motoko-rts/src/candidx.rs",
        "visitor": ROOT / "rust/src/visitor.rs",
        "standalone": ROOT / "wasm/src/lib.rs",
    }
    sources = {name: path.read_text() for name, path in paths.items()}
    checks = []

    def check(name, condition):
        assert condition, name
        checks.append(name)

    codegen = sources["codegen"]
    primitive = section(codegen, "| SerializeCanonicalPrim ts, [e] ->", "| SerializePrim ts, [e] ->")
    helper = section(codegen, "let serialize_canonical env ts", "let serialize env ts")
    check("dedicated typed IR branch retains native values", "compile_exp_vanilla" in primitive)
    check("typed IR branch calls direct helper", "Serialization.serialize_canonical env ts" in primitive)
    check("typed IR branch bypasses ordinary serializer", not re.search(r"Serialization\.serialize\b", primitive))
    check("direct helper passes source metadata to heap encoder", "canonical_heap_descriptor env ts" in helper and '"cx_encode_typed_heap"' in helper)
    check("direct helper has no ordinary serializer call", not re.search(r"\bserialize\s+env\b", helper))
    descriptor = section(codegen, "let canonical_heap_descriptor env ts", "let serialize_canonical env ts")
    check("source metadata uses CXTY rather than DIDL", '"CXTY"' in descriptor and '"DIDL"' not in descriptor)

    rts = sources["rts"]
    entry = section(rts, "unsafe fn cx_encode_typed_heap", "unsafe fn cx_canonicalize_blob")
    check("RTS entry derives and emits from native source handles", "SourceValue" in entry and "::candidx::encode_source_args(&source, &values)" in entry)
    check("RTS entry parses only source metadata", "metadata(blob_bytes(schema))" in entry)
    check("RTS entry does not invoke inbound parser or typed-tree builder", not re.search(r"\b(canonicalize|encode_direct_args|read_value|parse)\s*\(", entry))
    provider = section(rts, "struct SourceValue", "unsafe fn cx_encode_typed_heap")
    check("native adapter implements source visitor", "impl ::candidx::DirectSource for HeapSource" in provider and "raw: Value" in provider)
    check("native adapter does not construct compound payload nodes", not re.search(r"DirectValue::(Option|Vec|Record|Variant)\b", provider))

    visitor = sources["visitor"]
    first = section(visitor, "fn derive_into", "fn validate_scalar")
    emission = section(visitor, "fn emit_into", "pub fn encode_source_args")
    check("shape visit does not read scalar payloads", "source.scalar(" not in first and "source.binary_slice(" not in first)
    check("emission reads scalar payloads directly", "source.scalar(value)?" in emission and "source.binary_slice(value)?" in emission)
    check("source visitor does not invoke inbound parser", not re.search(r"\b(canonicalize|read_value|parse)\s*\(", visitor))

    standalone = sources["standalone"]
    typed = section(standalone, 'pub unsafe extern "C" fn cx_encode_typed(', 'pub unsafe extern "C" fn cx_result_ptr')
    check("standalone typed ABI directly encodes typed nodes", "encode_direct_args" in typed)
    check("standalone typed ABI bypasses inbound normalizer", "canonicalize(" not in typed)
    report = {
        "profile": "cx", "passed": len(checks), "checks": checks,
        "source_sha256": {name: sha256(path.read_bytes()).hexdigest() for name, path in paths.items()},
        "scope": "Source call-chain guards complement golden byte, decoder, disabled-parser SDK, and compiler runtime tests.",
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
