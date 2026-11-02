"""仅验证新增薄发布器的构建/局部更新边界，不执行 Docker、网络或数据库操作。"""
import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('free_release', Path(__file__).with_name('deploy-openrouter-free.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FreeReleaseTests(unittest.TestCase):
    def candidate(self, root):
        value = object.__new__(MODULE.FreeChatRelease)
        value.source = root
        value.run_dir = root
        value.s = {'new_image': 'test:free', 'source_commit': 'a' * 40,
                   'expected_image_id': 'sha256:' + '1' * 64, 'expected_compose_image': 'test:old'}
        value.run = lambda args: '' if args[1:3] == ['image', 'ls'] else (
            value.s['expected_image_id'] if args[-1] == 'test:free-base' else 'sha256:' + '2' * 64
        )
        return value

    def test_update_preserves_all_nonrelease_settings(self):
        release = self.candidate(Path('/data/source'))
        service = {'image': 'test:old', 'environment': {'TASK_MODEL_EXTERNAL': 'paid-existing'},
                   'volumes': ['existing:/app/backend/data'], 'networks': ['existing'], 'restart': 'unless-stopped'}
        before = copy.deepcopy(service)
        release.update(service, Path('/data/source/openwebui-profile-overlay'))
        self.assertEqual({k: v for k, v in service.items() if k not in ('image', 'build')},
                         {k: v for k, v in before.items() if k not in ('image', 'build')})
        self.assertEqual(service['build']['dockerfile'], 'Dockerfile.openrouter-free')

    def test_build_uses_current_base_and_native_five_file_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            release = self.candidate(Path(directory))
            output = '\n'.join('PL_FREE_TASK_SOURCE_SHA256=' + name + ':' + 'b' * 64
                               for name in sorted(MODULE.SOURCE_FILES)).encode()
            with patch.object(MODULE.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, b'')) as run:
                _, image = release.build()
            self.assertEqual(set(release.s['desired_source']), MODULE.SOURCE_FILES)
            self.assertEqual(image, 'sha256:' + '2' * 64)
            command = run.call_args.args[0]
            self.assertIn('--network=none', command)
            self.assertIn('--pull=false', command)
            self.assertIn('CHAT_BASE_IMAGE=test:free-base', command)

    def test_incomplete_or_conflicting_build_receipt_stops(self):
        for output in (b'PL_FREE_TASK_SOURCE_SHA256=utils/task.py:' + b'b' * 64,
                       b'PL_FREE_TASK_SOURCE_SHA256=unexpected.py:' + b'b' * 64):
            with self.subTest(output=output[:45]), tempfile.TemporaryDirectory() as directory:
                release = self.candidate(Path(directory))
                with patch.object(MODULE.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, b'')):
                    with self.assertRaises(RuntimeError):
                        release.build()


if __name__ == '__main__':
    unittest.main()
