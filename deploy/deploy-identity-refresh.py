#!/usr/bin/env python3
"""Apply the reviewed Chat refresh release. Takes a server-only, nonsecret JSON scope.

No env-file contents, container environments, credentials, or model APIs are read.
Requires Python 3, PyYAML, Docker Compose, and an existing /data mount on Linux.
"""
import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

import yaml

CHANGED = ("config.mjs", "identity-provider.mjs")
EXPECTED_SOURCE = {
    "config.mjs": "1c2729ac3d98c482fa6e3b6db3fa861fcacbe8f53e25964e6ca9c0500296f190",
    "identity-provider.mjs": "1b85216a508781843e22da25ad5feb3c5602ffa5630280321bd8cae9eb8becfd",
}
NEW_SOURCE = {
    "config.mjs": "9816678de28ab12ae50965a3eeaee47b3e2b1a2af0f549db795e001426830ff1",
    "identity-provider.mjs": "28f2ae21bbc9c8d394dc77dd9a263dd332c846ec31c9b0f93fbe72c11fcb9285",
}
KNOWN_STALE_SOURCE = {
    "config.mjs": "d6a509a1d10090d285d75073a799cc7398301aa9e2ed4f6ad8f6bf2f68ff9167",
    "identity-provider.mjs": "46b3b3b459373abe791cc6370462a3d7c51eb3d181c248f3727edc15eef9e80c",
}
REFRESH_ENV = {
    "OAUTH_SCOPES": "openid profile offline_access",
    "OAUTH_AUTHORIZE_PARAMS": '{"prompt":"consent"}',
    "OAUTH_REFRESH_TOKEN_INCLUDE_SCOPE": "false",
}
PATH_KEYS = ("source_dir", "provider_dir", "base_compose", "bridge_compose",
             "candidate_compose", "receipt_dir")
NAME_KEYS = ("bridge_project", "bridge_service", "bridge_container", "candidate_project",
             "ow_service", "ow_container", "expected_bridge_image", "new_bridge_image", "expected_ow_image")


def check(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_scope(filename):
    scope = json.loads(Path(filename).read_text(encoding="utf-8"))
    required = set(PATH_KEYS + NAME_KEYS) | {"bridge_env_files", "candidate_env_files"}
    optional = {"restore_from_verified_runtime", "restore_compose_backup"}
    check(required <= set(scope) <= required | optional, "Scope keys do not match the documented schema")
    check(type(scope.get("restore_from_verified_runtime", False)) is bool, "Runtime restore flag must be boolean")
    if "restore_compose_backup" in scope:
        check(scope.get("restore_from_verified_runtime") is True, "Compose restore requires explicit runtime reconciliation")
        scope["restore_compose_backup"] = safe_path(scope["restore_compose_backup"], file=True)
    for key in NAME_KEYS:
        check(isinstance(scope[key], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]*", scope[key]), "Unsafe name: " + key)
    check(scope["expected_bridge_image"].endswith(":sso-recovery-20261030"), "Unexpected bridge release")
    check(scope["new_bridge_image"] != scope["expected_bridge_image"], "New image must have a distinct tag")
    for key in PATH_KEYS:
        scope[key] = safe_path(scope[key])
    for key in ("bridge_env_files", "candidate_env_files"):
        check(isinstance(scope[key], list), "Env-file paths must be a list")
        scope[key] = [safe_path(value, file=True) for value in scope[key]]
    check(len(scope["bridge_env_files"]) == 3 and scope["bridge_env_files"][-1].name == "session-release.env", "Preserve all three existing bridge env files")
    check(scope["receipt_dir"].is_relative_to(Path("/data")), "Receipts/builds must stay on /data")
    for key in ("base_compose", "bridge_compose", "candidate_compose"):
        check(scope[key].is_file(), "Compose file missing: " + key)
    check(len({scope[k] for k in ("base_compose", "bridge_compose", "candidate_compose")}) == 3, "Compose targets must be distinct")
    check(scope["source_dir"] != scope["provider_dir"], "Release and live source must be distinct")
    check(scope["bridge_container"] != scope["ow_container"], "Service containers must be distinct")
    return scope


def safe_path(value, file=False):
    check(isinstance(value, str) and "\n" not in value and "\r" not in value, "Invalid path")
    path = Path(value)
    check(path.is_absolute() and ".." not in path.parts, "Paths must be absolute and normalized")
    check(all(not item.is_symlink() for item in (path, *path.parents)), "Symlink path rejected")
    if file:
        check(path.is_file(), "Required file missing")
    return path


def write_in_place(path, data):
    """Keep the inode so existing single-file bind mounts observe the same file."""
    check(path.is_file() and not path.is_symlink(), "Unsafe in-place target")
    before = path.stat()
    with path.open("r+b") as stream:
        stream.seek(0)
        stream.write(data)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    after = path.stat()
    check((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), "Target inode changed")


def patch_service(text, service, update):
    """Change only this service block; preserve all other service text verbatim."""
    original = yaml.safe_load(text)
    expected = copy.deepcopy(original)
    check(isinstance(expected.get("services", {}).get(service), dict), "Service missing")
    update(expected["services"][service])
    root = yaml.compose(text)
    services = next(value for key, value in root.value if key.value == "services")
    matches = [value for key, value in services.value if key.value == service]
    check(len(matches) == 1, "Duplicate service definition")
    node = matches[0]
    lines = text.splitlines(keepends=True)
    start = sum(map(len, lines[:node.start_mark.line]))
    end = sum(map(len, lines[:node.end_mark.line]))
    check(node.start_mark.column == 4 and node.end_mark.column in (0, 2, 4), "Use ordinary block-style service YAML")
    replacement = "".join("    " + line if line.strip() else line for line in
                          yaml.safe_dump(expected["services"][service], sort_keys=False, allow_unicode=True).splitlines(keepends=True))
    result = text[:start] + replacement + text[end:]
    check(yaml.safe_load(result) == expected, "Compose change exceeded intended service")
    return result


class Deployer:
    def __init__(self, scope):
        self.s = scope
        self.run_dir = None
        self.reconciliation_receipt = None

    def run(self, args, timeout=180):
        result = subprocess.run([str(arg) for arg in args], capture_output=True, timeout=timeout)
        # Child output may include expanded settings. Never print or persist it.
        check(result.returncode == 0, "Command failed: " + " ".join(str(arg) for arg in args[:2]))
        return result.stdout.decode("utf-8")

    def compose(self, target, *args):
        s = self.s
        bridge = target == "bridge"
        command = ["docker", "compose", "--project-name", s["bridge_project" if bridge else "candidate_project"]]
        for envfile in s["bridge_env_files" if bridge else "candidate_env_files"]:
            command += ["--env-file", envfile]
        for filename in ([s["base_compose"], s["bridge_compose"]] if bridge else [s["candidate_compose"]]):
            command += ["-f", filename]
        return self.run(command + list(args))

    def image(self, container):
        return self.run(["docker", "inspect", "--format", "{{.Config.Image}}", container]).strip()

    def healthy(self, container):
        for _ in range(30):
            # Inspect only status booleans; .State.Health.Log could contain output.
            state = self.run(["docker", "inspect", "--format", "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", container]).strip()
            if state == "true healthy":
                return
            time.sleep(2)
        raise RuntimeError("Container did not become healthy: " + container)

    def recreate(self, target):
        service = self.s["bridge_service" if target == "bridge" else "ow_service"]
        self.compose(target, "up", "-d", "--no-deps", "--no-build", "--pull", "never", service)
        self.healthy(self.s["bridge_container" if target == "bridge" else "ow_container"])

    def reconcile_known_stale_source(self):
        """Opt-in repair of reviewed source/Compose checkout drift; no restart."""
        s = self.s
        if not s.get("restore_from_verified_runtime", False):
            return
        for name in CHANGED:
            source = safe_path(str(s["source_dir"] / name), file=True)
            check(digest(source.read_bytes()) == NEW_SOURCE[name], "Unreviewed release source: " + name)
        disk = {safe_path(str(s["provider_dir"] / name), file=True): (s["provider_dir"] / name).read_bytes() for name in CHANGED}
        if all(digest(data) == EXPECTED_SOURCE[path.name] for path, data in disk.items()):
            disk = {}
        else:
            check(all(digest(data) == KNOWN_STALE_SOURCE[path.name] for path, data in disk.items()),
                  "Runtime restore accepts only the two reviewed stale source hashes")
        runtime = {}
        for path in disk:
            data = self.run(["docker", "exec", s["bridge_container"], "node", "-e",
                "process.stdout.write(require('node:fs').readFileSync('/app/" + path.name + "'))"]).encode("utf-8")
            check(digest(data) == EXPECTED_SOURCE[path.name], "Runtime source content is not the approved release")
            runtime[path] = data
        if "restore_compose_backup" in s:
            backup_text = safe_path(str(s["restore_compose_backup"]), file=True).read_text(encoding="utf-8")
            backup_doc = yaml.safe_load(backup_text)
            backup_service = backup_doc.get("services", {}).get(s["bridge_service"], {})
            previous_image = s["expected_bridge_image"].removesuffix(":sso-recovery-20261030") + ":sso-clients-20261030"
            check(backup_service.get("image") == previous_image, "Backup is not the approved pre-recovery Compose")
            envfiles = backup_service.get("env_file")
            check(isinstance(envfiles, list) and len(envfiles) == 1, "Backup must retain the one existing client env file")
            client_env = safe_path(envfiles[0], file=True)
            check(client_env.is_relative_to(Path("/data")) and client_env.name == "bridge-clients.env", "Client env file must stay on /data")
            check(client_env.stat().st_mode & 0o777 == 0o600, "Client env file must be mode 0600")
            desired_text = patch_service(backup_text, s["bridge_service"], lambda service: service.update(image=s["expected_bridge_image"]))
            desired = yaml.safe_load(desired_text)
            current_bytes = s["bridge_compose"].read_bytes()
            current = yaml.safe_load(current_bytes)
            if current != desired:
                reset_image = s["expected_bridge_image"].removesuffix(":sso-recovery-20261030") + ":${CHAT_SSO_RELEASE:?Set release commit}"
                current_service = current.get("services", {}).get(s["bridge_service"], {})
                check(current_service.get("image") == reset_image and "env_file" not in current_service, "Compose is not the reviewed checkout reset")
                normalized = copy.deepcopy(current)
                normalized["services"][s["bridge_service"]]["image"] = s["expected_bridge_image"]
                normalized["services"][s["bridge_service"]]["env_file"] = envfiles
                check(normalized == desired, "Compose differs beyond the approved image/env_file reset")
                disk[s["bridge_compose"]] = current_bytes
                runtime[s["bridge_compose"]] = desired_text.encode("utf-8")
        if not disk:
            return
        check(self.image(s["bridge_container"]) == s["expected_bridge_image"], "Runtime image changed before reconciliation")
        s["receipt_dir"].mkdir(parents=True, exist_ok=True, mode=0o700)
        check(s["receipt_dir"].stat().st_mode & 0o077 == 0, "Receipt directory must be private")
        directory = s["receipt_dir"] / ("verified-runtime-source-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        directory.mkdir(mode=0o700)
        for path, data in disk.items():
            (directory / path.name).write_bytes(data)
        self.reconciliation_receipt = directory / "receipt.json"
        report = {"success": False, "services_restarted": False, "bridge_image": s["expected_bridge_image"],
                  "backup": str(directory), "old_disk_sha256": {path.name: digest(data) for path, data in disk.items()},
                  "verified_restore_sha256": {path.name: digest(data) for path, data in runtime.items()}}
        self.reconciliation_receipt.write_text(json.dumps(report, indent=2), encoding="utf-8")
        touched = []
        try:
            check(all(path.read_bytes() == data for path, data in disk.items()), "Disk source changed during reconciliation")
            for path, data in runtime.items():
                touched.append(path)
                write_in_place(path, data)
            check(all(path.read_bytes() == data for path, data in runtime.items()), "Runtime source restoration mismatch")
        except Exception:
            restored = True
            for path in reversed(touched):
                try:
                    write_in_place(path, disk[path])
                except Exception:
                    restored = False
            report["stale_disk_restored"] = restored
            self.reconciliation_receipt.write_text(json.dumps(report, indent=2), encoding="utf-8")
            raise RuntimeError("Source reconciliation failed; no service restarted; see " + str(self.reconciliation_receipt)) from None
        report["success"] = True
        self.reconciliation_receipt.write_text(json.dumps(report, indent=2), encoding="utf-8")

    def deploy(self):
        s = self.s
        check(self.run(["findmnt", "-n", "-o", "TARGET", "/data"]).strip() == "/data", "/data mount missing")
        check(self.image(s["bridge_container"]) == s["expected_bridge_image"], "Bridge image changed")
        check(self.image(s["ow_container"]) == s["expected_ow_image"], "Chat image changed")
        bridge_id = self.run(["docker", "inspect", "--format", "{{.Image}}", s["bridge_container"]]).strip()
        check(re.fullmatch(r"sha256:[a-f0-9]{64}", bridge_id), "Unexpected current image ID")
        check(self.run(["docker", "image", "inspect", "--format", "{{.Id}}", s["expected_bridge_image"]]).strip() == bridge_id, "Bridge tag moved")
        # Refuse replacing an existing release tag; it must remain available for rollback.
        images = self.run(["docker", "image", "ls", "--quiet", s["new_bridge_image"]]).strip()
        check(not images, "New image tag already exists; inspect previous attempt")
        self.reconcile_known_stale_source()
        originals = {}
        release = {}
        for name in CHANGED:
            source = safe_path(str(s["source_dir"] / name), file=True)
            live = safe_path(str(s["provider_dir"] / name), file=True)
            originals[live] = live.read_bytes()
            release[live] = source.read_bytes()
            check(digest(originals[live]) == EXPECTED_SOURCE[name], "Live source hash changed: " + name)
            check(digest(release[live]) == NEW_SOURCE[name], "Unreviewed release source: " + name)
            live_hash = self.run(["docker", "exec", s["bridge_container"], "node", "-e",
                "process.stdout.write(require('node:crypto').createHash('sha256').update(require('node:fs').readFileSync('/app/" + name + "')).digest('hex'))"]).strip()
            check(live_hash == EXPECTED_SOURCE[name], "Running/source mismatch: " + name)
        for key in ("bridge_compose", "candidate_compose"):
            originals[s[key]] = s[key].read_bytes()
        untouched = {s["base_compose"]: s["base_compose"].read_bytes()}
        bridge_doc = originals[s["bridge_compose"]].decode("utf-8")
        candidate_doc = originals[s["candidate_compose"]].decode("utf-8")
        def bridge_update(service):
            check(service.get("image") == s["expected_bridge_image"], "Compose bridge image changed")
            service["image"] = s["new_bridge_image"]
        def chat_update(service):
            check(isinstance(service.get("environment"), dict), "Chat needs mapping-style environment")
            service["environment"].update(REFRESH_ENV)
        replacements = dict(release)
        replacements[s["bridge_compose"]] = patch_service(bridge_doc, s["bridge_service"], bridge_update).encode("utf-8")
        replacements[s["candidate_compose"]] = patch_service(candidate_doc, s["ow_service"], chat_update).encode("utf-8")
        for target in ("bridge", "chat"):
            self.compose(target, "config", "--quiet")
        s["receipt_dir"].mkdir(parents=True, exist_ok=True, mode=0o700)
        check(s["receipt_dir"].stat().st_mode & 0o077 == 0, "Receipt directory must be private")
        receipt = s["receipt_dir"] / "identity-refresh-receipt.json"
        check(not receipt.exists(), "Receipt already exists; do not repeat deployment")
        self.run_dir = s["receipt_dir"] / ("identity-refresh-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        self.run_dir.mkdir(mode=0o700)
        backup = self.run_dir / "backup"
        backup.mkdir(mode=0o700)
        manifest = {}
        for index, (filename, data) in enumerate(originals.items()):
            saved = backup / (str(index) + "-" + filename.name)
            saved.write_bytes(data)
            manifest[str(filename)] = {"backup": str(saved), "sha256": digest(data)}
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (backup / "images.json").write_text(json.dumps({"bridge": s["expected_bridge_image"], "bridge_id": bridge_id,
            "chat": s["expected_ow_image"]}, indent=2), encoding="utf-8")
        build = self.run_dir / "build"
        build.mkdir(mode=0o700)
        for name in CHANGED:
            (build / name).write_bytes(release[s["provider_dir"] / name])
        (build / "Dockerfile").write_text("FROM " + s["expected_bridge_image"] + "\nCOPY --chown=node:node " + " ".join(CHANGED) + " /app/\n", encoding="utf-8")
        self.run(["docker", "build", "--pull=false", "--network=none", "-t", s["new_bridge_image"], build], timeout=300)
        check(self.run(["docker", "image", "inspect", "--format", "{{.Id}}", s["expected_bridge_image"]]).strip() == bridge_id, "Base image tag changed during build")
        for name in CHANGED:
            self.run(["docker", "run", "--rm", "--network=none", "--read-only", "--entrypoint", "node", s["new_bridge_image"], "--check", "/app/" + name])
        touched = []
        restarted = []
        try:
            for filename, data in originals.items():
                check(filename.read_bytes() == data, "Target changed during preflight")
            for filename, data in replacements.items():
                touched.append(filename)
                write_in_place(filename, data)
            for target in ("bridge", "chat"):
                self.compose(target, "config", "--quiet")
            for target in ("bridge", "chat"):
                restarted.append(target)
                self.recreate(target)
            check(self.image(s["bridge_container"]) == s["new_bridge_image"], "Bridge release not active")
            check(self.image(s["ow_container"]) == s["expected_ow_image"], "Chat image changed unexpectedly")
            for filename, data in {**untouched, **replacements}.items():
                check(filename.read_bytes() == data, "Configuration changed during activation")
        except Exception:
            rollback_ok = True
            for filename in reversed(touched):
                try:
                    write_in_place(filename, originals[filename])
                except Exception:
                    rollback_ok = False
            # Never restart against a partially restored configuration/source set.
            if rollback_ok:
                for target in restarted:
                    try:
                        self.compose(target, "config", "--quiet")
                        self.recreate(target)
                        container = s["bridge_container" if target == "bridge" else "ow_container"]
                        expected = s["expected_bridge_image" if target == "bridge" else "expected_ow_image"]
                        check(self.image(container) == expected, "Rollback image mismatch")
                    except Exception:
                        rollback_ok = False
            (self.run_dir / "failure.json").write_text(json.dumps({"success": False, "rollback_healthy": rollback_ok,
                "backup": str(backup)}), encoding="utf-8")
            raise RuntimeError("Activation failed; rollback " + ("healthy" if rollback_ok else "needs operator recovery") + "; backup: " + str(backup)) from None
        result = {"success": True, "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "backup": str(backup), "bridge_image": s["new_bridge_image"], "chat_image": s["expected_ow_image"],
                  "changed_services": [s["bridge_service"], s["ow_service"]], "source_sha256": NEW_SOURCE,
                  "secrets_changed": False, "roles_changed": False, "newapi_changed": False,
                  "fresh_chat_login_required": True,
                  "runtime_source_reconciliation": str(self.reconciliation_receipt) if self.reconciliation_receipt else None}
        receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", help="Existing nonsecret JSON with explicit deployment scope")
    args = parser.parse_args()
    os.umask(0o077)
    Deployer(load_scope(args.scope)).deploy()


if __name__ == "__main__":
    main()
