#!/usr/bin/env node
// Fresh-process V8 occupancy probes; these do not estimate allocation totals.
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { deepStrictEqual } from 'node:assert';

const here = fileURLToPath(import.meta.url);
const root = resolve(dirname(here), '..');
const require = createRequire(resolve(root, 'ts/package.json'));
const { IDL } = require('@icp-sdk/core/candid');
const { canonicalize } = await import('../ts/dist/index.js');
const { encodeCanonical } = await import('../ts/dist/candid.js');
const phases = ['ordinary_encode', 'canonical_encode_direct', 'canonical_encode_pipeline', 'canonicalize_preencoded'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const args = process.argv.slice(2);

if (args[0] === '--worker') {
  if (!global.gc) throw Error('This probe requires --expose-gc');
  const [, fixturePath, name, phase] = args;
  const fixture = JSON.parse(readFileSync(fixturePath, 'utf8')).cases.find(c => c.name === name);
  if (!fixture || !phases.includes(phase)) throw Error('Unknown case or phase');
  const Tx = IDL.Record({ owner: IDL.Principal, amount: IDL.Nat, memo: IDL.Opt(IDL.Vec(IDL.Nat8)) });
  const Block = IDL.Record({ timestamp: IDL.Nat64, parentHash: IDL.Opt(IDL.Vec(IDL.Nat8)), tx: IDL.Vec(Tx), note: IDL.Opt(IDL.Text) });
  const raw = Uint8Array.from(Buffer.from(fixture.raw_hex, 'hex'));
  const [block] = IDL.decode([Block], raw); // Preparation is outside the measurement.
  const operations = {
    ordinary_encode: () => IDL.encode([Block], [block]),
    canonical_encode_direct: () => encodeCanonical([Block], [block]),
    canonical_encode_pipeline: () => canonicalize(IDL.encode([Block], [block])),
    canonicalize_preencoded: () => canonicalize(raw),
  };
  // Explicit roots keep prepared inputs alive even after their last local use.
  const roots = { fixture, raw, block, operations, output: null };
  globalThis.__cx_memory_roots = roots;
  // Populate SDK/type caches and then discard the warmup output.
  for (const operation of Object.values(operations)) operation();
  const collect = () => { for (let i = 0; i < 4; i++) global.gc(); };
  const snapshot = () => {
    const { heapUsed, heapTotal, external, arrayBuffers } = process.memoryUsage();
    return { heap_used_bytes: heapUsed, heap_capacity_bytes: heapTotal,
      external_bytes: external, array_buffer_bytes: arrayBuffers };
  };
  collect();
  const before = snapshot();
  roots.output = operations[phase]();
  const output = roots.output;
  const after = snapshot();
  collect();
  const retained = snapshot();
  // Keep the output alive through the retained snapshot and verify it afterward.
  const expected = phase === 'ordinary_encode' ? Buffer.from(IDL.encode([Block], [block])) : Buffer.from(fixture.canonical_hex, 'hex');
  deepStrictEqual(Buffer.from(output), expected);
  console.log(JSON.stringify({ before, after, retained, output_bytes: output.byteLength, output_sha256: hash(output) }));
} else {
  const [fixturePath, label = 'direct'] = args;
  if (!fixturePath) throw Error('Usage: node benchmarks/ts_memory.mjs BENCH-RUST.json [LABEL]');
  const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
  const report = {
    label, recorded_utc: new Date().toISOString(), profile: 'cx', node: process.version,
    fixture_sha256: hash(readFileSync(fixturePath)),
    method: 'Three fresh Node processes per workload/phase; retain source values; warm SDK/type caches; force four GCs before and after one operation; retain and verify output',
    semantics: 'Occupied V8 heap, external memory and ArrayBuffer snapshots; not total allocations or guaranteed peak (V8 may collect during the operation). Heap capacity is reserved storage. Small deltas include measurement bookkeeping and GC noise. Existing input and fixtures remain retained in every phase.',
    source_fingerprints: Object.fromEntries([...readdirSync(resolve(root, 'ts/src'), { recursive: true }).filter(path => path.endsWith('.ts')).map(path => `ts/src/${path}`), 'benchmarks/ts_memory.mjs'].map(path => [path, hash(readFileSync(resolve(root, path)))])),
    cases: []
  };
  for (const item of fixture.cases) {
    const measurements = [];
    for (const phase of phases) {
      for (let trial = 0; trial < 3; trial++) {
        const sample = JSON.parse(execFileSync(process.execPath,
          ['--expose-gc', here, '--worker', resolve(fixturePath), item.name, phase], { encoding: 'utf8' }));
        measurements.push({ trial, phase, ...sample });
      }
    }
    report.cases.push({ name: item.name, measurements });
    console.log(`${item.name}: V8 occupancy captured`);
  }
  const out = resolve(root, '_out');
  mkdirSync(out, { recursive: true });
  const path = resolve(out, `ts-memory-${label}.json`);
  writeFileSync(path, JSON.stringify(report, null, 2) + '\n');
  console.log(path);
}
