import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('traffic_apply', Path(__file__).with_name('apply.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TrafficLoaderApplyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.application = Path(self.temporary.name)
        self.target = self.application / 'build/static/loader.js'
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'')
        self.index = self.application / 'build/index.html'
        self.index.write_text('<script src="/static/loader.js" defer></script>', encoding='utf-8')

    def test_default_off_preserves_empty_native_slot(self):
        self.assertFalse(MODULE.apply(self.application))
        self.assertEqual(self.target.read_bytes(), b'')

    def test_enable_is_idempotent_and_disable_restores_only_own_loader(self):
        self.assertTrue(MODULE.apply(self.application, 'true'))
        self.assertEqual(self.target.read_bytes(), Path(__file__).with_name('loader.js').read_bytes())
        self.assertFalse(MODULE.apply(self.application, 'true'))
        self.assertTrue(MODULE.apply(self.application, 'false'))
        self.assertEqual(self.target.read_bytes(), b'')

    def test_existing_custom_loader_is_not_overwritten_or_removed(self):
        self.target.write_bytes(b'customLoader();')
        with self.assertRaisesRegex(ValueError, 'not_empty'):
            MODULE.apply(self.application, 'true')
        self.assertFalse(MODULE.apply(self.application, 'false'))
        self.assertEqual(self.target.read_bytes(), b'customLoader();')

    def test_changed_native_entry_stops_without_writing(self):
        self.index.write_text('<html></html>', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'entry_missing_or_changed'):
            MODULE.apply(self.application, 'true')
        self.assertEqual(self.target.read_bytes(), b'')

    def test_missing_slot_and_invalid_switch_are_rejected(self):
        self.target.unlink()
        with self.assertRaisesRegex(ValueError, 'slot_missing'):
            MODULE.apply(self.application, 'true')
        with self.assertRaisesRegex(ValueError, 'must_be_true_or_false'):
            MODULE.apply(self.application, 'yes')


if __name__ == '__main__':
    unittest.main()
