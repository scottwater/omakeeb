#!/usr/bin/env python3
"""Vendor literal Vial keycode names from a reviewed local vial-gui checkout."""
import ast
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
source = Path(sys.argv[1]).resolve()
structural = {'QK_LAYER_TAP', 'QK_TO', 'QK_MOMENTARY', 'QK_DEF_LAYER', 'QK_PERSISTENT_DEF_LAYER',
              'QK_TOGGLE_LAYER', 'QK_ONE_SHOT_LAYER', 'QK_ONE_SHOT_MOD', 'QK_TAP_DANCE',
              'QK_LAYER_TAP_TOGGLE', 'QK_LAYER_MOD', 'QK_MOD_TAP', 'QK_MACRO', 'QK_KB',
              'QMK_LM_SHIFT', 'QMK_LM_MASK', 'ON_PRESS'}
tables = {}
for version in (5, 6):
    tree = ast.parse((source / f'src/main/python/keycodes/keycodes_v{version}.py').read_text())
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    literal = next(node.value for node in klass.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'kc' for target in node.targets))
    table = ast.literal_eval(literal)
    tables[str(version)] = {key: value for key, value in table.items()
        if '(' not in key and key not in structural
        and not key.startswith(('MOD_', 'QK_LCTL', 'QK_LSFT', 'QK_LALT', 'QK_LGUI',
                                'QK_RCTL', 'QK_RSFT', 'QK_RALT', 'QK_RGUI'))
        and type(value) is int and 0 <= value < 0x9990}
metadata = {'source': 'https://github.com/vial-kb/vial-gui',
    'commit': subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(),
    'license': 'GPL-2.0-or-later',
    'sources': ['src/main/python/keycodes/keycodes_v5.py', 'src/main/python/keycodes/keycodes_v6.py'],
    'note': 'Literal key names only; structural constants and explicitly fake legacy placeholders excluded.'}
(root / 'resources/vial-keycodes.json').write_text(json.dumps({'metadata': metadata, 'protocols': tables}, sort_keys=True, indent=2) + '\n')
(root / 'resources/licenses/vial-gui-GPL-2.0.txt').write_bytes((source / 'COPYING').read_bytes())
print({version: len(table) for version, table in tables.items()})
