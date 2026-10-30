"""Refuse to overwrite a main-site source that changed after review."""
import hashlib
import json
from pathlib import Path
import sys

root, manifest = Path(sys.argv[1]), Path(sys.argv[2])
expected = json.loads(manifest.read_text(encoding='utf-8'))
for relative, wanted in expected.items():
    target = root / relative
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != wanted:
        raise SystemExit('Main source baseline mismatch: ' + relative)
print('Main source baseline verified')
