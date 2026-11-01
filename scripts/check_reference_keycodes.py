#!/usr/bin/env python3
"""Check emitted VIA strings against the real reference parser (development only).

Usage: python scripts/check_reference_keycodes.py /path/to/the-via/app
Needs Node 22+ for built-in TypeScript stripping; the application does not.
No package install, browser, server, keyboard, or network access is used.
"""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from omakeeb.keycodes import KeycodeError, via_name

reference = Path(sys.argv[1]).resolve()
cases = []
for protocol in range(9, 14):
    for code in range(65536):
        try:
            text = via_name(code, protocol=protocol)
        except KeycodeError:
            continue
        cases.append([protocol, code, text])
program = r'''
import fs from 'node:fs';
import vm from 'node:vm';
import {pathToFileURL} from 'node:url';
import {stripTypeScriptTypes} from 'node:module';
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(payload.reference + '/src/utils/key.ts', 'utf8');
const begin = source.indexOf('export function getByteForCode(');
const end = source.indexOf('\nfunction getCodeForLayerByte(', begin);
if (begin < 0 || end < 0) throw Error('Reference source boundaries changed');
const code = stripTypeScriptTypes(source.slice(begin, end).replace(/^export /mg, ''));
const advanced = await import(pathToFileURL(payload.reference + '/src/utils/advanced-keys.ts'));
const context = {advancedStringToKeycode: advanced.advancedStringToKeycode};
vm.createContext(context);
vm.runInContext(code, context);
let count = 0;
for (const [protocol, expected, text] of payload.cases) {
    const actual = context.getByteForCode(text, payload.tables[protocol]);
    if (actual !== expected) throw Error(`VIA${protocol} ${text}: expected ${expected}, got ${actual}`);
    count++;
}
console.log(`Actual VIA parser preserved all ${count.toLocaleString()} exported values across protocols 9–13.`);
'''
tables = json.loads((root / 'resources/keycodes.json').read_text())['protocols']
subprocess.run(['node', '--no-warnings', '--experimental-strip-types', '--input-type=module', '-e', program],
               input=json.dumps({'reference': str(reference), 'cases': cases, 'tables': tables}),
               text=True, check=True)
