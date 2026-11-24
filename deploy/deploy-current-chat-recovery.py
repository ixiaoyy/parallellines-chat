"""仅在当前完整运行镜像上发布授权恢复前后端，不回退到官方原始镜像。"""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('current_recovery_frontend', Path(__file__).with_name('deploy-frontend-performance.py'))
frontend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend)
base = frontend.base


class CurrentRecoveryRelease(frontend.FrontendOnlyRelease):
    """复用既有前端清单、服务级条件更新和失败恢复，只叠加本次授权目录边界。"""

    def build(self):
        """先离线叠加完整前端，再以它为基线应用精确后端补丁；不读取凭据或运行迁移。"""
        overlay = self.source / 'openwebui-profile-overlay'
        auth = json.loads((overlay / 'frontend-auth-recovery/manifest.json').read_text())
        auth_bytes = (overlay / 'frontend-auth-recovery/manifest.json').read_bytes()
        public = json.loads((base.safe_path(self.s['frontend_context']) / 'frontend-build-manifest.json').read_text())
        base.check(auth.get('feature_order') == ['frontend-performance', 'frontend-bootstrap', 'frontend-auth-recovery'],
                   'recovery_feature_order_changed')
        base.check(public.get('auth_recovery_manifest_sha256') == base.digest(auth_bytes), 'recovery_manifest_changed')
        base.check(public.get('auth_recovery_patch_sha256') == base.digest(
            (overlay / 'frontend-auth-recovery/frontend-auth-recovery.patch').read_bytes()), 'recovery_patch_changed')
        expected_backend = auth['backend']
        base.check(set(expected_backend) == {'routers/openai.py', 'utils/main_model_auth.py'}, 'recovery_backend_scope_changed')
        base.check(self.s['expected_source'].get('backend/open_webui/routers/openai.py') ==
                   expected_backend['routers/openai.py']['before_sha256'], 'recovery_backend_baseline_changed')
        base.check(base.digest((overlay / 'main_model_auth.py').read_bytes()) ==
                   expected_backend['utils/main_model_auth.py']['after_sha256'], 'recovery_helper_changed')
        base.check(not self.run(['docker', 'image', 'ls', '--quiet', self.s['new_image']]), 'release_tag_already_exists')

        # 只调用成熟发布器的构建阶段；中间镜像尚未激活，线上仍使用原版本。
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
        for name in ('Dockerfile.current-recovery', 'apply_model_auth.py', 'main_model_auth.py'):
            target = overlay / name
            base.check(target.is_file() and not target.is_symlink(), 'recovery_source_invalid')
            (context / name).write_bytes(target.read_bytes())
        self.run(['docker', 'build', '--pull=false', '--network=none', '--build-arg',
                  'CHAT_BASE_IMAGE=' + self.frontend_image, '-f', context / 'Dockerfile.current-recovery',
                  '-t', self.s['new_image'], context], timeout=300)
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.frontend_image]) ==
                   frontend_image_id, 'frontend_image_changed')
        self.s['desired_source'] = {**stage.s['desired_source'], **{
            'backend/open_webui/' + name: record['after_sha256'] for name, record in expected_backend.items()}}
        image_id = self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.s['new_image']])
        base.check(re.fullmatch(r'sha256:[a-f0-9]{64}', image_id), 'invalid_recovery_image')
        return context, image_id

    def update(self, service, context):
        """只替换 Chat 镜像和构建配方；模型配置、环境、网络及其他服务保持原样。"""
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        service['image'] = self.s['new_image']
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.current-recovery',
                            'args': {'CHAT_BASE_IMAGE': self.frontend_image}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', help='本次核对的非敏感 Chat 发布范围，不含凭据值')
    args = parser.parse_args()
    os.umask(0o077)
    release = CurrentRecoveryRelease(json.loads(Path(args.scope).read_text()), 'chat')
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'phase': 'chat', 'stage': release.stage,
                          'failure_type': type(error).__name__,
                          'receipt_dir': str(release.run_dir) if release.run_dir else None}), flush=True)
        raise SystemExit(1) from None
