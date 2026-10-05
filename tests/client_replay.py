#!/usr/bin/env python3
"""Check CanonicalBytes against actual ordinary producer messages and table permutations."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import direct
from cases import perturbed_vectors
from reference import canonicalize, values
import verify


def replay(name, cases, out):
    path = out / f'{name}-inputs.json'
    path.write_text(json.dumps({'profile': 'cx', 'decode_only': True, 'cases': cases},
                               ensure_ascii=False, indent=2) + '\n')
    output = verify.command(['node', verify.ROOT / 'ts/test/fuzz.mjs', path], name)
    rows = [json.loads(line) for line in output.splitlines()]
    assert len(rows) == len(cases), (name, 'wrong output count')
    by_id = {row['id']: row for row in rows}
    assert len(by_id) == len(cases), (name, 'duplicate output IDs')
    totals = {}
    for case in cases:
        row = by_id[case['id']]
        assert not row.get('error'), row
        expected = {producer: 2 * len(direct.arguments(case)[0]) for producer in case['decode_inputs']}
        assert row.get('client_canonical_checks') == expected, row
        assert row['roundtrips'] == {producer: 'exact' for producer in expected}, row
        for producer, count in expected.items():
            totals[producer] = totals.get(producer, 0) + count
    return {'status': 'pass', 'cases': len(cases), 'captures_by_producer': dict(sorted(totals.items())),
            'capture_checks': sum(totals.values()), 'inputs_sha256': sha256(path.read_bytes()).hexdigest(),
            'output_sha256': sha256(output.encode()).hexdigest()}


def producer_inputs(directory, corpus_name, records_name):
    cases = json.loads((directory / corpus_name).read_text())['cases']
    records = json.loads((directory / records_name).read_text())
    by_id = {row['id']: row for row in records}
    assert len(records) == len(cases) == len(by_id), directory
    result = []
    for case in cases:
        row = by_id[case['id']]
        expected = direct.expected_bytes(case)
        assert row['canonical'] == expected.hex(), (directory, case['id'])
        assert row['application_sha256'] == sha256(expected).hexdigest(), (directory, case['id'])
        inputs = {}
        for producer, raw in row['raw'].items():
            data = bytes.fromhex(raw)
            assert canonicalize(data) == expected, (directory, case['id'], producer)
            assert values(data) == direct.normalized_values(case), (directory, case['id'], producer)
            inputs[producer] = raw
        assert inputs, (directory, case['id'], 'no ordinary producer messages')
        result.append({**case, 'decode_inputs': inputs})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fuzz_directories', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, default=verify.ROOT / '_out/client-inbound')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    verify.OUT = out
    starting_sources = verify.source_fingerprints()
    # Saved fuzz snapshots include the then-current tests. Later regressions
    # may differ, but all codecs, artifacts, spec, and source witnesses must match.
    codec_sources = {path: digest for path, digest in starting_sources.items()
                     if not path.startswith(('tests/', 'ts/test/'))}
    producers = []
    for index, directory in enumerate(args.fuzz_directories):
        report = json.loads((directory / 'report.json').read_text())
        assert report['status'] == 'pass' and not report['sources_changed_during_run'], directory
        assert all(report['source_sha256'].get(path) == digest for path, digest in codec_sources.items()), directory
        corpus = producer_inputs(directory, 'corpus.json', 'records.json')
        fixed = producer_inputs(directory, 'direct-corpus.json', 'direct-records.json')
        producers.append({'seed': report['seed_hex'],
            'generated': replay(f'ordinary-{index}', corpus, out),
            'directed': replay(f'directed-{index}', fixed, out),
            'source_report_sha256': sha256((directory / 'report.json').read_bytes()).hexdigest()})
    vectors = json.loads((verify.ROOT / 'tests/vectors.json').read_text())['vectors']
    originals = {case['id']: case for case in direct.frozen_cases()}
    permutations = []
    for wire in perturbed_vectors(vectors):
        original_id = wire['id'].removeprefix('table-permutation-').rsplit('-', 1)[0]
        case = originals[original_id]
        expected = direct.expected_bytes(case)
        assert wire['canonical'] == expected.hex() and canonicalize(bytes.fromhex(wire['raw'])) == expected, wire['id']
        permutations.append({**case, 'id': wire['id'], 'decode_inputs': {'permuted_table': wire['raw']}})
    permutation_report = replay('wire-permutations', permutations, out)
    result = {'status': 'pass', 'profile': 'cx', 'completed_utc': datetime.now(timezone.utc).isoformat(),
        'ordinary_producer_replays': producers, 'wire_permutations': permutation_report,
        'capture_checks': sum(item[part]['capture_checks'] for item in producers for part in ('generated', 'directed'))
                          + permutation_report['capture_checks'],
        'equality': 'standalone direct bytes/hash and independent original typed values in both client decoding paths',
        'saved_messages': 'ordinary producer messages independently checked against source-value Python oracle before client decoding',
        'source_sha256': starting_sources, 'sources_changed_during_run': verify.source_fingerprints() != starting_sources}
    (out / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
