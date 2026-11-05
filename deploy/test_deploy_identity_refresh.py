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
        self.runtime_sources = {name: ("// old " + name).encode() for name in release.CHANGED}
        self.release_sources = {name: ("// new " + name).encode() for name in release.CHANGED}
        self.has_release = scope.get("resume_existing_image", False)
        self.release_id = "sha256:" + "c" * 64
        self.health_waits = []

    def run(self, args, timeout=180):
        args = list(map(str, args))
        self.commands.append(args)
        s = self.s
        if args[0] == "findmnt":
            return "/data\n"
        if args[:2] == ["docker", "inspect"]:
            if args[3] == "{{.Image}}":
                return self.release_id if self.current_bridge == s["new_bridge_image"] else "sha256:" + "a" * 64
            return self.current_bridge if args[-1] == s["bridge_container"] else s["expected_ow_image"]
        if args[:3] == ["docker", "image", "inspect"]:
            return self.release_id if args[-1] == s["new_bridge_image"] else "sha256:" + "a" * 64
        if args[:3] == ["docker", "image", "ls"]:
            return self.release_id if self.has_release else ""
        if args[:2] == ["docker", "run"] and "createHash" in args[-1]:
            name = next(name for name in release.CHANGED if name in args[-1])
            return release.digest(self.release_sources[name])
        if args[:2] == ["docker", "exec"]:
            name = next(name for name in release.CHANGED if name in args[-1])
            return (release.digest(self.runtime_sources[name]) if "createHash" in args[-1]
                    else self.runtime_sources[name].decode("utf-8"))
        if args[:2] == ["docker", "compose"] and "up" in args and args[-1] == s["bridge_service"]:
            self.current_bridge = release.yaml.safe_load(s["bridge_compose"].read_text())["services"][s["bridge_service"]]["image"]
        return ""

    def healthy(self, container, seconds=60):
        self.health_waits.append((container, seconds))
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
        # Fixtures stay in the OS temp directory rather than touching /data.
        # Windows cannot enforce POSIX modes. All content/scope guards stay active.
        patch.object(release, "check", side_effect=lambda condition, reason: original_check(
            condition or reason == "Client env file must stay on /data" or
            (os.name == "nt" and reason in {"Receipt directory must be private", "Client env file must be mode 0600"}), reason)).start()
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
        self.assertEqual(config["services"]["openwebui"]["environment"]["OAUTH_SCOPES"], 'openid profile email offline_access')
        self.assertEqual({path: path.stat().st_ino for path in self.originals}, self.inodes)
        ups = [args for args in deployer.commands if args[:2] == ["docker", "compose"] and "up" in args]
        self.assertEqual([args[-1] for args in ups], ["chat-oidc", "openwebui"])
        self.assertTrue(all("--no-deps" in args and "--no-build" in args and "never" in args for args in ups))
        self.assertTrue(json.loads((self.scope["receipt_dir"] / "identity-refresh-receipt.json").read_text())["success"])
        self.assertTrue(all(".Config.Env" not in " ".join(args) for args in deployer.commands))
        self.assertEqual(deployer.health_waits, [("issuer", 60), ("chat", 180)])

    def test_resume_verifies_immutable_existing_image_without_rebuilding(self):
        self.scope["resume_existing_image"] = True
        deployer = FakeDeployer(self.scope)
        deployer.deploy()
        self.assertFalse(any(args[:2] == ["docker", "build"] for args in deployer.commands))
        checks = [args for args in deployer.commands if args[:2] == ["docker", "run"]]
        self.assertEqual(len(checks), 4)
        self.assertTrue(all(deployer.release_id in args and "--network=none" in args for args in checks))
        receipt = json.loads((self.scope["receipt_dir"] / "identity-refresh-receipt.json").read_text())
        self.assertTrue(receipt["resumed_existing_image"])
        self.assertEqual(receipt["bridge_image_id"], deployer.release_id)

    def test_resume_rejects_wrong_release_source_before_any_mutation(self):
        self.scope["resume_existing_image"] = True
        deployer = FakeDeployer(self.scope)
        deployer.release_sources["config.mjs"] = b"unreviewed source"
        with self.assertRaisesRegex(RuntimeError, "Release image source mismatch"):
            deployer.deploy()
        for path, before in self.originals.items():
            self.assertEqual(path.read_bytes(), before)
        self.assertFalse(any(args[:2] in (["docker", "build"], ["docker", "compose"]) for args in deployer.commands))

    def test_existing_image_requires_explicit_resume_and_resume_requires_image(self):
        for flag in (False, True):
            self.scope["resume_existing_image"] = flag
            deployer = FakeDeployer(self.scope)
            deployer.has_release = not flag
            with self.assertRaisesRegex(RuntimeError, "Resume requires an existing image"):
                deployer.deploy()
            self.assertIsNone(deployer.run_dir)

    def test_resume_tag_movement_stops_before_source_write_or_restart(self):
        self.scope["resume_existing_image"] = True
        deployer = FakeDeployer(self.scope)
        original_compose = deployer.compose
        def tag_moves(target, *args):
            result = original_compose(target, *args)
            if target == "chat":
                deployer.release_id = "sha256:" + "d" * 64
            return result
        with patch.object(deployer, "compose", side_effect=tag_moves):
            with self.assertRaisesRegex(RuntimeError, "rollback healthy"):
                deployer.deploy()
        for path, before in self.originals.items():
            self.assertEqual(path.read_bytes(), before)
        self.assertFalse(any(args[:2] == ["docker", "compose"] and "up" in args for args in deployer.commands))

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
        failure = json.loads((deployer.run_dir / "failure.json").read_text())
        self.assertEqual(failure["failed_stage"], "activate-chat")
        self.assertEqual(failure["failure_type"], "RuntimeError")
        self.assertNotIn("Synthetic", json.dumps(failure))

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

    def stale_fixture(self):
        self.scope["restore_from_verified_runtime"] = True
        stale = {self.scope["provider_dir"] / name: ("// reviewed stale " + name).encode() for name in release.CHANGED}
        patch.dict(release.KNOWN_STALE_SOURCE, {path.name: release.digest(data) for path, data in stale.items()}).start()
        for path, data in stale.items():
            path.write_bytes(data)
        return stale

    def test_known_checkout_drift_requires_verified_runtime_and_keeps_independent_backup(self):
        stale = self.stale_fixture()
        deployer = FakeDeployer(self.scope)
        deployer.deploy()
        receipt = json.loads(deployer.reconciliation_receipt.read_text())
        self.assertTrue(receipt["success"])
        self.assertFalse(receipt["services_restarted"])
        for path, before in stale.items():
            self.assertEqual((deployer.reconciliation_receipt.parent / path.name).read_bytes(), before)
            self.assertEqual(path.stat().st_ino, self.inodes[path])
        ups = [args for args in deployer.commands if args[:2] == ["docker", "compose"] and "up" in args]
        self.assertEqual([args[-1] for args in ups], ["chat-oidc", "openwebui"])

    def test_runtime_reconciliation_rejects_unknown_disk_or_runtime_bytes(self):
        stale = self.stale_fixture()
        unknown = self.scope["provider_dir"] / "config.mjs"
        unknown.write_text("not a reviewed checkout")
        deployer = FakeDeployer(self.scope)
        with self.assertRaisesRegex(RuntimeError, "only the two reviewed stale"):
            deployer.reconcile_known_stale_source()
        self.assertEqual(deployer.commands, [])
        unknown.write_bytes(stale[unknown])
        deployer.runtime_sources["config.mjs"] = b"unexpected running source"
        with self.assertRaisesRegex(RuntimeError, "not the approved release"):
            deployer.reconcile_known_stale_source()
        for path, data in stale.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertIsNone(deployer.reconciliation_receipt)

    def test_runtime_reconciliation_write_failure_restores_stale_files_without_restart(self):
        stale = self.stale_fixture()
        deployer = FakeDeployer(self.scope)
        original_write = release.write_in_place
        def fail_second_write(path, data):
            if path.name == "identity-provider.mjs" and data == deployer.runtime_sources[path.name]:
                raise OSError("Synthetic source restore failure")
            original_write(path, data)
        with patch.object(release, "write_in_place", side_effect=fail_second_write):
            with self.assertRaisesRegex(RuntimeError, "no service restarted"):
                deployer.reconcile_known_stale_source()
        for path, data in stale.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse(any(args[:2] == ["docker", "compose"] for args in deployer.commands))
        self.assertTrue(json.loads(deployer.reconciliation_receipt.read_text())["stale_disk_restored"])

    def compose_reset_fixture(self):
        root = Path(self.temporary.name)
        client_env = root / "bridge-clients.env"
        client_env.write_text("SYNTHETIC=not-read\n")
        client_env.chmod(0o600)
        backup = release.yaml.safe_load(self.scope["bridge_compose"].read_text())
        backup["services"]["chat-oidc"].update(image="bridge:sso-clients-20261030", env_file=[str(client_env)])
        backup_path = root / "compose.before.yaml"
        backup_path.write_text(release.yaml.safe_dump(backup, sort_keys=False))
        self.scope["restore_compose_backup"] = backup_path
        reset = release.copy.deepcopy(backup)
        reset["services"]["chat-oidc"].update(image="bridge:${CHAT_SSO_RELEASE:?Set release commit}")
        del reset["services"]["chat-oidc"]["env_file"]
        self.scope["bridge_compose"].write_text(release.yaml.safe_dump(reset, sort_keys=False))
        return self.scope["bridge_compose"].read_bytes(), str(client_env)

    def test_verified_compose_reset_is_reconciled_without_changing_other_settings(self):
        self.stale_fixture()
        reset_bytes, client_env = self.compose_reset_fixture()
        deployer = FakeDeployer(self.scope)
        deployer.deploy()
        doc = release.yaml.safe_load(self.scope["bridge_compose"].read_text())
        self.assertEqual(doc["services"]["chat-oidc"], {"image": self.scope["new_bridge_image"], "env_file": [client_env]})
        self.assertEqual((deployer.reconciliation_receipt.parent / self.scope["bridge_compose"].name).read_bytes(), reset_bytes)
        self.assertEqual(self.scope["bridge_compose"].stat().st_ino, self.inodes[self.scope["bridge_compose"]])

    def test_other_compose_drift_stops_before_reconciliation_writes(self):
        stale = self.stale_fixture()
        self.compose_reset_fixture()
        current = release.yaml.safe_load(self.scope["bridge_compose"].read_text())
        current["services"]["chat-oidc"]["privileged"] = True
        self.scope["bridge_compose"].write_text(release.yaml.safe_dump(current))
        deployer = FakeDeployer(self.scope)
        with self.assertRaisesRegex(RuntimeError, "differs beyond"):
            deployer.reconcile_known_stale_source()
        self.assertIsNone(deployer.reconciliation_receipt)
        for path, data in stale.items():
            self.assertEqual(path.read_bytes(), data)

    def test_compose_repair_failure_restores_both_source_files_and_reset_compose(self):
        stale = self.stale_fixture()
        reset_bytes, _ = self.compose_reset_fixture()
        deployer = FakeDeployer(self.scope)
        original_write = release.write_in_place
        def fail_compose(path, data):
            if path == self.scope["bridge_compose"] and data != reset_bytes:
                raise OSError("Synthetic Compose repair failure")
            original_write(path, data)
        with patch.object(release, "write_in_place", side_effect=fail_compose):
            with self.assertRaisesRegex(RuntimeError, "no service restarted"):
                deployer.reconcile_known_stale_source()
        for path, data in {**stale, self.scope["bridge_compose"]: reset_bytes}.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse(any(args[:2] == ["docker", "compose"] for args in deployer.commands))


class ReadinessTests(unittest.TestCase):
    def state(self, **overrides):
        return {"running": True, "restarting": False, "oom_killed": False, "dead": False,
                "exit_code": 0, "restart_count": 0, "health": "starting", **overrides}

    def test_chat_can_become_ready_after_old_sixty_second_window(self):
        deployer = release.Deployer({})
        with patch.object(deployer, "container_state", side_effect=[self.state(), self.state(), self.state(health="healthy")]), \
             patch.object(release.time, "monotonic", side_effect=[0, 60, 90]), \
             patch.object(release.time, "sleep") as sleep:
            deployer.healthy("chat", 180)
        self.assertEqual(sleep.call_count, 2)

    def test_timeout_and_terminal_failure_have_only_safe_state_fields(self):
        deployer = release.Deployer({})
        for state in (self.state(), self.state(running=False, oom_killed=True, exit_code=137)):
            with patch.object(deployer, "container_state", return_value=state), \
                 patch.object(release.time, "monotonic", side_effect=[0, 180]), \
                 patch.object(release.time, "sleep"):
                with self.assertRaises(release.ReadinessError) as raised:
                    deployer.healthy("chat", 180)
            self.assertEqual(raised.exception.details, {"container": "chat", "wait_seconds": 180, "state": state})

    def test_state_inspection_does_not_read_logs_error_or_environment(self):
        deployer = release.Deployer({})
        with patch.object(deployer, "run", return_value=json.dumps(self.state())) as run:
            self.assertEqual(deployer.container_state("chat"), self.state())
        command = " ".join(run.call_args.args[0])
        for forbidden in (".Config.Env", ".State.Error", ".Health.Log"):
            self.assertNotIn(forbidden, command)


if __name__ == "__main__":
    unittest.main()
