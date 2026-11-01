#!/usr/bin/env python3
"""Extract literal protocol tables from a reviewed local the-via/app checkout."""
from pathlib import Path
import json
import re
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
source = Path(sys.argv[1]).resolve()
tables = {}
for protocol, filename in ((9, "default"), (10, "v10"), (11, "v11"), (12, "v12"), (13, "v13")):
    text = (source / f"src/utils/key-to-byte/{filename}.ts").read_text()
    literals = {name: int(value, 16) for name, value in re.findall(r"^\s+([A-Z_][A-Za-z_0-9]*):\s*(0x[0-9a-fA-F]+),", text, re.M)}
    if protocol == 13:
        result = dict(tables["12"])
        for old in re.findall(r"^\s+(RGB_\w+): _RGB_\w+,", text, re.M):
            result.pop(old, None)
        result.update(literals)
    else:
        result = literals
    tables[str(protocol)] = result
metadata = {"source": "https://github.com/the-via/app", "commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(), "license": "GPL-3.0"}
(root / "resources/keycodes.json").write_text(json.dumps({"metadata": metadata, "protocols": tables}, indent=2, sort_keys=True) + "\n")
licenses = root / "resources/licenses"
licenses.mkdir(exist_ok=True)
(licenses / "via-app-GPL-3.0.txt").write_bytes((source / "LICENSE").read_bytes())
print({protocol: len(table) for protocol, table in tables.items()})
