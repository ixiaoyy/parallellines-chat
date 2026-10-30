import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from apply import INDEX_SHA256, MODULE_NAME, apply, patch


class ApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = Path(os.environ['OPENWEBUI_ORIGINAL_INDEX']).read_bytes()
        assert hashlib.sha256(cls.original).hexdigest() == INDEX_SHA256

    def make_checkout(self, directory):
        root = Path(directory)
        index = root / 'src/lib/apis/index.ts'
        index.parent.mkdir(parents=True)
        index.write_bytes(self.original)
        return root, index

    def test_check_is_read_only_and_apply_changes_only_get_models_and_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            root, index = self.make_checkout(directory)
            apply(root, check=True)
            self.assertEqual(index.read_bytes(), self.original)
            self.assertFalse((index.parent / MODULE_NAME).exists())
            apply(root)
            updated = index.read_text(encoding='utf-8')
            original = self.original.decode('utf-8')
            marker = 'export const unloadModel = async '
            self.assertEqual(updated[updated.index(marker):], original[original.index(marker):])
            self.assertEqual((index.parent / MODULE_NAME).read_bytes(), (Path(__file__).parent / MODULE_NAME).read_bytes())
            self.assertEqual(updated.count('showMainOAuthAuthorizationRequired(res.status'), 1)
            self.assertIn('\t\t\tthrow responseError;\n', updated)
            with self.assertRaises(ValueError):
                apply(root)

    def test_unexpected_source_or_existing_module_refused_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root, index = self.make_checkout(directory)
            index.write_bytes(self.original + b'\n')
            with self.assertRaises(ValueError):
                apply(root)
            self.assertEqual(index.read_bytes(), self.original + b'\n')
            self.assertFalse((index.parent / MODULE_NAME).exists())
            index.write_bytes(self.original)
            target = index.parent / MODULE_NAME
            target.write_text('existing module', encoding='utf-8')
            with self.assertRaises(ValueError):
                apply(root)
            self.assertEqual(index.read_bytes(), self.original)
            self.assertEqual(target.read_text(encoding='utf-8'), 'existing module')

    def test_changed_marker_is_refused(self):
        original = self.original.decode('utf-8')
        with self.assertRaises(ValueError):
            patch(original.replace('if (!res.ok) throw await res.json();', 'if (!res.ok) throw new Error();'))


if __name__ == '__main__':
    unittest.main()
