#!/usr/bin/env python3
"""Vendor a reviewed local the-via/keyboards checkout, without network at runtime.

Usage: python scripts/vendor_definitions.py /path/to/the-via/keyboards
The source commit and license are bundled; invalid definitions are reported and
excluded. v3 supersedes src only for the exact same relative source filename.
"""
from pathlib import Path
import gzip
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from omakeeb.definitions import load_definition


def main():
    source = Path(sys.argv[1]).resolve()
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    entries = {}
    rejected = []
    candidates = {}
    for folder in ("src", "v3"):
        for path in sorted((source / folder).rglob("*.json")):
            candidates[str(path.relative_to(source / folder))] = path
    for path in candidates.values():
        id = str(path.relative_to(source))
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            definition = load_definition(raw, id=id)
            entries[id] = definition.raw
        except (ValueError, OSError) as exc:
            rejected.append({"path": id, "reason": str(exc)})
    metadata = {"source": "https://github.com/the-via/keyboards", "commit": commit, "license": "GPL-3.0", "count": len(entries), "rejected": rejected}
    content = json.dumps({"metadata": metadata, "definitions": entries}, separators=(",", ":"), sort_keys=True).encode()
    target = ROOT / "resources/definitions.json.gz"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(gzip.compress(content, compresslevel=9, mtime=0))
    metadata["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    (ROOT / "resources/definitions-source.json").write_text(json.dumps(metadata, indent=2) + "\n")
    licenses = ROOT / "resources/licenses"
    licenses.mkdir(exist_ok=True)
    (licenses / "via-keyboards-GPL-3.0.txt").write_bytes((source / "LICENSE").read_bytes())
    print(f"Bundled {len(entries)} definitions ({target.stat().st_size:,} bytes); excluded {len(rejected)} invalid files.")
    for issue in rejected:
        print(issue)


if __name__ == "__main__":
    main()
