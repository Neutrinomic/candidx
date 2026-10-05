#!/usr/bin/env node
// Local IC heap/GC probe. Uses the existing TS SDK solely for Candid RPCs.
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import { readFile, writeFile, mkdir, unlink } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { deepStrictEqual, strictEqual } from 'node:assert';
import { setTimeout as delay } from 'node:timers/promises';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(resolve(root, 'ts/package.json'));
const { IDL } = require('@icp-sdk/core/candid');
const { Principal } = require('@icp-sdk/core/principal');
const { canonicalize } = await import('../ts/dist/index.js');
const options = { wasm: null, label: 'local', server: 'pocket-ic', trials: 3,
  source: null, direct: false, 'stress-phase': 'canonicalize_preencoded' };
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || process.argv[i + 1] === undefined) throw Error(`Unknown option ${process.argv[i]}`);
  if (key === 'direct') {
    if (!['true', 'false'].includes(process.argv[i + 1])) throw Error('--direct expects true or false');
    options[key] = process.argv[i + 1] === 'true';
  } else options[key] = key === 'trials' ? Number(process.argv[i + 1]) : process.argv[i + 1];
}
if (!options.wasm || !Number.isSafeInteger(options.trials) || options.trials < 1)
  throw Error('Usage: node benchmarks/heap.mjs --wasm path.wasm [--server pocket-ic] [--label name] [--trials 3] [--direct true] [--stress-phase canonical_encode_direct]');
const out = resolve(root, '_out');
await mkdir(out, { recursive: true });
const portFile = resolve(out, `pocketic-heap-${process.pid}.port`);
const logFile = resolve(out, `pocketic-heap-${options.label}.log`);
const wasm = new Uint8Array(await readFile(resolve(options.wasm)));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const base64 = bytes => Buffer.from(bytes).toString('base64');
const anon = Principal.anonymous();
const management = Principal.fromUint8Array(new Uint8Array());
const Stats = IDL.Record({ heap: IDL.Nat, allocated: IDL.Nat, reclaimed: IDL.Nat,
  memory: IDL.Nat, maxLive: IDL.Nat, collectorInstructions: IDL.Nat });
const Measurement = IDL.Record({ before: Stats, after: Stats, instructions: IDL.Nat64, outputSize: IDL.Nat });
const Settings = IDL.Record({ controllers: IDL.Opt(IDL.Vec(IDL.Principal)) });
const Create = IDL.Record({ amount: IDL.Opt(IDL.Nat), settings: IDL.Opt(Settings), specified_id: IDL.Opt(IDL.Principal) });
const Install = IDL.Record({ mode: IDL.Variant({ install: IDL.Null }), canister_id: IDL.Principal,
  wasm_module: IDL.Vec(IDL.Nat8), arg: IDL.Vec(IDL.Nat8) });
const Created = IDL.Record({ canister_id: IDL.Principal });
const specs = [['small', 0, 4, 32], ['records-128', 0, 128, 32], ['blob-64k', 0, 1, 65536], ['records-1024', 0, 1024, 0],
  ['nat64-4096', 1, 4096, 0], ['text-64k', 2, 0, 65536], ['unicode-64k', 3, 0, 65536]];
const phases = ['ordinary_encode', 'canonicalize_preencoded', 'canonical_encode_pipeline', 'ordinary_decode', 'canonical_decode', 'control'];
if (options.direct) phases.push('canonical_encode_direct');
const stressPhase = phases.indexOf(options['stress-phase']);
if (stressPhase < 0 || phases[stressPhase] === 'control') throw Error('Unknown or unmeasurable --stress-phase');
let base, instanceId;
let logs = '';
const server = spawn(options.server, ['--ip-addr', '127.0.0.1', '--port-file', portFile, '--ttl', '3600', '--log-levels', 'pocket_ic_server=warn'], { stdio: ['ignore', 'pipe', 'pipe'] });
server.stdout.on('data', chunk => { logs += chunk.toString(); });
server.stderr.on('data', chunk => { logs += chunk.toString(); });
let spawnError;
server.on('error', error => { spawnError = error; });

async function rest(path, method = 'GET', body) {
  const deadline = Date.now() + 120_000;
  let endpoint = path, verb = method, payload = body;
  while (Date.now() < deadline) {
    const response = await fetch(`${base}/${endpoint}`, { method: verb,
      headers: { 'content-type': 'application/json' },
      body: payload === undefined ? undefined : JSON.stringify(payload), signal: AbortSignal.timeout(120_000) });
    const text = await response.text();
    let value;
    try { value = text ? JSON.parse(text) : null; }
    catch { throw Error(`${path}: ${response.status} ${text}`); }
    if (value?.state_label && value?.op_id) {
      if (response.status === 202) {
        endpoint = `read_graph/${encodeURIComponent(value.state_label)}/${encodeURIComponent(value.op_id)}`;
        verb = 'GET'; payload = undefined;
      } else if (response.status !== 409 && response.status !== 200) throw Error(`${path}: ${response.status} ${text}`);
      await delay(10); continue;
    }
    if (!response.ok) throw Error(`${path}: ${response.status} ${text}`);
    return value;
  }
  throw Error(`Timed out at ${path}`);
}
function ok(value, method) {
  if (!Object.hasOwn(value, 'Ok')) throw Error(`${method}: ${JSON.stringify(value)}`);
  return value.Ok;
}
async function call(canister, method, types = [], values = [], replyTypes = [], query = false, effective = 'None') {
  const body = { sender: base64(anon.toUint8Array()), canister_id: base64(canister.toUint8Array()), method,
    payload: base64(IDL.encode(types, values)), effective_principal: effective };
  let value;
  if (query) value = await rest(`instances/${instanceId}/read/query`, 'POST', body);
  else {
    const submitted = ok(await rest(`instances/${instanceId}/update/submit_ingress_message`, 'POST', body), method);
    value = await rest(`instances/${instanceId}/update/await_ingress_message`, 'POST', submitted);
  }
  return IDL.decode(replyTypes, Uint8Array.from(Buffer.from(ok(value, method), 'base64')));
}
async function stats(canister) { return (await call(canister, 'stats', [], [], [Stats], true))[0]; }
async function collect(canister) {
  let current;
  // Fixed increments: occupied heap cannot converge while the current 64 MiB
  // allocation partition retains garbage and GC creates fresh mark bitmaps.
  for (let step = 0; step < 4; step++) {
    await call(canister, '__motoko_gc_trigger');
    current = await stats(canister);
  }
  return { ...current, increments: 4 };
}
const report = { label: options.label, recorded_utc: new Date().toISOString(), profile: 'cx',
  wasm_sha256: sha(wasm), trials: options.trials,
  phases, stress_phase: phases[stressPhase],
  method: 'Fresh local IC canister per workload/trial; clear previous output, force GC, execute one operation, retain output, force GC; no hashing inside canister',
  heap_semantics: 'Before/after snapshots exclude reply serialization. Operation-end occupied heap is the operation peak when no collection occurs inside the synchronous operation. Post-GC occupied heap includes retained data, bookkeeping, and garbage in the current 64 MiB allocation partition (not evacuated). It is not an exact live-object measurement, process RSS, or reserved linear memory.',
  cases: [] };
report.source_fingerprints = Object.fromEntries(await Promise.all([
  'benchmarks/heap.mjs', 'benchmarks/runtime_sources.py', 'motoko/src/lib.mo',
  'motoko/test/heap.mo', 'rust/src/lib.rs', 'rust/src/tree.rs', 'rust/src/visitor.rs',
  'wasm/motoko-compiler.patch', 'wasm/artifacts/manifest.json',
].map(async path => [path, sha(await readFile(resolve(root, path)))])));
if (options.source) report.generated_source = {
  path: options.source, sha256: sha(await readFile(resolve(options.source))) };
try {
  for (let attempt = 0; attempt < 500; attempt++) {
    if (spawnError) throw spawnError;
    if (server.exitCode !== null) throw Error(`PocketIC exited: ${logs}`);
    try {
      const port = (await readFile(portFile, 'utf8')).trim();
      if (!/^[1-9][0-9]*$/.test(port)) { await delay(10); continue; }
      base = `http://127.0.0.1:${port}`; break;
    }
    catch { await delay(10); }
  }
  if (!base) throw Error('PocketIC startup timed out');
  report.pocket_ic_version = execFileSync(options.server, ['--version'], { encoding: 'utf8' }).trim();
  const subnet = { state_config: 'New', instruction_config: 'Production', subnet_admins: null, cost_schedule: 'Normal' };
  const config = { subnet_config_set: { nns: null, sns: null, ii: null, fiduciary: null,
    bitcoin: null, test_threshold_keys: null, system: [], application: [subnet], cloud_engine: [], verified_application: [] },
    http_gateway_config: null, state_dir: null, icp_config: null, log_level: null, bitcoind_addr: null,
    dogecoind_addr: null, icp_features: null, incomplete_state: 'Disabled', initial_time: { Timestamp: { nanos_since_epoch: 1700000000000000000 } },
    mainnet_nns_subnet_id: false, disable_ingress_validation: false };
  const created = await rest('instances', 'POST', config);
  if (!created.Created) throw Error(`Cannot create instance: ${JSON.stringify(created)}`);
  instanceId = created.Created.instance_id;
  const effective = { CanisterId: created.Created.topology.default_effective_canister_id.canister_id };
  for (const [name, kind, count, memoSize] of specs) {
    const caseResult = { name, measurements: [] };
    for (let trial = 0; trial < options.trials; trial++) {
      const [made] = await call(management, 'provisional_create_canister_with_cycles', [Create],
        [{ amount: [10n ** 15n], settings: [{ controllers: [ [anon] ] }], specified_id: [] }], [Created], false, effective);
      const canister = made.canister_id;
      await call(management, 'install_code', [Install], [{ mode: { install: null }, canister_id: canister,
        wasm_module: wasm, arg: new Uint8Array(IDL.encode([], [])) }], [], false, { CanisterId: base64(canister.toUint8Array()) });
      await call(canister, 'configure', [IDL.Nat, IDL.Nat, IDL.Nat], [BigInt(kind), BigInt(count), BigInt(memoSize)]);
      const [raw, canonical] = await call(canister, 'bytes', [], [], [IDL.Vec(IDL.Nat8), IDL.Vec(IDL.Nat8)], true);
      deepStrictEqual(canonical, canonicalize(raw), `${name}: canonical bytes`);
      caseResult.ordinary_bytes = raw.length; caseResult.canonical_bytes = canonical.length;
      caseResult.canonical_sha256 = sha(canonical);
      for (let phase = 0; phase < phases.length; phase++) {
        await call(canister, 'clear');
        const clean = await collect(canister);
        const [sample] = await call(canister, 'run', [IDL.Nat, IDL.Nat], [BigInt(phase), 1n], [Measurement]);
        strictEqual(sample.before.reclaimed, sample.after.reclaimed, 'Unexpected GC inside synchronous operation');
        const retained = await collect(canister);
        strictEqual((await call(canister, 'check', [], [], [IDL.Bool], true))[0], true, 'Original typed block lost');
        caseResult.measurements.push({ trial, phase: phases[phase], instructions: sample.instructions,
          allocated_bytes: sample.after.allocated - sample.before.allocated,
          heap_before: sample.before.heap, heap_after: sample.after.heap,
          occupied_heap_growth: sample.after.heap - sample.before.heap,
          clean_heap: clean.heap, retained_heap: retained.heap,
          retained_heap_growth: retained.heap - clean.heap,
          reclaimed_after_gc: retained.reclaimed - clean.reclaimed,
          gc_instructions: retained.collectorInstructions - clean.collectorInstructions,
          wasm_memory_before: sample.before.memory, wasm_memory_after: sample.after.memory,
          max_live_ever_after_gc: retained.maxLive,
          forced_gc_increments: retained.increments, output_size: sample.outputSize });
      }
      await call(canister, 'clear');
      const clean = await collect(canister);
      const single = caseResult.measurements.find(item => item.trial === trial && item.phase === phases[stressPhase]);
      const allocationTargetIterations = Math.ceil(72 * 1024 * 1024 / Number(single.allocated_bytes));
      const instructionBudgetIterations = Math.max(1, Math.floor(5_000_000_000 / Number(single.instructions)));
      const iterations = BigInt(Math.max(1, Math.min(20_000, allocationTargetIterations, instructionBudgetIterations)));
      const [stress] = await call(canister, 'run', [IDL.Nat, IDL.Nat], [BigInt(stressPhase), iterations], [Measurement]);
      const collected = await collect(canister);
      const stressResult = { trial, phase: phases[stressPhase], iterations, allocation_target_iterations: allocationTargetIterations,
        instruction_budget_iterations: instructionBudgetIterations, instructions: stress.instructions,
        crossed_heap_partition: stress.after.allocated - stress.before.allocated >= 64n * 1024n * 1024n,
        allocated_bytes: stress.after.allocated - stress.before.allocated,
        heap_before: stress.before.heap, peak_occupied_heap: stress.after.heap,
        occupied_heap_after_forced_gc: collected.heap,
        reclaimed_bytes: collected.reclaimed - clean.reclaimed,
        gc_instructions: collected.collectorInstructions - clean.collectorInstructions,
        output_size: stress.outputSize, wasm_memory_bytes: collected.memory };
      (caseResult.gc_stress ??= []).push(stressResult);
      if (Number(stressResult.allocated_bytes) >= 64 * 1024 * 1024 && stressResult.reclaimed_bytes <= 0n)
        throw Error(`${name}: no collection observed after crossing a heap partition`);
      strictEqual((await call(canister, 'check', [], [], [IDL.Bool], true))[0], true);
      console.log(`${options.label} ${name} trial ${trial + 1}/${options.trials}: heap and GC captured`);
    }
    report.cases.push(caseResult);
  }
  const path = resolve(out, `heap-${options.label}.json`);
  await writeFile(path, JSON.stringify(report, (_, value) => typeof value === 'bigint' ?
    (value <= BigInt(Number.MAX_SAFE_INTEGER) && value >= BigInt(Number.MIN_SAFE_INTEGER) ? Number(value) : value.toString()) : value, 2) + '\n');
  console.log(path);
} finally {
  if (instanceId !== undefined && base) await rest(`instances/${instanceId}`, 'DELETE').catch(() => {});
  server.kill('SIGTERM');
  await writeFile(logFile, logs);
  await unlink(portFile).catch(() => {});
}
