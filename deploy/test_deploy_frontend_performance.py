"""仅验证新前端发布器的局部更新/公开输入合同；不运行 Docker、网络或业务测试。"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('frontend_only_release', Path(__file__).with_name('deploy-frontend-performance.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FrontendOnlyTests(unittest.TestCase):
    def candidate(self, root):
        release = object.__new__(module.FrontendOnlyRelease)
        release.phase = 'chat'
        release.source = root / 'source'
        context = root / 'context'
        (release.source / 'openwebui-profile-overlay/frontend-performance').mkdir(parents=True)
        (context / 'frontend-build').mkdir(parents=True)
        dockerfile = b'ARG CHAT_BASE_IMAGE\nFROM ${CHAT_BASE_IMAGE}\nCOPY frontend-build/ /app/build/\n'
        (release.source / 'openwebui-profile-overlay/Dockerfile.performance').write_bytes(dockerfile)
        (context / 'Dockerfile.performance').write_bytes(dockerfile)
        patch = b'fixed performance patch'
        (release.source / 'openwebui-profile-overlay/frontend-performance/frontend-performance.patch').write_bytes(patch)
        (context / '.dockerignore').write_text('frontend-debug/\n')
        (context / 'frontend-build/index.html').write_bytes(b'frontend')
        manifest = {'upstream_commit': 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca',
                    'performance_patch_sha256': module.base.digest(patch), 'files': {'index.html': module.base.digest(b'frontend')}}
        data = json.dumps(manifest).encode()
        (context / 'frontend-build-manifest.json').write_bytes(data)
        release.s = {'frontend_context': str(context), 'source_root': '/app', 'interpreter': 'python',
                     'frontend_manifest_sha256': module.base.digest(data), 'new_image': 'chat:frontend-new',
                     'expected_compose_image': 'chat:live', 'expected_image_id': 'sha256:' + '1' * 64,
                     'expected_source': {'build/index.html': 'old-index', 'backend/open_webui/main.py': 'backend-unchanged'}}
        release.commands = []
        def run(args, **kwargs):
            release.commands.append(args)
            if args[1:3] == ['image', 'ls']: return ''
            if args[1:3] == ['image', 'inspect']:
                return release.s['expected_image_id'] if args[-1].endswith('-base') else 'sha256:' + '2' * 64
            return ''
        release.run = run
        return release, context

    def test_update_changes_only_image_and_build_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            release, context = self.candidate(Path(directory))
            service = {'image': 'chat:live', 'environment': {'CHAT_OPENROUTER_FREE_ENABLED': 'true', 'CUSTOM': 'preserved'},
                       'env_file': ['/data/private.env'], 'volumes': ['existing:/app/backend/data'],
                       'extra_hosts': ['pingxingxian.space:host-gateway'], 'networks': ['existing'], 'cpus': 1.0}
            before = copy.deepcopy(service)
            release.update(service, context)
            self.assertEqual({k: v for k, v in service.items() if k not in ('image', 'build')},
                             {k: v for k, v in before.items() if k not in ('image', 'build')})
            self.assertEqual(service['build']['dockerfile'], 'Dockerfile.performance')
            drift = copy.deepcopy(before); drift['image'] = 'another:live'
            frozen = copy.deepcopy(drift)
            with self.assertRaises(RuntimeError): release.update(drift, context)
            self.assertEqual(drift, frozen)

    def test_build_uses_live_image_offline_and_preserves_backend_expectations(self):
        with tempfile.TemporaryDirectory() as directory:
            release, context = self.candidate(Path(directory))
            actual_context, image = release.build()
            self.assertEqual(actual_context, context)
            self.assertEqual(image, 'sha256:' + '2' * 64)
            command = next(args for args in release.commands if args[1] == 'build')
            self.assertIn('--network=none', command)
            self.assertIn('--pull=false', command)
            self.assertIn('CHAT_BASE_IMAGE=chat:frontend-new-base', command)
            self.assertEqual(release.s['desired_source']['backend/open_webui/main.py'], 'backend-unchanged')
            self.assertEqual(release.s['desired_source']['build/index.html'], module.base.digest(b'frontend'))

    def test_private_context_or_changed_dockerfile_stops_before_docker(self):
        for damage in ('private-directory', 'changed-dockerfile'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                release, context = self.candidate(Path(directory))
                if damage == 'private-directory': (context / 'frontend-debug').mkdir()
                else: (context / 'Dockerfile.performance').write_text('FROM unapproved\n')
                with self.assertRaises(RuntimeError): release.build()
                self.assertEqual(release.commands, [])


if __name__ == '__main__': unittest.main()
