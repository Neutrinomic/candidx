#!/usr/bin/env python3
"""Measure standalone Wasm scratch and retained linear-memory capacity."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'wasm'))
from runtime import Canonicalizer, TypedEncoder


def block_tree(name):
    """Prepare the same native block as the language benchmarks, without DIDL."""
    count, memo_size = {
        'small': (4, 32), 'records-128': (128, 32),
        'blob-64k': (1, 65536), 'records-1024': (1024, 0),
    }[name]

    def scalar(kind, value):
        return {'kind': kind, 'value': value}

    def option(value):
        return scalar('opt', value)

    def record(fields):
        result = []
        for label, value in fields:
            number = 0
            for byte in label.encode('utf-8'):
                number = (223 * number + byte) & 0xffffffff
            result.append({'id': number, 'value': value,
                           'optional': value['kind'] == 'opt'})
        return {'kind': 'record', 'fields': result}

    memo = bytes(i % 256 for i in range(memo_size))
    transactions = [record([
        ('owner', scalar('principal', b'\1\2')),
        ('amount', scalar('nat', (1 << 200) + i)),
        ('memo', option(scalar('blob', memo) if i % 3 == 0 or memo_size == 65536 else None)),
    ]) for i in range(count)]
    return record([
        ('timestamp', scalar('nat64', 1234567890)),
        ('parentHash', option(scalar('blob', bytes([7]) * 32))),
        ('tx', {'kind': 'vec', 'values': transactions}),
        ('note', option(None)),
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path, help='Rust benchmark JSON retaining raw_hex/canonical_hex')
    parser.add_argument('--report', type=Path, default=ROOT / 'benchmarks/results-wasm-memory.json')
    parser.add_argument('--typed', action='store_true', help='Measure direct typed-memory input instead of inbound DIDL normalization')
    args = parser.parse_args()
    rows = []
    for case in json.loads(args.fixture.read_text())['cases']:
        codec = TypedEncoder() if args.typed else Canonicalizer()
        raw = bytes.fromhex(case['raw_hex'])
        expected = bytes.fromhex(case['canonical_hex'])
        trees = [block_tree(case['name'])] if args.typed else None
        operation = (lambda: codec.encode_args(trees)) if args.typed else (lambda: codec.canonicalize(raw))
        assert operation() == expected
        capacity, scratch = codec.memory_bytes, codec.scratch_bytes
        for _ in range(100):
            assert operation() == expected
            assert codec.memory_bytes == capacity
            assert codec.scratch_bytes == scratch
        rows.append({'name': case['name'], 'ordinary_message_bytes_for_comparison': len(raw), 'output_bytes': len(expected),
                     'scratch_bytes_including_input': scratch, 'linear_memory_capacity_bytes': capacity,
                     'reused_calls': 100})
    report = {'profile': 'cx', 'recorded_utc': datetime.now(timezone.utc).isoformat(),
        'phase': 'canonical_encode_direct_typed_abi' if args.typed else 'canonicalize_preencoded',
        'method': 'Fresh standalone memory32 instance per workload; arena high-water includes input and temporary allocations until reset; linear memory capacity is retained, not live heap or process RSS',
        'input': 'Typed ABI nodes and native integer magnitudes; byte vectors use compact Blob spans; host preparation/marshalling allocations excluded; no ordinary message is encoded or decoded' if args.typed else 'Copied ordinary DIDL bytes',
        'wasmtime': importlib.metadata.version('wasmtime'),
        'artifact_sha256': sha256((ROOT / 'wasm/artifacts/cx.wasm').read_bytes()).hexdigest(),
        'fixture_sha256': sha256(args.fixture.read_bytes()).hexdigest(),
        'source_fingerprints': {path: sha256((ROOT / path).read_bytes()).hexdigest()
                                for path in ['benchmarks/wasm_memory.py', 'wasm/runtime.py', 'wasm/src/lib.rs', 'rust/src/tree.rs']},
        'rows': rows}
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
