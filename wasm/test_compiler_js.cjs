#!/usr/bin/env node
// Run the direct fixture through the JS interpreter and JS-built Wasm.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const childProcess = require('node:child_process');

const compiler = process.env.CX_MOTOKO_DIR || path.join(__dirname, '_build', 'motoko-minimal');
const api = require(path.join(compiler, 'src', '_build', 'default', 'js', 'moc_interpreter.bc.js'));
const motoko = api.Motoko || globalThis.Motoko;
assert.ok(motoko, 'Motoko JS interpreter export');
for (const name of ['cx-direct-candid']) {
  const source = fs.readFileSync(path.join(compiler, 'test', 'run', `${name}.mo`), 'utf8');
  motoko.saveFile(`${name}.mo`, source);
  const result = motoko.run([], `${name}.mo`);
  assert.equal(result.stderr, '', result.stderr);
  assert.equal(result.result.error, null, JSON.stringify(result));
}
const removed = 'removed-inbound-api.mo';
const removedSource = 'import Prim "mo:⛔"; let _ = Prim.canonicalizeCandid("DIDL\\00\\00");';
motoko.saveFile(removed, removedSource);
const absent = motoko.run([], removed);
assert.notEqual(absent.result.error, null, 'removed inbound API must fail typechecking');
assert.match(absent.stderr, /canonicalizeCandid/);
process.env.MOC_RELEASE_RTS = path.join(compiler, 'rts', 'mo-rts.wasm');
process.env.MOC_DEBUG_RTS = path.join(compiler, 'rts', 'mo-rts-debug.wasm');
const compilerApi = require(path.join(compiler, 'src', '_build', 'default', 'js', 'moc_js.bc.js'));
const moc = compilerApi.Motoko || globalThis.Motoko;
moc.saveFile(removed, removedSource);
const rejected = moc.compileWasm('wasi', removed);
assert.ok(!rejected.code, 'removed inbound API must not compile');
assert.ok(rejected.diagnostics.some(message => message.severity === 1 &&
  message.message.includes('canonicalizeCandid')), JSON.stringify(rejected.diagnostics));
const name = 'cx-direct-candid.mo';
moc.saveFile(name, fs.readFileSync(path.join(compiler, 'test', 'run', name), 'utf8'));
const compiled = moc.compileWasm('wasi', name);
assert.ok(compiled.code, JSON.stringify(compiled.diagnostics));
assert.equal(compiled.diagnostics.filter(message => message.severity === 1).length, 0);
const out = process.env.CX_TEST_OUT || path.join(__dirname, '..', '_out', 'wasm-minimal-js-fixtures');
const output = path.join(out, 'cx-direct-candid.wasm');
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, compiled.code.wasm);
const run = childProcess.spawnSync(process.env.WASMTIME || 'wasmtime', ['-W', 'memory64', output], { encoding: 'utf8' });
assert.equal(run.status, 0, run.error?.message || run.stderr || run.stdout);
process.stdout.write(JSON.stringify({ js_interpreter_fixtures: 1, js_compiler_wasm_fixtures: 1,
  removed_inbound_api_checks: 2, profile: 'cx' }) + '\n');
