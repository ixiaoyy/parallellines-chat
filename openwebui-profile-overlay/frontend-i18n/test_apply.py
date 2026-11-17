import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('frontend_i18n_apply', HERE / 'apply.py')
feature = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feature)


class I18nOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['CHAT_I18N_BASELINE'])
        cls.manifest = json.loads((HERE / 'manifest.json').read_text())

    def fixture(self, target):
        root = Path(target)
        for name, hashes in self.manifest['files'].items():
            if hashes['before_sha256']:
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.source / name, dest)
        return root

    def test_manifest_check_atomic_application_and_repeat_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            before = {p: (root / p).read_bytes() if (root / p).exists() else None for p in self.manifest['files']}
            feature.apply(root, check=True)
            self.assertEqual(before, {p: (root / p).read_bytes() if (root / p).exists() else None for p in before})
            feature.apply(root)
            for name, hashes in self.manifest['files'].items():
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), hashes['after_sha256'])
            with self.assertRaisesRegex(ValueError, 'baseline_changed'):
                feature.apply(root)

    def test_unknown_input_is_rejected_before_any_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            target = root / 'src/lib/apis/index.ts'
            target.write_bytes(target.read_bytes() + b'\n// other work\n')
            with self.assertRaisesRegex(ValueError, 'baseline_changed'):
                feature.apply(root)
            self.assertFalse((root / 'src/lib/i18n/main-ui.ts').exists())
            self.assertIn('// other work', target.read_text(encoding='utf-8'))

    def test_translations_and_language_detection_keep_native_contracts(self):
        result = feature.prepare(self.source)
        en = json.loads(result['src/lib/i18n/locales/en-US/translation.json'])
        zh = json.loads(result['src/lib/i18n/locales/zh-CN/translation.json'])
        additions = json.loads((HERE / 'translations.json').read_text(encoding='utf-8'))
        self.assertEqual(additions['en-US'].keys(), additions['zh-CN'].keys())
        for key, value in additions['en-US'].items():
            self.assertEqual(en[key], value)
            self.assertTrue(zh[key])
        index = result['src/lib/i18n/index.ts']
        self.assertIn("order: ['querystring', 'localStorage', 'supportedBrowser', 'defaultLocale']", index)
        self.assertIn("lookupLocalStorage: 'locale'", index)
        self.assertIn("default: ['en-US']", index)
        self.assertNotIn('backend/', '\n'.join(result))
        guest = result['src/lib/components/layout/GuestChatShell.svelte']
        self.assertIn('await changeLanguage', guest)
        self.assertIn('getLanguages()', guest)
        self.assertIn('Open WebUI', guest)
        self.assertIn('loginForFeature(draft)', guest)
        self.assertNotIn('fetch(', guest)
        auth = result['src/lib/apis/main-oauth-auth-recovery.mjs']
        self.assertIn("status !== 403 || error?.detail?.code !== AUTHORIZATION_ERROR_CODE", auth)
        self.assertIn('navigate(REAUTHORIZE_PATH)', auth)
        silent = result['src/lib/apis/main-model-silent-recovery.mjs']
        self.assertIn("if (status === 'identity_mismatch')", silent)
        self.assertIn('RECOVERY_TIMEOUT_MS = 20000', silent)


if __name__ == '__main__':
    unittest.main()
