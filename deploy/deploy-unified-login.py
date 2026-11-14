"""在已核当前镜像上增量交付统一登录；仅 root 操作者在授权服务器执行。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location('unified_frontend_release', Path(__file__).with_name('deploy-frontend-performance.py'))
frontend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend)
base = frontend.base
ORDER = ['frontend-performance', 'frontend-bootstrap', 'frontend-auth-recovery', 'frontend-silent-recovery', 'unified-login']
BRIDGE = ['config.mjs', 'identity-provider.mjs', 'main-bridge.mjs', 'sqlite-adapter.mjs', 'rp-session.mjs']
OVERLAY = ['apply.py', 'main_unified_login.py', 'manifest.json', 'start.sh']


class UnifiedRelease(frontend.FrontendOnlyRelease):
    """复用服务级 CAS、私有备份、健康和条件恢复，只替换当前 Chat 或共享发行方。"""

    def build(self):
        """构建前校验正式上下文与前置源；不安装依赖、不读密钥、不调用模型。"""
        s = self.s
        base.check(self.phase in ('chat', 'bridge') and s['source_root'] == '/app', 'unified_scope_required')
        base_tag = s['new_image'] + '-base'
        for tag in (s['new_image'], base_tag):
            base.check(not self.run(['docker', 'image', 'ls', '--quiet', tag]), 'release_tag_already_exists')
        self.run(['docker', 'image', 'tag', s['expected_image_id'], base_tag])
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag]) == s['expected_image_id'], 'base_alias_mismatch')
        if self.phase == 'bridge':
            # 现有依赖和 SQLite 挂载不变；只增加本次五个发行方源文件。
            base.check(s['interpreter'] == 'node', 'bridge_node_required')
            for name in ('package.json', 'package-lock.json'):
                base.check(s['expected_source'].get(name) == base.digest((self.source / 'identity-bridge' / name).read_bytes()), 'bridge_dependencies_changed')
            context = self.run_dir / 'bridge-build'
            context.mkdir(mode=0o700)
            for name in BRIDGE:
                path = self.source / 'identity-bridge' / name
                base.check(path.is_file() and not path.is_symlink(), 'invalid_bridge_source')
                (context / name).write_bytes(path.read_bytes())
            checks = ' && '.join('node --check /app/' + name for name in BRIDGE)
            dockerfile = 'ARG BRIDGE_BASE_IMAGE\nFROM ${BRIDGE_BASE_IMAGE}\nCOPY --chown=node:node ' + ' '.join(BRIDGE) + ' /app/\nRUN ' + checks + '\n'
            (context / 'Dockerfile').write_text(dockerfile)
            args = ['BRIDGE_BASE_IMAGE=' + base_tag]
            dockerfile_name = 'Dockerfile'
            self.s['desired_source'] = {**s['expected_source'], **{name: base.digest((context / name).read_bytes()) for name in BRIDGE}}
        else:
            base.check(s['interpreter'] == 'python', 'chat_python_required')
            context = base.safe_path(s['frontend_context'])
            base.check({p.name for p in context.iterdir()} == {'frontend-build', 'frontend-build-manifest.json', '.dockerignore', 'Dockerfile.unified-login', 'unified-login'}, 'unified_context_scope_changed')
            feature = self.source / 'openwebui-profile-overlay/unified-login'
            manifest_bytes = (feature / 'manifest.json').read_bytes()
            manifest = json.loads(manifest_bytes)
            base.check(manifest['feature_order'] == ORDER, 'unified_feature_order_changed')
            overlay = context / 'unified-login'
            base.check({p.name for p in overlay.iterdir()} == set(OVERLAY), 'unified_overlay_scope_changed')
            for name in OVERLAY:
                base.check((overlay / name).is_file() and not (overlay / name).is_symlink() and (overlay / name).read_bytes() == (feature / name).read_bytes(), 'unified_overlay_changed')
            base.check((context / 'Dockerfile.unified-login').read_bytes() == (self.source / 'openwebui-profile-overlay/Dockerfile.unified-login').read_bytes(), 'unified_dockerfile_changed')
            public_bytes = (context / 'frontend-build-manifest.json').read_bytes()
            base.check(base.digest(public_bytes) == s['frontend_manifest_sha256'], 'unified_public_manifest_changed')
            public = json.loads(public_bytes)
            base.check(public.get('feature_order') == ORDER and public.get('upstream_commit') == manifest['upstream_commit'], 'unified_frontend_provenance_changed')
            inputs = public.get('feature_manifests', {})
            for name in ORDER[:-1]:
                base.check(inputs.get(name) == base.digest((self.source / 'openwebui-profile-overlay' / name / 'manifest.json').read_bytes()), 'unified_frontend_prerequisite_changed')
            overlay_hash = base.digest(manifest_bytes)
            if inputs.get('unified-login') != overlay_hash:
                # 仅复用明确记录的后端修正；保留原编译输入，不能把 metadata 更新说成前端重构建。
                reuse = public.get('frontend_reuse', {})
                expected_frontend = {name: record['after_sha256'] for name, record in manifest['files'].items() if name.startswith('src/')}
                base.check(public.get('backend_overlay_manifest_sha256') == overlay_hash and reuse.get('rebuilt') is False and reuse.get('reason') == 'backend-only-oidc-userinfo-authority' and reuse.get('build_unified_manifest_sha256') == inputs.get('unified-login') and reuse.get('frontend_source_after_sha256') == expected_frontend, 'unified_frontend_reuse_not_proven')
            expected = public.get('files', {})
            base.check(expected and 'index.html' in expected and all(not name.endswith('.map') and not name.startswith('pyodide/') and not Path(name).is_absolute() and '..' not in Path(name).parts for name in expected), 'unified_public_assets_required')
            actual = {}
            for path in (context / 'frontend-build').rglob('*'):
                base.check(not path.is_symlink(), 'unified_asset_symlink_rejected')
                if path.is_file():
                    actual[path.relative_to(context / 'frontend-build').as_posix()] = base.digest(path.read_bytes())
            base.check(actual == expected, 'unified_frontend_bytes_changed')
            desired = {**s['expected_source'], 'build/index.html': expected['index.html'], 'frontend-performance-manifest.json': s['frontend_manifest_sha256'], 'backend/unified-start.sh': base.digest((feature / 'start.sh').read_bytes())}
            for name, record in manifest['files'].items():
                if name.startswith('backend/'):
                    if record['before_sha256'] is not None:
                        base.check(s['expected_source'].get(name) == record['before_sha256'], 'unified_backend_before_scope_changed')
                    desired[name] = record['after_sha256']
            self.s['desired_source'] = desired
            self.manifest_sha = s['frontend_manifest_sha256']
            args = ['CHAT_BASE_IMAGE=' + base_tag]
            dockerfile_name = 'Dockerfile.unified-login'
        self.run(['docker', 'build', '--pull=false', '--network=none', '--build-arg', args[0], '-f', context / dockerfile_name, '-t', s['new_image'], context], timeout=300)
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag]) == s['expected_image_id'], 'base_alias_changed')
        return context, self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', s['new_image']])

    def update(self, service, context):
        """只改目标 image/build 和明确非敏感开关；保留 env_file、凭据、数据、网络和模型配置。"""
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        if self.phase == 'chat':
            base.check(service.get('command') is None and service.get('entrypoint') is None, 'custom_command_requires_review')
        service['image'] = self.s['new_image']
        argument = 'CHAT_BASE_IMAGE' if self.phase == 'chat' else 'BRIDGE_BASE_IMAGE'
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.unified-login' if self.phase == 'chat' else 'Dockerfile', 'args': {argument: self.s['new_image'] + '-base'}}
        if 'environment' not in service:
            service['environment'] = {}
        base.check(isinstance(service['environment'], dict), 'environment_mapping_required')
        service['environment'].update({'UNIFIED_AUTH_ENABLED': 'true'} if self.phase == 'bridge' else {'MAIN_UNIFIED_LOGIN_ENABLED': 'true', 'WEBUI_AUTH': 'true', 'OAUTH_AUTO_REDIRECT': 'false', 'MAIN_UNIFIED_RECOVERY_ENABLED': 'false'})

    def healthy(self):
        """Chat 沿既有完整公开清单读回，发行方沿既有单服务健康；不扩大到模型请求。"""
        return super().healthy() if self.phase == 'chat' else base.Release.healthy(self)

    def deploy(self):
        """沿用现有条件激活/恢复，回执明确本次只改变非敏感登录开关。"""
        base.Release.deploy(self)
        path = self.run_dir / 'receipt.json'
        result = json.loads(path.read_text())
        result.update({'environment_changed': True, 'network_changed': False, 'native_model_configuration_changed': False})
        if self.phase == 'chat':
            result.update({'frontend_delivery': self.delivery, 'frontend_manifest_sha256': self.manifest_sha})
        path.write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', help='服务器端已审核非敏感 scope，不含凭据值；保留原项目与服务')
    parser.add_argument('phase', choices=['bridge', 'chat'])
    args = parser.parse_args()
    os.umask(0o077)
    release = UnifiedRelease(json.loads(Path(args.scope).read_text()), args.phase)
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'phase': args.phase, 'stage': release.stage, 'failure_type': type(error).__name__, 'receipt_dir': str(release.run_dir) if release.run_dir else None}), flush=True)
        raise SystemExit(1) from None
