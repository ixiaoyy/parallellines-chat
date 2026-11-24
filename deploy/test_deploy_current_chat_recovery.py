"""只验证本次增量发布接线与旧能力保留，不调用 Docker、服务器或模型。"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('current_chat_recovery', Path(__file__).with_name('deploy-current-chat-recovery.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CurrentRecoveryTests(unittest.TestCase):
    def candidate(self, root):
        """建立最小明确基线，不使用本机配置、凭据或运行容器。"""
        release = object.__new__(module.CurrentRecoveryRelease)
        release.phase = 'chat'
        release.source = root / 'source'
        release.receipts = root / 'receipts'
        release.run_dir = root / 'receipt'
        release.run_dir.mkdir()
        overlay = release.source / 'openwebui-profile-overlay'
        feature = overlay / 'frontend-auth-recovery'
        feature.mkdir(parents=True)
        helper = b'recovery helper'
        auth = {'feature_order': ['frontend-performance', 'frontend-bootstrap', 'frontend-auth-recovery'],
                'backend': {'routers/openai.py': {'before_sha256': 'old-openai', 'after_sha256': 'new-openai'},
                            'utils/main_model_auth.py': {'before_sha256': None, 'after_sha256': module.base.digest(helper)}}}
        data = json.dumps(auth).encode()
        (feature / 'manifest.json').write_bytes(data)
        (feature / 'frontend-auth-recovery.patch').write_bytes(b'auth patch')
        (overlay / 'main_model_auth.py').write_bytes(helper)
        (overlay / 'apply_model_auth.py').write_bytes(b'applier')
        (overlay / 'Dockerfile.current-recovery').write_bytes(b'ARG CHAT_BASE_IMAGE\nFROM ${CHAT_BASE_IMAGE}\n')
        context = root / 'frontend'
        context.mkdir()
        public = {'auth_recovery_manifest_sha256': module.base.digest(data),
                  'auth_recovery_patch_sha256': module.base.digest(b'auth patch')}
        (context / 'frontend-build-manifest.json').write_text(json.dumps(public))
        release.s = {'frontend_context': str(context), 'expected_source': {
            'backend/open_webui/routers/openai.py': 'old-openai', 'backend/open_webui/utils/chat.py': 'free-guard'},
            'new_image': 'chat:recovery', 'expected_compose_image': 'chat:live'}
        release.commands = []
        def run(args, **kwargs):
            release.commands.append(args)
            if args[1:3] == ['image', 'inspect']:
                return 'sha256:' + ('2' if args[-1].endswith('-frontend') else '3') * 64
            return ''
        release.run = run
        return release, context

    def test_build_preserves_complete_frontend_backend_and_is_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            release, context = self.candidate(Path(directory))
            class FrontendStage:
                def __init__(self, scope, phase):
                    self.s = scope['chat']
                    self.manifest_sha = 'public-manifest'
                def build(self):
                    self.s['desired_source'] = {**self.s['expected_source'], 'build/index.html': 'new-index'}
                    return context, 'sha256:' + '2' * 64
            with patch.object(module.frontend, 'FrontendOnlyRelease', FrontendStage):
                actual_context, image_id = release.build()
            self.assertEqual(image_id, 'sha256:' + '3' * 64)
            self.assertEqual(release.s['new_image'], 'chat:recovery')
            self.assertEqual(release.s['desired_source']['backend/open_webui/utils/chat.py'], 'free-guard')
            self.assertEqual(release.s['desired_source']['backend/open_webui/routers/openai.py'], 'new-openai')
            self.assertEqual(release.s['desired_source']['build/index.html'], 'new-index')
            self.assertIn('backend/open_webui/utils/main_model_auth.py', release.s['desired_source'])
            build = next(args for args in release.commands if args[1] == 'build')
            self.assertIn('--network=none', build)
            self.assertIn('--pull=false', build)
            self.assertIn('CHAT_BASE_IMAGE=chat:recovery-frontend', build)
            self.assertEqual({p.name for p in actual_context.iterdir()},
                             {'Dockerfile.current-recovery', 'apply_model_auth.py', 'main_model_auth.py'})

    def test_unreviewed_helper_or_baseline_stops_before_any_docker_command(self):
        for damage in ('helper', 'baseline'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                release, _ = self.candidate(Path(directory))
                if damage == 'helper':
                    (release.source / 'openwebui-profile-overlay/main_model_auth.py').write_bytes(b'drift')
                else:
                    release.s['expected_source']['backend/open_webui/routers/openai.py'] = 'another-baseline'
                with self.assertRaises(RuntimeError):
                    release.build()
                self.assertEqual(release.commands, [])

    def test_update_preserves_model_environment_network_and_other_service_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            release, context = self.candidate(Path(directory))
            release.frontend_image = 'chat:recovery-frontend'
            service = {'image': 'chat:live', 'environment': {'KEEP': 'unchanged'},
                       'volumes': ['existing:/app/backend/data'], 'networks': ['existing'],
                       'extra_hosts': ['pingxingxian.space:host-gateway'], 'cpus': 1.0}
            before = copy.deepcopy(service)
            release.update(service, context)
            self.assertEqual({k: v for k, v in service.items() if k not in ('image', 'build')},
                             {k: v for k, v in before.items() if k not in ('image', 'build')})
            drift = copy.deepcopy(before)
            drift['image'] = 'other:live'
            with self.assertRaises(RuntimeError):
                release.update(drift, context)
            self.assertEqual(drift['image'], 'other:live')


if __name__ == '__main__':
    unittest.main()
