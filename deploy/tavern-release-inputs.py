"""校验 Tavern 发布输入；仅准备公开镜像上下文，不访问服务或密钥。"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


UPSTREAM_COMMIT = 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca'
FRONTEND_FEATURES = (
    'frontend-performance', 'frontend-bootstrap', 'frontend-auth-recovery',
    'frontend-silent-recovery', 'unified-login', 'personal-connections',
    'frontend-i18n', 'frontend-chat-rooms',
)
BRIDGE_FILES = ('config.mjs', 'error-page.mjs', 'identity-provider.mjs')
BRIDGE_DEPENDENCIES = ('package.json', 'package-lock.json')
MAX_PUBLIC_FILES = 12000
MAX_PUBLIC_BYTES = 512 * 1024 * 1024


def _check(condition, code):
    if not condition:
        raise RuntimeError(code)


def _directory(path):
    _check(path.is_dir() and not any(p.is_symlink() for p in (path, *path.parents)),
           'tavern_release_directory_invalid')


def _read_public_file(path, limit=2 * 1024 * 1024):
    _directory(path.parent)
    _check(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit,
           'tavern_release_file_invalid')
    return path.read_bytes()


def prepare_bridge(source: Path, context: Path):
    """从已审 source 在新 context 生成桥增量，返回覆盖文件与依赖的 SHA-256，不复制依赖。"""
    checkout = source / 'identity-bridge'
    files = {name: _read_public_file(checkout / name) for name in BRIDGE_FILES}
    dependencies = {name: hashlib.sha256(_read_public_file(checkout / name)).hexdigest()
                    for name in BRIDGE_DEPENDENCIES}
    _directory(context.parent)
    # 单独派生当前镜像，仅覆盖本次三份源文件；不改变 WORKDIR、USER、健康检查或启动入口。
    context.mkdir(mode=0o700)
    for name, data in files.items():
        (context / name).write_bytes(data)
    names = ' '.join(BRIDGE_FILES)
    checks = ' && '.join(f'node --check /app/{name}' for name in BRIDGE_FILES)
    (context / 'Dockerfile').write_text(
        f'ARG BRIDGE_BASE_IMAGE\nFROM ${{BRIDGE_BASE_IMAGE}}\n'
        f'COPY --chown=node:node {names} /app/\nRUN {checks}\n',
        encoding='utf-8',
    )
    return {'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            'dependencies': dependencies}


def _public_asset_path(name):
    """仅接受打包器生成的规范相对路径；运行时及私有产物不能由前端包覆盖。"""
    path = PurePosixPath(name)
    parts = tuple(part.casefold() for part in path.parts)
    return (bool(parts) and not path.is_absolute() and path.as_posix() == name
            and '..' not in parts and '\\' not in name and not re.search(r'[\x00-\x1f\x7f]', name)
            and parts[0] != 'pyodide'
            and not any(part in {'.git', 'node_modules', 'frontend-debug', '__pycache__', '.env'}
                        or part.startswith('.env.') for part in parts)
            and not parts[-1].endswith(('.map', '.map.gz', '.map.br', '.key', '.pem')))


def _verify_public_tree(root, expected):
    """按清单逐份验证全部公开文件；包内多出任何文件或链接都停止。"""
    _directory(root)
    pending = [root]
    seen = set()
    file_bytes = 0
    visited_entries = 0
    while pending:
        for item in pending.pop().iterdir():
            visited_entries += 1
            name = item.relative_to(root).as_posix()
            _check(visited_entries <= 2 * MAX_PUBLIC_FILES and not item.is_symlink()
                   and _public_asset_path(name), 'tavern_frontend_asset_path_invalid')
            if item.is_dir():
                pending.append(item)
                continue
            _check(item.is_file() and name in expected, 'tavern_frontend_asset_unlisted')
            with item.open('rb') as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    file_bytes += len(chunk)
                    _check(file_bytes <= MAX_PUBLIC_BYTES, 'tavern_frontend_asset_size_limit')
                    digest.update(chunk)
            _check(digest.hexdigest() == expected[name], 'tavern_frontend_asset_changed')
            seen.add(name)
    _check(seen == expected.keys(), 'tavern_frontend_asset_missing')


def verify_frontend(source: Path, context: Path):
    """只读核对公开镜像 context 的来源及全部文件，返回 manifest/首页摘要供发布读回。"""
    _directory(context)
    _check({item.name for item in context.iterdir()} == {
        '.dockerignore', 'Dockerfile.frontend-i18n', 'frontend-build', 'frontend-build-manifest.json',
    }, 'tavern_frontend_public_context_required')
    overlays = source / 'openwebui-profile-overlay'
    _check(_read_public_file(context / '.dockerignore') == b'frontend-debug/\n'
           and _read_public_file(context / 'Dockerfile.frontend-i18n')
           == _read_public_file(overlays / 'Dockerfile.frontend-i18n'),
           'tavern_frontend_docker_inputs_changed')
    manifest_bytes = _read_public_file(context / 'frontend-build-manifest.json')
    manifest = json.loads(manifest_bytes)
    # 绑定构建入口使用的完整增量顺序；旧 i18n 或只打了侧栏的构建均不能替代。
    _check(isinstance(manifest, dict)
           and manifest.get('upstream_commit') == UPSTREAM_COMMIT
           and manifest.get('feature_order') == list(FRONTEND_FEATURES)
           and manifest.get('preserved_runtime') == ['pyodide/'], 'tavern_frontend_provenance_changed')
    features = {}
    for name in FRONTEND_FEATURES:
        data = _read_public_file(overlays / name / 'manifest.json')
        _check(json.loads(data).get('upstream_commit') == UPSTREAM_COMMIT, 'tavern_frontend_overlay_changed')
        features[name] = hashlib.sha256(data).hexdigest()
    _check(manifest.get('feature_manifests') == features, 'tavern_frontend_overlay_changed')
    expected = manifest.get('files')
    _check(isinstance(expected, dict) and 'index.html' in expected and len(expected) <= MAX_PUBLIC_FILES
           and all(_public_asset_path(name) and isinstance(value, str)
                   and re.fullmatch(r'[a-f0-9]{64}', value) for name, value in expected.items()),
           'tavern_frontend_public_manifest_invalid')
    _verify_public_tree(context / 'frontend-build', expected)
    return {'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
            'index_sha256': expected['index.html']}
