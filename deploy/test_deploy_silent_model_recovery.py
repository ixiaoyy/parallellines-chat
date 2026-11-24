"""验证静默恢复发布配方的准确边界，不执行 Docker、网络或业务请求。"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('silent_model_release', Path(__file__).with_name('deploy-silent-model-recovery.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SilentReleaseTests(unittest.TestCase):
    def candidate(self, root):
        """使用无身份资料和凭据的合成清单建立测试基线。"""
        release = object.__new__(module.SilentRecoveryRelease)
        release.source = root / 'source'
        release.receipts = root / 'receipts'
        release.run_dir = root / 'receipt'
        release.run_dir.mkdir()
        overlay = release.source / 'openwebui-profile-overlay'
        feature = overlay / 'frontend-silent-recovery'
        feature.mkdir(parents=True)
        (overlay / 'frontend-auth-recovery').mkdir()
        (overlay / 'frontend-auth-recovery/manifest.json').write_bytes(b'{"manual":"preserved"}')
        helper = b'silent helper'
        order = ['frontend-performance', 'frontend-bootstrap', 'frontend-auth-recovery', 'frontend-silent-recovery']
        manifest = {'feature_order': order, 'backend': {
            'main.py': {'before_sha256': 'old-main', 'after_sha256': 'new-main'},
            'utils/oauth.py': {'before_sha256': 'old-oauth', 'after_sha256': 'new-oauth'},
            'utils/main_model_silent_recovery.py': {'before_sha256': None, 'after_sha256': module.base.digest(helper)}}}
        content = json.dumps(manifest).encode()
        (feature / 'manifest.json').write_bytes(content)
        (feature / 'frontend-silent-recovery.patch').write_bytes(b'silent patch')
        (overlay / 'main_model_silent_recovery.py').write_bytes(helper)
        (feature / 'apply_backend.py').write_bytes(b'applier')
        (overlay / 'Dockerfile.silent-recovery').write_bytes(b'ARG CHAT_BASE_IMAGE\nFROM ${CHAT_BASE_IMAGE}\n')
        context = root / 'frontend'
        context.mkdir()
        (context / 'frontend-build-manifest.json').write_text(json.dumps({
            'feature_order': order, 'silent_recovery_manifest_sha256': module.base.digest(content),
            'silent_recovery_patch_sha256': module.base.digest(b'silent patch'),
            'auth_recovery_manifest_sha256': module.base.digest(b'{"manual":"preserved"}')}))
        release.s = {'frontend_context': str(context), 'expected_source': {
            'backend/open_webui/main.py': 'old-main', 'backend/open_webui/utils/oauth.py': 'old-oauth',
            'backend/open_webui/routers/openai.py': 'manual-responses-free', 'build/pyodide/pyodide-lock.json': 'runtime'},
            'new_image': 'chat:silent', 'expected_compose_image': 'chat:live'}
        release.commands = []
        def run(args, **kwargs):
            release.commands.append(args)
            if args[1:3] == ['image', 'inspect']:
                return 'sha256:' + ('2' if args[-1].endswith('-frontend') else '3') * 64
            return ''
        release.run = run
        return release, context

    def test_exact_new_backend_and_frontend_preserve_all_unmodified_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            release, context = self.candidate(Path(directory))
            class FrontendStage:
                def __init__(self, scope, phase):
                    self.s = scope['chat']
                    self.manifest_sha = 'frontend'
                def build(self):
                    self.s['desired_source'] = {**self.s['expected_source'], 'build/index.html': 'new-index'}
                    return context, 'sha256:' + '2' * 64
            with patch.object(module.frontend, 'FrontendOnlyRelease', FrontendStage):
                actual, image = release.build()
            self.assertEqual(image, 'sha256:' + '3' * 64)
            self.assertEqual(release.s['desired_source']['backend/open_webui/main.py'], 'new-main')
            self.assertEqual(release.s['desired_source']['backend/open_webui/utils/oauth.py'], 'new-oauth')
            self.assertEqual(release.s['desired_source']['backend/open_webui/routers/openai.py'], 'manual-responses-free')
            self.assertEqual(release.s['desired_source']['build/pyodide/pyodide-lock.json'], 'runtime')
            self.assertEqual({p.name for p in actual.iterdir()},
                             {'Dockerfile.silent-recovery', 'apply_backend.py', 'main_model_silent_recovery.py'})
            command = next(c for c in release.commands if c[1] == 'build')
            self.assertIn('--network=none', command)
            self.assertIn('--pull=false', command)
            self.assertIn('CHAT_BASE_IMAGE=chat:silent-frontend', command)

    def test_baseline_drift_or_helper_change_rejected_before_docker(self):
        for damage in ('main', 'oauth', 'helper'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                release, _ = self.candidate(Path(directory))
                if damage == 'helper':
                    (release.source / 'openwebui-profile-overlay/main_model_silent_recovery.py').write_bytes(b'drift')
                else:
                    relative = 'main.py' if damage == 'main' else 'utils/oauth.py'
                    release.s['expected_source']['backend/open_webui/' + relative] = 'other'
                with self.assertRaises(RuntimeError): release.build()
                self.assertEqual(release.commands, [])

    def test_service_update_preserves_native_configuration_network_and_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            release, context = self.candidate(Path(directory))
            release.frontend_image = 'chat:silent-frontend'
            service = {'image': 'chat:live', 'environment': {'JWT_LIFETIME': 'unchanged'},
                       'env_file': ['/data/private.env'], 'networks': ['existing'], 'mem_limit': '1.5g'}
            before = copy.deepcopy(service)
            release.update(service, context)
            self.assertEqual({k: v for k, v in service.items() if k not in ('image', 'build')},
                             {k: v for k, v in before.items() if k not in ('image', 'build')})
            self.assertEqual(service['build']['dockerfile'], 'Dockerfile.silent-recovery')


if __name__ == '__main__': unittest.main()
