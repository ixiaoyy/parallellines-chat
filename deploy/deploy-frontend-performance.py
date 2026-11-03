"""仅发布已验证的 Chat 前端；保留 live 基线后端，不合并网络、身份或模型配置。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('frontend_profile_release', Path(__file__).with_name('deploy-profile-sync.py'))
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


class FrontendOnlyRelease(base.Release):
    """沿用已有 CAS、私有备份、就绪检查与恢复，仅替换目标 Chat 的 image/build。"""

    def build(self):
        """使用已打包的公开目录叠加当前镜像 ID；拒绝调试目录、任意 Dockerfile 和不完整清单。"""
        base.check(self.phase == 'chat' and self.s['source_root'] == '/app' and self.s['interpreter'] == 'python',
                   'frontend_chat_scope_required')
        context = base.safe_path(self.s['frontend_context'])
        base.check({p.name for p in context.iterdir()} == {
            'frontend-build', 'frontend-build-manifest.json', '.dockerignore', 'Dockerfile.performance'},
            'public_frontend_context_only')
        for name in ('frontend-build-manifest.json', '.dockerignore', 'Dockerfile.performance'):
            base.check((context / name).is_file() and not (context / name).is_symlink(), 'invalid_frontend_context_file')
        assets = context / 'frontend-build'
        base.check(assets.is_dir() and not assets.is_symlink(), 'invalid_frontend_assets')
        dockerfile = self.source / 'openwebui-profile-overlay/Dockerfile.performance'
        base.check((context / 'Dockerfile.performance').read_bytes() == dockerfile.read_bytes(), 'frontend_dockerfile_changed')
        manifest_bytes = (context / 'frontend-build-manifest.json').read_bytes()
        base.check(base.digest(manifest_bytes) == self.s['frontend_manifest_sha256'], 'frontend_manifest_changed')
        manifest = json.loads(manifest_bytes)
        base.check(manifest.get('upstream_commit') == 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca', 'frontend_upstream_changed')
        patch = self.source / 'openwebui-profile-overlay/frontend-performance/frontend-performance.patch'
        base.check(manifest.get('performance_patch_sha256') == base.digest(patch.read_bytes()), 'frontend_patch_mismatch')
        expected = manifest.get('files', {})
        base.check(expected and 'index.html' in expected and all(
            not name.endswith('.map') and not name.startswith('pyodide/') for name in expected), 'public_frontend_manifest_required')
        actual = {}
        for path in assets.rglob('*'):
            base.check(not path.is_symlink(), 'frontend_symlink_rejected')
            if path.is_file():
                actual[path.relative_to(assets).as_posix()] = base.digest(path.read_bytes())
        base.check(actual == expected, 'frontend_artifact_changed')
        self.manifest_sha = self.s['frontend_manifest_sha256']
        base_tag = self.s['new_image'] + '-base'
        for tag in (self.s['new_image'], base_tag):
            base.check(not self.run(['docker', 'image', 'ls', '--quiet', tag]), 'image_tag_exists')
        self.run(['docker', 'image', 'tag', self.s['expected_image_id'], base_tag])
        self.run(['docker', 'build', '--pull=false', '--network=none', '--build-arg', 'CHAT_BASE_IMAGE=' + base_tag,
                  '-f', context / 'Dockerfile.performance', '-t', self.s['new_image'], context], timeout=300)
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag]) == self.s['expected_image_id'],
                   'base_image_changed')
        # 继承已核对的后端/Pyodide 源文件基线；仅将前端首页预期值替换为本次发布结果。
        self.s['desired_source'] = {**self.s['expected_source'], 'build/index.html': expected['index.html']}
        return context, self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.s['new_image']])

    def update(self, service, context):
        """只改 image/build；环境、env_file、挂载、extra_hosts、网络和免费模型开关均原样保留。"""
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        service['image'] = self.s['new_image']
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.performance',
                            'args': {'CHAT_BASE_IMAGE': self.s['new_image'] + '-base'}}

    def healthy(self):
        """沿用容器就绪；激活后读回完整公开清单并确认编译映射已移除，不访问模型。"""
        state = super().healthy()
        if self.stage == 'health':
            code = (
                'import hashlib,json;from pathlib import Path\n'
                'root=Path("/app/build");p=Path("/app/frontend-performance-manifest.json")\n'
                'assert hashlib.sha256(p.read_bytes()).hexdigest()==' + repr(self.manifest_sha) + '\n'
                'manifest=json.loads(p.read_text())\n'
                'for name,expected in manifest["files"].items():\n'
                ' target=root/name\n'
                ' assert target.resolve().is_relative_to(root), "invalid_asset_path"\n'
                ' assert hashlib.sha256(target.read_bytes()).hexdigest()==expected, "asset_changed"\n'
                'assert not any((root/"_app").rglob("*.map")), "compiled_source_maps_present"\n'
                'print(json.dumps({"files_verified":len(manifest["files"]),"compiled_maps":0}))\n'
            )
            self.delivery = json.loads(self.run(['docker', 'exec', self.s['container'], 'python', '-c', code], timeout=90))
        return state

    def deploy(self):
        """正常激活/恢复完成后补充前端回执，不执行其他阶段或原生配置写入。"""
        super().deploy()
        receipt = self.run_dir / 'receipt.json'
        result = json.loads(receipt.read_text())
        result.update({'frontend_delivery': self.delivery, 'frontend_manifest_sha256': self.manifest_sha,
                       'environment_changed': False, 'network_changed': False, 'native_model_configuration_changed': False})
        receipt.write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', help='已审核的服务器非敏感 scope；沿用现有项目/服务/路径，不含凭证值')
    args = parser.parse_args()
    os.umask(0o077)
    release = FrontendOnlyRelease(json.loads(Path(args.scope).read_text()), 'chat')
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'phase': 'chat', 'stage': release.stage,
                          'failure_type': type(error).__name__, 'receipt_dir': str(release.run_dir) if release.run_dir else None}), flush=True)
        raise SystemExit(1) from None
