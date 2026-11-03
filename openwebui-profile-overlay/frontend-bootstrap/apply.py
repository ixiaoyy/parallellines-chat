"""在已应用 frontend-performance 的固定源码上追加启动恢复与设置懒加载，不修改旧发布清单。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def apply(root):
    """root 为准备好的固定源码；先核对前置与全部基线，再应用补丁并核对结果，拒绝漂移和重复应用。"""
    feature = Path(__file__).resolve().parent
    manifest = json.loads((feature / 'manifest.json').read_text(encoding='utf-8'))
    performance = feature.parent / 'frontend-performance'
    if hashlib.sha256((performance / 'frontend-performance.patch').read_bytes()).hexdigest() != manifest['requires_performance_patch_sha256']:
        raise RuntimeError('bootstrap_performance_prerequisite_changed')
    for relative, expected in json.loads((performance / 'manifest.json').read_text(encoding='utf-8'))['files'].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected['after_sha256']:
            raise RuntimeError('bootstrap_performance_not_applied:' + relative)
    for relative, expected in manifest['files'].items():
        target = root / relative
        if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
            raise RuntimeError('invalid_source_path')
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if actual != expected['before_sha256']:
            raise RuntimeError('bootstrap_source_baseline_changed:' + relative)
    env = {**os.environ, 'GIT_CEILING_DIRECTORIES': str(root.resolve().parent)}
    command = ['git', '-c', 'core.autocrlf=false', 'apply']
    subprocess.run(command + ['--check', str(feature / 'frontend-bootstrap.patch')], cwd=root, env=env, check=True, timeout=30)
    subprocess.run(command + [str(feature / 'frontend-bootstrap.patch')], cwd=root, env=env, check=True, timeout=30)
    for name, relative in {
        'bootstrap.ts': 'src/lib/utils/bootstrap.ts',
        'settingsRetry.ts': 'src/lib/utils/settingsRetry.ts',
        'BootstrapError.svelte': 'src/lib/components/common/BootstrapError.svelte',
        'LazySettingsModal.svelte': 'src/lib/components/chat/LazySettingsModal.svelte',
    }.items():
        (root / relative).write_bytes((feature / name).read_bytes())
    for relative, expected in manifest['files'].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected['after_sha256']:
            raise RuntimeError('bootstrap_source_result_changed:' + relative)
    print(json.dumps({'applied': 'frontend-bootstrap', 'files': len(manifest['files'])}))


if __name__ == '__main__':
    apply(Path(sys.argv[1]).resolve(strict=True))
