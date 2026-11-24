"""检查精确源基线、独立补丁和旧 getModels 之外的全部代码保留。"""
import os
from pathlib import Path
import tempfile
import unittest

from apply import apply, MODULE_NAME, patch

BASELINE = Path(os.environ['MODEL_RECOVERY_BASELINES']) / 'index.ts'


class FrontendPatchTests(unittest.TestCase):
    def test_only_get_models_and_imports_change_and_signal_contract_is_preserved(self):
        original = BASELINE.read_text(encoding='utf-8')
        updated = patch(original)
        marker = 'export const unloadModel = async '
        self.assertEqual(original[original.index(marker):], updated[updated.index(marker):])
        self.assertIn('signal?: AbortSignal', updated)
        self.assertEqual(updated.count('if (signal?.aborted) throw signal.reason;'),
                         original.count('if (signal?.aborted) throw signal.reason;'))
        self.assertIn('getOpenAIModelsDirect(url, OPENAI_API_KEYS[idx], signal)', updated)
        self.assertIn('startModelAuthorizationRecovery(token, toast)', updated)
        self.assertNotIn('await startModelAuthorizationRecovery', updated)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'src/lib/apis/index.ts'
            target.parent.mkdir(parents=True)
            target.write_bytes(BASELINE.read_bytes())
            layout = root / 'src/routes/(app)/+layout.svelte'
            layout.parent.mkdir(parents=True)
            layout.write_bytes((BASELINE.parents[0] / 'app-layout.svelte').read_bytes())
            apply(root, check=True)
            self.assertEqual(target.read_bytes(), BASELINE.read_bytes())
            target.write_bytes(BASELINE.read_bytes() + b'\n')
            with self.assertRaises(ValueError): apply(root)
            self.assertFalse((target.parent / MODULE_NAME).exists())
            target.write_bytes(BASELINE.read_bytes())
            apply(root)
            self.assertTrue((target.parent / MODULE_NAME).exists())
            with self.assertRaises(ValueError): apply(root)


if __name__ == '__main__': unittest.main()
