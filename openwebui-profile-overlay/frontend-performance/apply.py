"""把已审核的性能/弱网恢复增量应用到固定 Open WebUI 源码，不修改生成的 JS。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def apply(root):
    """root 为固定版本源码目录；逐文件核对基线后应用补丁和独立懒加载模块。"""
    feature = Path(__file__).resolve().parent
    manifest = json.loads((feature / 'manifest.json').read_text(encoding='utf-8'))
    for relative, expected in manifest['files'].items():
        target = root / relative
        if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
            raise RuntimeError('invalid_source_path')
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if actual != expected['before_sha256']:
            raise RuntimeError('performance_source_baseline_changed:' + relative)
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', str(feature / 'frontend-performance.patch')],
                   cwd=root, env={**os.environ, 'GIT_CEILING_DIRECTORIES': str(root.resolve().parent)}, check=True)
    (root / 'src/lib/utils/formattedClipboard.ts').write_bytes((feature / 'formattedClipboard.ts').read_bytes())
    for relative, expected in manifest['files'].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected['after_sha256']:
            raise RuntimeError('performance_source_result_changed:' + relative)


if __name__ == '__main__':
    apply(Path(sys.argv[1]).resolve(strict=True))
