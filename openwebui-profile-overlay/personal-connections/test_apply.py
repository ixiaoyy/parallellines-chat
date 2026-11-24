"""完整个人连接清单应用/恢复边界；仅复制固定本地源码文件，不连接服务。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('personal_apply', HERE / 'apply.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)
BASELINE = Path(os.environ.get('PERSONAL_CONNECTIONS_BASELINE', HERE.parents[1] / '.build/unified-login-20261115/source'))
MANIFEST = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))


class ApplyTests(unittest.TestCase):
    def baseline(self, directory, *, frontend_only=False):
        root = Path(directory)
        for relative, record in MANIFEST['files'].items():
            if frontend_only and relative.startswith('backend/'):
                continue
            if record['before_sha256'] is not None:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((BASELINE / relative).read_bytes())
        return root

    def test_check_does_not_write_and_apply_matches_complete_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.baseline(directory)
            self.assertEqual(len(overlay.apply(root, check=True)), len(MANIFEST['files']))
            for relative, record in MANIFEST['files'].items():
                if record['before_sha256'] is None:
                    self.assertFalse((root / relative).exists())
            overlay.apply(root)
            for relative, record in MANIFEST['files'].items():
                self.assertEqual(hashlib.sha256((root / relative).read_bytes()).hexdigest(), record['after_sha256'])
            with self.assertRaisesRegex(ValueError, 'baseline_changed'):
                overlay.apply(root)

    def test_baseline_drift_rejects_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.baseline(directory)
            target = root / 'backend/open_webui/utils/task.py'
            target.write_text(target.read_text(encoding='utf-8') + '\n# unrelated change\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'baseline_changed'):
                overlay.apply(root)
            self.assertFalse((root / 'backend/open_webui/utils/main_personal_connections.py').exists())

    def test_backend_only_does_not_require_or_touch_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.baseline(directory)
            before = (root / 'src/lib/apis/index.ts').read_bytes()
            changed = overlay.apply(root, False, True)
            self.assertTrue(all(path.startswith('backend/') for path in changed))
            self.assertEqual((root / 'src/lib/apis/index.ts').read_bytes(), before)

    def test_frontend_only_is_read_only_on_check_and_never_needs_backend_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.baseline(directory, frontend_only=True)
            expected = {path: record for path, record in MANIFEST['files'].items() if not path.startswith('backend/')}
            before = {path: (root / path).read_bytes() for path in expected if (root / path).is_file()}
            self.assertEqual(set(overlay.apply(root, check=True, frontend_only=True)), set(expected))
            self.assertEqual({path: (root / path).read_bytes() for path in expected if (root / path).is_file()}, before)
            self.assertEqual(set(overlay.apply(root, frontend_only=True)), set(expected))
            self.assertFalse((root / 'backend').exists())
            for path, record in expected.items():
                self.assertEqual(hashlib.sha256((root / path).read_bytes()).hexdigest(), record['after_sha256'])

    def test_frontend_only_and_backend_only_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, 'personal_overlay_modes_conflict'):
            overlay.apply(Path('unused'), False, True, frontend_only=True)


if __name__ == '__main__':
    unittest.main()
