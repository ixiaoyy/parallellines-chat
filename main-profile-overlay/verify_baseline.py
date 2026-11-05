"""Refuse to overwrite a main-site source that changed after review."""
import hashlib
import json
from pathlib import Path
import sys

def verify_source(root: Path, manifests: list[Path]) -> str:
    """Check the app root against full manifests; return the matched name or exit."""
    # 只接受整套已审阅基线，防止逐文件放行后覆盖混合版本或未知改动。
    for manifest in manifests:
        expected = json.loads(manifest.read_text(encoding='utf-8'))
        if expected and all((root / relative).is_file() and
                            hashlib.sha256((root / relative).read_bytes()).hexdigest() == wanted
                            for relative, wanted in expected.items()):
            return manifest.name
    raise SystemExit('Main source does not match a complete reviewed manifest')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        raise SystemExit('Usage: verify_baseline.py APP_ROOT MANIFEST [MANIFEST ...]')
    matched = verify_source(Path(sys.argv[1]), [Path(value) for value in sys.argv[2:]])
    print('Main source manifest verified: ' + matched)
