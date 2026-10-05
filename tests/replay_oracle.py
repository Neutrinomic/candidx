#!/usr/bin/env python3
"""Independently check saved shared-fuzz messages and their complete data."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from reference import canonicalize, values
from direct import expected_bytes, normalized_values


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directories", nargs="+", type=Path,
                   help="fuzz output directories containing records.json")
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    runs = []
    for directory in args.directories:
        checked = 0
        entries = json.loads((directory / "records.json").read_text())
        for entry in entries:
            expected = bytes.fromhex(entry["canonical"])
            assert sha256(expected).hexdigest() == entry["application_sha256"], entry["id"]
            actual_data = values(expected)
            for raw in [*entry["raw"].values(), *entry.get("direct", {}).values(), entry["canonical"]]:
                message = bytes.fromhex(raw)
                assert canonicalize(message) == expected, entry["id"]
                assert values(message) == actual_data, entry["id"]
                checked += 1
        source_cases = {case["id"]: case for case in json.loads((directory / "corpus.json").read_text())["cases"]}
        for entry in entries:
            assert expected_bytes(source_cases[entry["id"]]).hex() == entry["canonical"], entry["id"]
            assert normalized_values(source_cases[entry["id"]]) == values(bytes.fromhex(entry["canonical"])), entry["id"]
        direct_path = directory / "direct-records.json"
        direct_cases = 0
        if direct_path.exists():
            directed = {case["id"]: case for case in json.loads((directory / "direct-corpus.json").read_text())["cases"]}
            for entry in json.loads(direct_path.read_text()):
                expected = expected_bytes(directed[entry["id"]])
                assert expected.hex() == entry["canonical"], entry["id"]
                assert sha256(expected).hexdigest() == entry["application_sha256"], entry["id"]
                for raw in [*entry["raw"].values(), *entry["direct"].values(), entry["canonical"]]:
                    message = bytes.fromhex(raw)
                    assert canonicalize(message) == expected, entry["id"]
                    assert values(message) == normalized_values(directed[entry["id"]]), entry["id"]
                    checked += 1
                direct_cases += 1
        for entry in json.loads((directory / "float32-edges.json").read_text()):
            raw, expected = bytes.fromhex(entry["raw"]), bytes.fromhex(entry["canonical"])
            assert canonicalize(raw) == expected, entry["id"]
            assert values(raw) == values(expected), entry["id"]
            checked += 1
        runs.append({"directory": str(directory), "typed_records": len(entries),
                     "directed_typed_cases": direct_cases,
                     "messages": checked, "status": "pass",
                     "records_sha256": sha256((directory / "records.json").read_bytes()).hexdigest()})
        print(f"oracle/data preservation passed {directory}: {checked} messages", flush=True)
    report = {"status": "pass", "normalizer": "independent Python research model",
              "checks": "source-value expected bytes, canonical bytes, idempotence, complete normalized data, external hashes",
              "runs": runs, "messages": sum(r["messages"] for r in runs)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
