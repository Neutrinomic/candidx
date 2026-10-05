import { readFileSync } from 'node:fs';
import { canonicalize } from '../dist/index.js';
const lines = readFileSync(0, 'utf8').split('\n');
if (lines.at(-1) === '') lines.pop();
for (const line of lines) {
  try { console.log(`ok ${Buffer.from(canonicalize(Buffer.from(line, 'hex'))).toString('hex')}`); }
  catch (error) { console.log(`error ${error.message}`); }
}
