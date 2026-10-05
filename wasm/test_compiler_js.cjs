#!/usr/bin/env node
// Run the same direct and inbound fixtures through the built JS interpreter.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const childProcess = require('node:child_process');

const compiler = process.env.CX_MOTOKO_DIR || path.join(__dirname, '_build', 'motoko');
const api = require(path.join(compiler, 'src', '_build', 'default', 'js', 'moc_interpreter.bc.js'));
const motoko = api.Motoko || globalThis.Motoko;
assert.ok(motoko, 'Motoko JS interpreter export');
for (const name of ['cx-direct-candid', 'cx-candid']) {
  const source = fs.readFileSync(path.join(compiler, 'test', 'run', `${name}.mo`), 'utf8');
  motoko.saveFile(`${name}.mo`, source);
  const result = motoko.run([], `${name}.mo`);
  assert.equal(result.stderr, '', result.stderr);
  assert.equal(result.result.error, null, JSON.stringify(result));
}
process.env.MOC_RELEASE_RTS = path.join(compiler, 'rts', 'mo-rts.wasm');
process.env.MOC_DEBUG_RTS = path.join(compiler, 'rts', 'mo-rts-debug.wasm');
const compilerApi = require(path.join(compiler, 'src', '_build', 'default', 'js', 'moc_js.bc.js'));
const moc = compilerApi.Motoko || globalThis.Motoko;
const name = 'cx-direct-candid.mo';
moc.saveFile(name, fs.readFileSync(path.join(compiler, 'test', 'run', name), 'utf8'));
const compiled = moc.compileWasm('wasi', name);
assert.ok(compiled.code, JSON.stringify(compiled.diagnostics));
assert.equal(compiled.diagnostics.filter(message => message.severity === 1).length, 0);
const output = path.join(__dirname, '..', '_out', 'wasm-js-direct-fixture.wasm');
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, compiled.code.wasm);
const run = childProcess.spawnSync(process.env.WASMTIME || 'wasmtime', ['-W', 'memory64', output], { encoding: 'utf8' });
assert.equal(run.status, 0, run.error?.message || run.stderr || run.stdout);
process.stdout.write(JSON.stringify({ js_interpreter_fixtures: 2, js_compiler_wasm_fixtures: 1, profile: 'cx' }) + '\n');
