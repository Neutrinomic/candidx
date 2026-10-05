#!/usr/bin/env python3
"""Retain compact repeated-run evidence and aggregate within-run ratios."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
PHASES = ('ordinary_encode', 'canonicalize_preencoded', 'canonical_encode_direct', 'canonical_encode_pipeline',
          'ordinary_decode', 'canonical_decode', 'compatible_decode')


def compact(value):
    if isinstance(value, list):
        return [compact(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key.endswith('_hex') and isinstance(item, str):
            result[key[:-4] + '_sha256'] = sha256(bytes.fromhex(item)).hexdigest()
        else:
            result[key] = compact(item)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs=3)
    parser.add_argument('--prefix', default='final', help='Output names: results-PREFIX-{1,2,3,summary}.json')
    args = parser.parse_args()
    runs = [json.loads(path.read_text()) for path in args.reports]
    assert all(run['source_fingerprints'] == runs[0]['source_fingerprints'] for run in runs), 'Benchmark sources differ'
    assert all(set(run['results']) == set(runs[0]['results']) for run in runs), 'Backends differ'
    for i, run in enumerate(runs, 1):
        (ROOT / f'benchmarks/results-{args.prefix}-{i}.json').write_text(json.dumps(compact(run), indent=2) + '\n')
    initial = json.loads((ROOT / 'benchmarks/results-before.json').read_text())['results']
    rows = []
    for lang, result in runs[0]['results'].items():
        for case in result['cases']:
            samples = [next(c for c in run['results'][lang]['cases'] if c['name'] == case['name']) for run in runs]
            row = {'language': lang, 'name': case['name'], 'ordinary_bytes': case['ordinary_bytes'],
                   'canonical_bytes': case['canonical_bytes'], 'phases': {}}
            for phase in PHASES:
                if phase not in case:
                    continue
                stats = [sample[phase] for sample in samples]
                row['phases'][phase] = {'median_us': median(s['median_us'] for s in stats),
                    'min_us': min(s['min_us'] for s in stats), 'max_us': max(s['max_us'] for s in stats)}
                for key in ('median_fuel', 'allocated_bytes_per_op'):
                    if key in stats[0]:
                        row['phases'][phase][key] = median(s[key] for s in stats)
            if 'ordinary_encode' in case:
                primary = 'canonical_encode_direct' if 'canonical_encode_direct' in case else 'canonical_encode_pipeline'
                row['encode_method'] = primary
                row['encode_ratio'] = median(s[primary]['median_us'] / s['ordinary_encode']['median_us'] for s in samples)
                if 'canonical_encode_direct' in case and 'canonical_encode_pipeline' in case:
                    row['direct_speedup_vs_pipeline'] = median(s['canonical_encode_pipeline']['median_us'] / s['canonical_encode_direct']['median_us'] for s in samples)
            if lang in initial:
                before = next(c for c in initial[lang]['cases'] if c['name'] == case['name'])
                row['normalizer_speedup_vs_initial'] = before['canonicalize_preencoded']['median_us'] / row['phases']['canonicalize_preencoded']['median_us']
            rows.append(row)
    summary = {'profile': 'cx', 'aggregation': 'median of 3 runs, each 7 trials; ratio median computed within each run',
               'source_fingerprints': runs[0]['source_fingerprints'], 'rows': rows}
    suffix = 'summary' if args.prefix == 'final' else f'{args.prefix}-summary'
    (ROOT / f'benchmarks/results-{suffix}.json').write_text(json.dumps(summary, indent=2) + '\n')
    for row in rows:
        print(row['language'], row['name'], f"normalize {row['phases']['canonicalize_preencoded']['median_us']:.2f} us",
              f"{row['encode_method']} {row['encode_ratio']:.2f}x" if 'encode_ratio' in row else 'ordinary baseline unavailable')


if __name__ == '__main__':
    main()
