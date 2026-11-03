#!/usr/bin/env python3
"""Emit a remote SHA-256 check for rsync's reported file discrepancies.

Apple openrsync can report a checksum difference for identical files when
interoperating with GNU rsync. Only regular-file checksum discrepancies qualify
for this independent check; deletion, type, and directory differences fail.
"""
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
expected = {}
for line in sys.stdin.read().splitlines():
    fields = line.split(maxsplit=1)
    if len(fields) != 2 or not fields[0].startswith("<fc"):
        raise ValueError(f"unverified rsync change: {line}")
    name = fields[1]
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file() or (root / name).is_symlink():
        raise ValueError(f"unsupported checksum path: {name}")
    expected[name] = hashlib.sha256(path.read_bytes()).hexdigest()

print("import hashlib, sys\nfrom pathlib import Path")
print(f"expected = {json.dumps(expected)}")
print("""
root = Path(sys.argv[1])
for name, digest in expected.items():
    path = root / name
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit(f'Remote SHA-256 mismatch: {name}')
print(f'SHA-256 verified {len(expected)} reported checksum discrepancies')
""")
