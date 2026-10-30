"""Local fixtures and mocked subprocess only: never executes Docker or a server command."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("refresh_deploy", Path(__file__).with_name("deploy-identity-refresh.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class FakeDeployer(release.Deployer):
    def __init__(self, scope, fail_chat=False):
        super().__init__(scope)
        self.commands = []
        self.current_bridge = scope["expected_bridge_image"]
        self.fail_chat = fail_chat

    def run(self, args, timeout=180):
        args = list(map(str, args))
        self.commands.append(args)
        s = self.s
        if args[0] == "findmnt":
            return "/data\n"
        if args[:2] == ["docker", "inspect"]:
            if args[3] == "{{.Image}}":
                return "sha256:" + "a" * 64
            return self.current_bridge if args[-1] == s["bridge_container"] else s["expected_ow_image"]
        if args[:3] == ["docker", "image", "inspect"]:
            return "sha256:" + "a" * 64
        if args[:2] == ["docker", "exec"]:
            name = next(name for name in release.CHANGED if name in args[-1])
            return release.EXPECTED_SOURCE[name]
        if args[:2] == ["docker", "compose"] and "up" in args and args[-1] == s["bridge_service"]:
            self.current_bridge = release.yaml.safe_load(s["bridge_compose"].read_text())["services"][s["bridge_service"]]["image"]
        return ""

    def healthy(self, container):
        if self.fail_chat and container == self.s["ow_container"]:
            self.fail_chat = False
            raise RuntimeError("Synthetic Chat readiness failure")


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pl-deploy-refresh-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.scope = {key: root / key for key in release.PATH_KEYS}
        for key in ("source_dir", "provider_dir", "receipt_dir"):
            self.scope[key].mkdir(mode=0o700)
        self.scope.update(bridge_project="legacy", bridge_service="chat-oidc", bridge_container="issuer",
                          candidate_project="candidate", ow_service="openwebui", ow_container="chat",
                          expected_bridge_image="bridge:sso-recovery-20261030", new_bridge_image="bridge:refresh-test",
                          expected_ow_image="official-chat@sha256:" + "b" * 64,
                          bridge_env_files=[root / ".env", root / "second.env", root / "session-release.env"],
                          candidate_env_files=[root / "candidate.env"])
        for filename in self.scope["bridge_env_files"] + self.scope["candidate_env_files"]:
            filename.write_text("SYNTHETIC=not-used\n")
        old, new = {}, {}
        for name in release.CHANGED:
            before, after = ("// old " + name).encode(), ("// new " + name).encode()
            (self.scope["provider_dir"] / name).write_bytes(before)
            (self.scope["source_dir"] / name).write_bytes(after)
            old[name], new[name] = release.digest(before), release.digest(after)
        self.addCleanup(patch.stopall)
        patch.dict(release.EXPECTED_SOURCE, old).start()
        patch.dict(release.NEW_SOURCE, new).start()
        original_check = release.check
        # Windows cannot enforce POSIX mode bits; every other guard stays active.
        patch.object(release, "check", side_effect=lambda condition, reason: original_check(
            condition or (os.name == "nt" and reason == "Receipt directory must be private"), reason)).start()
        self.scope["base_compose"].write_text("services:\n  untouched-main:\n    image: keep\n")
        self.scope["bridge_compose"].write_text("services:\n  chat-oidc:\n    image: bridge:sso-recovery-20261030\n    env_file: [/restricted/runtime.env]\n")
        self.other_service = "  newapi:\n    image: api:do-not-touch\n    environment:\n      KeepValue: 'false'\n"
        self.scope["candidate_compose"].write_text("services:\n  openwebui:\n    image: official-chat\n    environment:\n      OAUTH_SCOPES: openid profile\n      DEFAULT_USER_ROLE: user\n" + self.other_service + "networks:\n  edge:\n    external: true\n")
        self.originals = {path: path.read_bytes() for path in [self.scope["bridge_compose"], self.scope["candidate_compose"],
            *(self.scope["provider_dir"] / name for name in release.CHANGED)]}
        self.inodes = {path: path.stat().st_ino for path in self.originals}

    def test_success_changes_only_target_services_and_preserves_inodes(self):
        deployer = FakeDeployer(self.scope)
        deployer.deploy()
        doc = self.scope["candidate_compose"].read_text()
        self.assertIn(self.other_service, doc)
        config = release.yaml.safe_load(doc)
        self.assertEqual(config["services"]["openwebui"]["environment"]["DEFAULT_USER_ROLE"], "user")
        self.assertEqual(config["services"]["openwebui"]["environment"]["OAUTH_AUTHORIZE_PARAMS"], '{"prompt":"consent"}')
        self.assertEqual({path: path.stat().st_ino for path in self.originals}, self.inodes)
        ups = [args for args in deployer.commands if args[:2] == ["docker", "compose"] and "up" in args]
        self.assertEqual([args[-1] for args in ups], ["chat-oidc", "openwebui"])
        self.assertTrue(all("--no-deps" in args and "--no-build" in args and "never" in args for args in ups))
        self.assertTrue(json.loads((self.scope["receipt_dir"] / "identity-refresh-receipt.json").read_text())["success"])
        self.assertTrue(all(".Config.Env" not in " ".join(args) for args in deployer.commands))

    def test_health_failure_restores_both_configs_and_sources_before_restarting(self):
        deployer = FakeDeployer(self.scope, fail_chat=True)
        with self.assertRaisesRegex(RuntimeError, "rollback healthy"):
            deployer.deploy()
        for path, before in self.originals.items():
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_ino, self.inodes[path])
        self.assertEqual(deployer.current_bridge, self.scope["expected_bridge_image"])
        self.assertFalse((self.scope["receipt_dir"] / "identity-refresh-receipt.json").exists())
        self.assertTrue(json.loads((deployer.run_dir / "failure.json").read_text())["rollback_healthy"])

    def test_source_drift_stops_before_build_or_mutation(self):
        (self.scope["provider_dir"] / "config.mjs").write_text("unexpected change")
        deployer = FakeDeployer(self.scope)
        with self.assertRaisesRegex(RuntimeError, "Live source hash changed"):
            deployer.deploy()
        self.assertFalse(any("build" in args or "up" in args for args in deployer.commands))

    def test_incomplete_restore_never_recreates_against_mixed_files(self):
        deployer = FakeDeployer(self.scope, fail_chat=True)
        original_write = release.write_in_place
        def fail_restore(path, data):
            if path == self.scope["candidate_compose"] and data == self.originals[path]:
                raise OSError("Synthetic restore failure")
            original_write(path, data)
        with patch.object(release, "write_in_place", side_effect=fail_restore):
            with self.assertRaisesRegex(RuntimeError, "needs operator recovery"):
                deployer.deploy()
        ups = [args for args in deployer.commands if args[:2] == ["docker", "compose"] and "up" in args]
        self.assertEqual(len(ups), 2)
        self.assertFalse(json.loads((deployer.run_dir / "failure.json").read_text())["rollback_healthy"])


if __name__ == "__main__":
    unittest.main()
