"""个人连接增量统一入口；固定源码、完整前置和全部输出核对后才写入。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location('personal_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(root, backend_only=False):
    """按已有分层准备补丁；root 为完整源码根，backend_only 同样不改变存储或服务。"""
    root = Path(root)
    result = {}
    for name in ('backend', 'credentials', 'audit'):
        for relative, source in _load(name).prepare(root / 'backend/open_webui').items():
            path = 'backend/open_webui/' + relative
            if path in result:
                raise ValueError('personal_patch_owner_collision:' + path)
            result[path] = source
    if not backend_only:
        for name in ('frontend', 'runtime'):
            for path, source in _load(name).prepare(root).items():
                if path in result:
                    raise ValueError('personal_patch_owner_collision:' + path)
                result[path] = source
    return result


def apply(root, check=False, backend_only=False):
    """核对固定清单的前后字节，不接受重复应用、未知版本、路径逃逸或半套补丁。"""
    root = Path(root).resolve(strict=True)
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    files = {p: v for p, v in manifest['files'].items() if not backend_only or p.startswith('backend/')}
    for relative, expected in files.items():
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError('personal_source_path_invalid:' + relative)
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if digest != expected['before_sha256']:
            raise ValueError('personal_source_baseline_changed:' + relative)
    prepared = prepare(root, backend_only)
    if set(prepared) != set(files):
        raise ValueError('personal_source_inventory_changed')
    for relative, source in prepared.items():
        if hashlib.sha256(source.encode()).hexdigest() != files[relative]['after_sha256']:
            raise ValueError('personal_source_result_changed:' + relative)
    if not check:
        for relative, source in prepared.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding='utf-8', newline='\n')
    return list(prepared)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--backend-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, args.check, args.backend_only)))
