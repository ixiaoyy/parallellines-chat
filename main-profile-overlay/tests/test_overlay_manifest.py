"""Offline source-integrity checks; temporary fixtures only, no image or deployment."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("overlay_verify", ROOT / "verify_baseline.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class ManifestTests(unittest.TestCase):
    def test_accepts_a_complete_baseline_and_rejects_mixed_or_missing_files(self):
        with tempfile.TemporaryDirectory(prefix="pl-main-manifest-") as temporary:
            root = Path(temporary)
            manifests = []
            for version in ("original", "profile"):
                hashes = {name: hashlib.sha256((version + name).encode()).hexdigest()
                          for name in ("core.py", "service.py")}
                manifest = root / (version + ".json")
                manifest.write_text(json.dumps(hashes), encoding="utf-8")
                manifests.append(manifest)
            for name in ("core.py", "service.py"):
                (root / name).write_text("profile" + name, encoding="utf-8")
            self.assertEqual(verify.verify_source(root, manifests), "profile.json")
            (root / "core.py").write_text("originalcore.py", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "complete reviewed manifest"):
                verify.verify_source(root, manifests)
            (root / "service.py").unlink()
            with self.assertRaisesRegex(SystemExit, "complete reviewed manifest"):
                verify.verify_source(root, manifests)

    def test_output_manifest_covers_all_replacement_files_and_unchanged_model(self):
        expected = json.loads((ROOT / "output-sha256.json").read_text(encoding="utf-8"))
        self.assertEqual(set(expected), {"core/chat_sso.py", "services/chat_sso.py",
                                        "schemas/chat_sso.py", "services/auth.py", "models/user.py"})
        for name, wanted in expected.items():
            if name != "models/user.py":
                self.assertEqual(hashlib.sha256((ROOT / "app" / name).read_bytes()).hexdigest(), wanted)
        for name in ("baseline-sha256.json", "baseline-profile-v1-sha256.json"):
            baseline = json.loads((ROOT / name).read_text(encoding="utf-8"))
            self.assertEqual(set(baseline), set(expected))
            self.assertEqual(baseline["models/user.py"], expected["models/user.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
