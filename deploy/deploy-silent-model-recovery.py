"""在已发布的完整 Chat 镜像上增量交付静默模型授权；不改主站或身份桥。"""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re

spec = importlib.util.spec_from_file_location('silent_recovery_frontend', Path(__file__).with_name('deploy-frontend-performance.py'))
frontend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend)
base = frontend.base


class SilentRecoveryRelease(frontend.FrontendOnlyRelease):
    """沿用服务级条件更新、完整清单与健康恢复，只叠加当前静默恢复配方。"""

    def build(self):
        """先在当前镜像上构建前端，再离线应用两个后端接线及一个新模块。"""
        overlay = self.source / 'openwebui-profile-overlay'
        feature = overlay / 'frontend-silent-recovery'
        manifest_bytes = (feature / 'manifest.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        public = json.loads((base.safe_path(self.s['frontend_context']) / 'frontend-build-manifest.json').read_text())
        order = ['frontend-performance', 'frontend-bootstrap', 'frontend-auth-recovery', 'frontend-silent-recovery']
        base.check(manifest.get('feature_order') == order and public.get('feature_order') == order,
                   'silent_feature_order_changed')
        base.check(public.get('silent_recovery_manifest_sha256') == base.digest(manifest_bytes), 'silent_manifest_changed')
        base.check(public.get('silent_recovery_patch_sha256') == base.digest(
            (feature / 'frontend-silent-recovery.patch').read_bytes()), 'silent_patch_changed')
        base.check(public.get('auth_recovery_manifest_sha256') == base.digest(
            (overlay / 'frontend-auth-recovery/manifest.json').read_bytes()), 'manual_recovery_baseline_changed')
        backend = manifest['backend']
        base.check(set(backend) == {'main.py', 'utils/oauth.py', 'utils/main_model_silent_recovery.py'},
                   'silent_backend_scope_changed')
        for relative, record in backend.items():
            path = 'backend/open_webui/' + relative
            if record['before_sha256'] is None:
                base.check(path not in self.s['expected_source'], 'silent_helper_already_present')
            else:
                base.check(self.s['expected_source'].get(path) == record['before_sha256'], 'silent_backend_baseline_changed')
        base.check(base.digest((overlay / 'main_model_silent_recovery.py').read_bytes()) ==
                   backend['utils/main_model_silent_recovery.py']['after_sha256'], 'silent_helper_changed')
        base.check(not self.run(['docker', 'image', 'ls', '--quiet', self.s['new_image']]), 'release_tag_already_exists')

        # 复用现有前端交付的全部校验；中间镜像不激活，失败时线上仍保持原版本。
        frontend_scope = copy.deepcopy(self.s)
        frontend_scope['new_image'] = self.s['new_image'] + '-frontend'
        stage = frontend.FrontendOnlyRelease({'source': str(self.source), 'receipts': str(self.receipts),
                                             'chat': frontend_scope}, 'chat')
        stage.run_dir = self.run_dir
        _, frontend_image_id = stage.build()
        self.frontend_image = frontend_scope['new_image']
        self.manifest_sha = stage.manifest_sha
        context = self.run_dir / 'backend-build'
        context.mkdir(mode=0o700)
        sources = {'Dockerfile.silent-recovery': overlay / 'Dockerfile.silent-recovery',
                   'apply_backend.py': feature / 'apply_backend.py',
                   'main_model_silent_recovery.py': overlay / 'main_model_silent_recovery.py'}
        for name, source in sources.items():
            base.check(source.is_file() and not source.is_symlink(), 'silent_source_invalid')
            (context / name).write_bytes(source.read_bytes())
        self.run(['docker', 'build', '--pull=false', '--network=none', '--build-arg',
                  'CHAT_BASE_IMAGE=' + self.frontend_image, '-f', context / 'Dockerfile.silent-recovery',
                  '-t', self.s['new_image'], context], timeout=300)
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.frontend_image]) ==
                   frontend_image_id, 'frontend_image_changed')
        self.s['desired_source'] = {**stage.s['desired_source'], **{
            'backend/open_webui/' + name: record['after_sha256'] for name, record in backend.items()}}
        image_id = self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.s['new_image']])
        base.check(re.fullmatch(r'sha256:[a-f0-9]{64}', image_id), 'invalid_silent_recovery_image')
        return context, image_id

    def update(self, service, context):
        """仅改 Chat 镜像/构建，保留原生模型、JWT、安全头、网络和全部其他服务。"""
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        service['image'] = self.s['new_image']
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.silent-recovery',
                            'args': {'CHAT_BASE_IMAGE': self.frontend_image}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', help='本次已核对的非敏感 Chat 发布范围，不含任何凭据值')
    args = parser.parse_args()
    os.umask(0o077)
    release = SilentRecoveryRelease(json.loads(Path(args.scope).read_text()), 'chat')
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'phase': 'chat', 'stage': release.stage,
                          'failure_type': type(error).__name__,
                          'receipt_dir': str(release.run_dir) if release.run_dir else None}), flush=True)
        raise SystemExit(1) from None
