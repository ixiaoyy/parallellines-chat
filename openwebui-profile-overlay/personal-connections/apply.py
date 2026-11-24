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


def prepare(root, backend_only=False, *, frontend_only=False):
    """在隔离源码根按选定范围准备补丁文本；两个单侧模式互斥。"""
    if backend_only and frontend_only:
        raise ValueError('personal_overlay_modes_conflict')
    root = Path(root)
    result = {}
    if not frontend_only:
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


def apply(root, check=False, backend_only=False, *, frontend_only=False):
    """校验并返回补丁路径；check 不写，frontend_only 不依赖后端前像。"""
    if backend_only and frontend_only:
        raise ValueError('personal_overlay_modes_conflict')
    root = Path(root).resolve(strict=True)
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    # 独立前端构建过滤输入检查；既有完整清单仍约束后续运行镜像的后端版本。
    files = {
        p: v for p, v in manifest['files'].items()
        if (not backend_only or p.startswith('backend/')) and (not frontend_only or not p.startswith('backend/'))
    }
    for relative, expected in files.items():
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError('personal_source_path_invalid:' + relative)
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if digest != expected['before_sha256']:
            raise ValueError('personal_source_baseline_changed:' + relative)
    prepared = prepare(root, backend_only, frontend_only=frontend_only)
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--backend-only', action='store_true')
    modes.add_argument('--frontend-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.root, args.check, args.backend_only, frontend_only=args.frontend_only)))
