#!/usr/bin/env python3
"""Exercise the standalone Wasm ABI against shared bytes and focused ABI bounds."""
import argparse
import json
from pathlib import Path
from runtime import Canonicalizer
import wasmtime

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", action="append", type=Path, default=[])
    parser.add_argument("--resource-stress", action="store_true")
    args = parser.parse_args()
    codec = Canonicalizer()
    shared = json.loads((ROOT / "tests/vectors.json").read_text())
    positives = list(shared["vectors"]) + list(shared["positive_edges"])
    for path in args.corpus:
        positives.extend(json.loads(path.read_text()))
    messages = 0
    for case in positives:
        expected = bytes.fromhex(case["canonical"])
        raws = case["raw"]
        for raw in raws.values() if isinstance(raws, dict) else [raws]:
            assert codec.canonicalize(bytes.fromhex(raw)) == expected, case["id"]
            assert codec.canonicalize(expected) == expected, case["id"] + " idempotence"
            messages += 1
    for case in shared["negative_cases"]:
        try:
            codec.canonicalize(bytes.fromhex(case["raw"]))
        except ValueError as failure:
            assert str(failure), case["id"]
        else:
            raise AssertionError("Accepted " + case["id"])
        assert codec.canonicalize(b"DIDL\0\0") == b"DIDL\0\0"

    exports, store = codec.exports, codec.store
    exports["cx_reset"](store)
    assert exports["cx_result_len"](store) == 0
    assert exports["cx_alloc"](store, 32 * 1024 * 1024 + 1) == 0
    assert exports["cx_canonicalize"](store, 0, 6) == 1
    assert exports["cx_canonicalize"](store, 0, 32 * 1024 * 1024 + 1) == 1
    pointer = exports["cx_alloc"](store, 6)
    assert exports["cx_canonicalize"](store, pointer, 7) == 1
    assert exports["cx_canonicalize"](store, 0xfffffff0, 100) == 1

    # Arena reset reuses capacity; retained pages must plateau on equal inputs.
    blob = next(case for case in shared["vectors"] if case["id"] == "blob-130")
    raw = bytes.fromhex(blob["raw"]["rust"])
    expected = bytes.fromhex(blob["canonical"])
    codec.canonicalize(raw)
    pages = codec.memory_bytes
    for _ in range(1000):
        assert codec.canonicalize(raw) == expected
        assert codec.memory_bytes == pages
    if args.resource_stress:
        # Fill the bounded arena through its host ABI. Memory32 objects are
        # smaller than their RTS counterparts; equal input need not use equal
        # scratch bytes, so exercise this bound directly and deterministically.
        limited = Canonicalizer()
        exports, store = limited.exports, limited.store
        exports["cx_reset"](store)
        for _ in range(8):
            assert exports["cx_alloc"](store, 32 * 1024 * 1024) != 0
        try:
            exports["cx_alloc"](store, 1)
        except wasmtime.Trap:
            assert store.get_fuel() > 0
            assert exports["cx_result_len"](store) == 0
        else:
            raise AssertionError("Scratch limit did not trap")
        assert limited.canonicalize(b"DIDL\0\0") == b"DIDL\0\0"
    print(json.dumps({"positive_messages": messages, "idempotence_checks": messages,
        "negative_cases": len(shared["negative_cases"]), "arena_reuse_calls":1000,
        "module_imports":len(codec.module.imports), "memory_bytes":pages,
        "scratch_limit_trap_and_reset":args.resource_stress}))


if __name__ == "__main__":
    main()
