"""个人连接前端补丁的定向接线检查；不写 prepared source。"""
import importlib.util
import json
import os
from pathlib import Path
import unittest


FEATURE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('personal_connection_frontend', FEATURE / 'frontend.py')
frontend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend)
SOURCE = Path(os.environ.get('PERSONAL_CONNECTIONS_SOURCE', FEATURE.parents[1] / '.build/personal-connections-20261117/source'))


class FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = frontend.prepare(SOURCE)

    def test_only_owned_native_files_and_new_module(self):
        self.assertEqual(set(self.files), {
            frontend.MODAL_PATH, frontend.SETTINGS_PATH, frontend.ROW_PATH,
            frontend.TRANSLATION_PATH, frontend.MODULE_PATH,
        })

    def test_models_are_visible_without_advanced_and_admin_controls_remain(self):
        modal = self.files[frontend.MODAL_PATH]
        self.assertIn('{#if direct || showAdvanced}', modal)
        self.assertEqual(modal.count("{$i18n.t('Model IDs')}"), 1)
        self.assertIn("value=\"system_oauth\"", modal)
        self.assertIn("apiType = apiType === 'responses'", modal)
        self.assertIn('if (direct) {\n\t\t\tverifyPersonalHandler();', modal)

    def test_personal_config_keeps_native_prefix_and_does_not_store_key_locally(self):
        modal = self.files[frontend.MODAL_PATH]
        self.assertIn('personalConnectionConfig({ prefix_id: prefixId, auth_type }, connectionName, personalLegacy)', modal)
        self.assertIn('...(direct ? { name: connectionName.trim() } : {})', modal)
        self.assertIn('prefix_id: prefixId', modal)
        for source in self.files.values():
            self.assertNotIn('localStorage.setItem', source)
            self.assertNotIn('localStorage.settings', source)

    def test_save_and_delete_callbacks_are_awaited(self):
        modal = self.files[frontend.MODAL_PATH]
        row = self.files[frontend.ROW_PATH]
        self.assertIn('await onSubmit(connection)', modal)
        self.assertIn('await onDelete()', modal)
        self.assertIn('onSubmit={async (connection)', row)
        self.assertIn('await onSubmit(connection);\n\t\turl = connection.url', row)
        self.assertIn('await saveSettings({ directConnections: candidate });\n\t\t\tconfig = candidate;', self.files[frontend.SETTINGS_PATH])

    def test_chinese_copy_is_complete_and_old_translations_preserved(self):
        before = json.loads((SOURCE / frontend.TRANSLATION_PATH).read_text(encoding='utf-8'))
        after = json.loads(self.files[frontend.TRANSLATION_PATH])
        for key, value in before.items():
            self.assertEqual(after[key], value)
        for key, value in frontend.TRANSLATIONS.items():
            self.assertEqual(after[key], before.get(key, value))

    def test_baseline_drift_fails_instead_of_patching_another_branch(self):
        with self.assertRaisesRegex(RuntimeError, 'baseline_changed'):
            frontend.modal('<script></script>')


if __name__ == '__main__':
    unittest.main()
