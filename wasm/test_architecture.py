#!/usr/bin/env python3
"""Guard generated direct encoding and unchanged ordinary compiler routines."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BASE = "9384e1076bf71c1c44b78627b9191011d8543deb"
ABI = ("new", "visit", "begin", "field", "end", "unary", "join", "root",
       "root_type", "types_size", "write_types", "child", "field_type",
       "case_index", "done")


def section(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin + len(start))]


def definition(source, name, module=None):
    if module:
        source = section(source, f"module {module} = struct", "\nmodule ")
    pattern = rf"^  let (?:rec )?{re.escape(name)}\b"
    match = re.search(pattern, source, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing definition {module or 'Serialization'}.{name}")
    following = re.search(r"^  let (?:rec )?\w+\b", source[match.end():], re.MULTILINE)
    end = len(source) if following is None else match.end() + following.start()
    return source[match.start():end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler-dir", type=Path,
                        default=Path(os.environ.get("CX_MOTOKO_DIR", ROOT / "wasm/_build/motoko-minimal")))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    compiler = args.compiler_dir.resolve()
    paths = {
        "codegen": compiler / "src/codegen/compile.ml",
        "shape_header_rts": compiler / "rts/motoko-rts/src/candid_canonical.rs",
        "prim": compiler / "src/prelude/prim.mo",
        "flags": compiler / "src/mo_config/flags.ml",
    }
    sources = {name: path.read_text() for name, path in paths.items()}
    baseline = subprocess.check_output(["git", "show", f"{BASE}:src/codegen/compile.ml"],
                                       cwd=compiler, text=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=compiler, text=True).strip()
    checks = []

    def check(name, condition):
        assert condition, name
        checks.append(name)

    check("compiler checkout has the pinned upstream HEAD", head == BASE)
    codegen = sources["codegen"]
    ordinary = {}
    for module, name in [("Serialization", "serialize"), ("Serialization", "buffer_size"),
                         ("Serialization", "serialize_go"), ("Blob", "of_ptr_size")]:
        before = definition(baseline, name, module)
        after = definition(codegen, name, module)
        check(f"ordinary {module}.{name} is byte-identical to pinned HEAD", after == before)
        ordinary[f"{module}.{name}"] = {
            "sha256": sha256(after.encode()).hexdigest(), "bytes": len(after.encode())}

    primitive = section(codegen, "| SerializeCanonicalPrim ts, [e] ->", "| SerializePrim ts, [e] ->")
    shape = definition(codegen, "canonical_shape", "Serialization")
    payload = definition(codegen, "canonical_payload", "Serialization")
    direct = definition(codegen, "serialize_canonical", "Serialization")
    check("dedicated IR branch compiles the native typed value", "compile_exp_vanilla" in primitive)
    check("dedicated IR branch calls generated direct encoder", "Serialization.serialize_canonical env ts" in primitive)
    check("direct branch has no ordinary serializer or output copy", not re.search(
        r"Serialization\.serialize\b|Blob\.of_ptr_size\b|Heap\.memcpy\b", primitive))
    check("shape traversal is specialized to the concrete source type",
          "Func.share_code" in shape and "normalize t" in shape and
          "canonical_shape env typ" in shape)
    check("shape traversal visits native fields, options, variants and arrays", all(
        token in shape for token in ("Object.load_idx_raw", "Opt.project", "Variant.project", "Arr.unsafe_idx")))
    check("payload traversal is specialized to the source type", "canonical_payload env emit typ" in payload)
    check("same generated payload traversal measures and writes", all(
        token in direct for token in ("canonical_payload env false typ", "canonical_payload env true typ")))
    check("generated payload traversal writes native scalar payloads", all(
        token in payload for token in ("Strm.write_bignum_leb", "Strm.write_text", "Strm.write_blob", "Strm.write_byte")))
    integer_size = section(payload, "| Prim Nat | Prim Int ->", "| Prim Float32 ->")
    check("generated integer sizing bounds magnitude before ordinary scalar sizer",
          integer_size.index("fits_unsigned_bits") < integer_size.index("buffer_size env t") and
          "28_672" in integer_size)
    check("direct encoder derives shapes and checks exact output size before allocation",
          direct.index("canonical_shape env typ") < direct.index('canonical_call env "types_size"') <
          direct.index("canonical_payload env false typ") < direct.index("Blob.alloc env Tagged.B"))
    check("direct encoder allocates the final Blob once", direct.count("Blob.alloc env Tagged.B") == 1)
    check("header is written directly into final Blob payload", re.search(
        r'get_blob \^\^ Blob\.payload_ptr_unskewed env \^\^\s+canonical_call env "write_types"', direct) is not None)
    check("direct encoder writes payload after final Blob allocation", direct.index("Blob.alloc env Tagged.B") <
          direct.index("canonical_payload env true typ"))
    check("direct encoder does not create or copy a complete intermediate output", not re.search(
        r"Blob\.of_ptr_size\b|Heap\.memcpy\b|Blob\.dyn_alloc_scratch\b|Strm\.create\b|Strm\.terminate\b", direct))
    check("direct encoder bypasses ordinary encoding and inbound parsing", not re.search(
        r"\bserialize\s+env\b|\bserialize_go\s+env\b|\bdeserialize\w*\b|canonicalize\w*", shape + payload + direct))
    check("compiler has no heap descriptor or full Rust heap encoder", not re.search(
        r"CXTY|canonical_heap_descriptor|cx_encode_typed_heap|cx_canonicalize_blob", codegen))
    check("old public Prim inbound API is absent", "canonicalizeCandid" not in sources["prim"])
    check("no canonical Candid CLI mode was added", not re.search(
        r"--(?:candid-canonical|canonical-candid)|canonicalizeCandid", sources["flags"]))

    rts = sources["shape_header_rts"].split("#[cfg(test)]", 1)[0]
    for name in ABI:
        check(f"internal shape/header ABI exports candid_canonical_{name}",
              f"fn candid_canonical_{name}(" in rts and f'"candid_canonical_{name}"' in codegen)
    check("RTS helper retains only shape and header state", all(
        token in rts for token in ("struct Context", "struct Node", "struct Field", "struct Header")))
    check("RTS helper does not access native payloads or materialize a value tree", not re.search(
        r"crate::types::Value\b|crate::bigint|mp_int|blob_bytes|text_as_blob|HeapSource|SourceValue|DirectValue|DirectSource", rts))
    check("RTS helper does not depend on the standalone encoder or inbound parser", not re.search(
        r"::candidx::|cx_encode_typed_heap|CXTY|\b(?:canonicalize|encode_source_args|encode_direct_args|parse|read_value)\s*\(", rts))
    check("RTS helper has no complete output byte vector", not re.search(
        r"\bVec\s*<\s*u8\s*>|\.to_vec\s*\(|\bvec!", rts))
    check("RTS header writer writes bytes to caller's final allocation",
          "ptr as *mut u8" in rts and ".write(byte)" in rts)
    report = {
        "profile": "cx", "status": "pass", "passed": len(checks), "checks": checks,
        "public_api": "to_candid_canonical", "pinned_upstream_head": BASE,
        "ordinary_functions": ordinary,
        "source_sha256": {name: sha256(path.read_bytes()).hexdigest() for name, path in paths.items()},
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Source guards complement direct typed golden bytes, native original-value recovery, and resource traps.",
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
