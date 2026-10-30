#!/usr/bin/env python3
"""Release one approved profile component, preserving unrelated Compose services."""
import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location('profile_deploy_utils', Path(__file__).with_name('profile-deploy-utils.py'))
utils = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = utils
spec.loader.exec_module(utils)

MAIN_FILES = ['core/chat_sso.py', 'services/chat_sso.py', 'schemas/chat_sso.py', 'services/auth.py']
BRIDGE_FILES = ['config.mjs', 'identity-provider.mjs', 'main-bridge.mjs', 'main-profile.mjs']
CHAT_ENV = {
    'MAIN_OAUTH_PROFILE_SYNC_ENABLED': 'true', 'OAUTH_SCOPES': 'openid profile email offline_access',
    'OAUTH_USERNAME_CLAIM': 'preferred_username', 'OAUTH_EMAIL_CLAIM': 'email',
    'OAUTH_UPDATE_NAME_ON_LOGIN': 'true', 'OAUTH_UPDATE_EMAIL_ON_LOGIN': 'true',
    'ENABLE_OAUTH_EMAIL_FALLBACK': 'false', 'OAUTH_MERGE_ACCOUNTS_BY_EMAIL': 'false',
    'ENABLE_OAUTH_ROLE_MANAGEMENT': 'false', 'ENABLE_OAUTH_GROUP_MANAGEMENT': 'false',
}


def check(value, reason):
    if not value:
        raise RuntimeError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    p = Path(value)
    check(p.is_absolute() and '..' not in p.parts, 'invalid_path')
    check(not any(x.is_symlink() for x in (p, *p.parents)), 'symlink_rejected')
    return p


class Release:
    def __init__(self, scope, phase):
        self.phase = phase
        self.s = scope[phase]
        self.source = safe_path(scope['source'])
        self.receipts = safe_path(scope['receipts'])
        check(self.source.is_relative_to(Path('/data')) and self.receipts.is_relative_to(Path('/data')), 'data_disk_required')
        for field in ('container', 'project', 'service', 'new_image'):
            check(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/@-]*', self.s[field]), 'invalid_name')
        check(re.fullmatch(r'sha256:[a-f0-9]{64}', self.s['expected_image_id']), 'invalid_image_id')
        self.compose_file = safe_path(self.s['target_compose'])
        self.stage = 'preflight'
        self.run_dir = None

    def announce(self, stage):
        self.stage = stage
        print(json.dumps({'phase': self.phase, 'stage': stage}), flush=True)

    def run(self, args, timeout=180, cwd=None, input=None):
        result = subprocess.run([str(x) for x in args], capture_output=True, timeout=timeout,
                                cwd=cwd, input=input)
        check(result.returncode == 0, 'subprocess_failed')
        return result.stdout.decode('utf-8').strip()

    def compose(self, *args):
        s = self.s
        command = ['docker', 'compose', '--project-name', s['project'], '--project-directory', s['working_dir']]
        for p in s['env_files']:
            command += ['--env-file', safe_path(p)]
        for p in s['compose_files']:
            command += ['-f', safe_path(p)]
        return self.run(command + list(args), cwd=safe_path(s['working_dir']))

    def image_id(self):
        return self.run(['docker', 'inspect', '--format', '{{.Image}}', self.s['container']])

    def source_hashes(self, files, root, interpreter):
        if interpreter == 'node':
            code = 'const fs=require("node:fs"),c=require("node:crypto");process.stdout.write(JSON.stringify(Object.fromEntries(' + json.dumps(files) + '.map(p=>[p,c.createHash("sha256").update(fs.readFileSync(' + json.dumps(root + '/') + '+p)).digest("hex")]))))'
            command = ['node', '-e', code]
        else:
            code = 'import hashlib,json;from pathlib import Path;r=Path(' + repr(root) + ');print(json.dumps({p:hashlib.sha256((r/p).read_bytes()).hexdigest() for p in ' + repr(files) + '}))'
            command = ['python', '-c', code]
        return json.loads(self.run(['docker', 'exec', self.s['container']] + command))

    def healthy(self):
        seconds = 240 if self.phase == 'chat' else 120
        deadline = time.monotonic() + seconds
        fmt = '{"running":{{.State.Running}},"restarting":{{.State.Restarting}},"dead":{{.State.Dead}},"oom":{{.State.OOMKilled}},"health":"{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"}'
        while True:
            state = json.loads(self.run(['docker', 'inspect', '--format', fmt, self.s['container']]))
            if state['running'] and state['health'] == 'healthy':
                return state
            if state['oom'] or state['dead'] or (not state['running'] and not state['restarting']) or time.monotonic() >= deadline:
                raise RuntimeError('readiness_failed')
            time.sleep(2)

    def build(self):
        s = self.s
        check(not self.run(['docker', 'image', 'ls', '--quiet', s['new_image']]), 'release_tag_already_exists')
        if self.phase == 'main':
            context = self.source / 'main-profile-overlay'
            args = ['--build-arg', 'MAIN_BASE_IMAGE=' + s['expected_image_id']]
        elif self.phase == 'bridge':
            context = self.run_dir / 'bridge-build'
            context.mkdir(mode=0o700)
            for name in BRIDGE_FILES:
                (context / name).write_bytes((self.source / 'identity-bridge' / name).read_bytes())
            (context / 'Dockerfile').write_text('FROM ' + s['expected_image_id'] + '\nCOPY --chown=node:node ' + ' '.join(BRIDGE_FILES) + ' /app/\nRUN node --check /app/config.mjs && node --check /app/identity-provider.mjs && node --check /app/main-bridge.mjs && node --check /app/main-profile.mjs\n')
            args = []
        else:
            context = self.source / 'openwebui-profile-overlay'
            args = []
        self.run(['docker', 'build', '--pull=false', '--network=none', '-t', s['new_image'], *args, context], timeout=300)
        image_id = self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', s['new_image']])
        check(re.fullmatch(r'sha256:[a-f0-9]{64}', image_id), 'invalid_built_image')
        return context, image_id

    def update(self, service, context):
        s = self.s
        check(service.get('image') == s.get('expected_compose_image'), 'compose_image_changed')
        service['image'] = s['new_image']
        service['build'] = {'context': str(context)}
        if self.phase == 'main':
            service['build']['args'] = {'MAIN_BASE_IMAGE': s['expected_image_id']}
        else:
            check(isinstance(service.get('environment'), dict), 'environment_mapping_required')
            service['environment'].update({'OIDC_MAIN_PROFILE_SYNC_ENABLED': 'true'} if self.phase == 'bridge' else CHAT_ENV)

    def deploy(self):
        s = self.s
        self.announce('preflight')
        check(self.run(['findmnt', '-n', '-o', 'TARGET', '/data']) == '/data', 'data_mount_missing')
        check(self.image_id() == s['expected_image_id'], 'runtime_image_changed')
        self.healthy()
        check(self.source_hashes(list(s['expected_source']), s['source_root'], s['interpreter']) == s['expected_source'], 'runtime_source_changed')
        self.compose('config', '--quiet')
        text = self.compose_file.read_bytes().decode('utf-8')
        before_hash = utils.service_hash(text, s['service'])
        old_service = copy.deepcopy(utils.load_yaml(text)['services'][s['service']])
        check(old_service.get('image') == s.get('expected_compose_image'), 'compose_image_changed')
        self.receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
        check(self.receipts.stat().st_mode & 0o077 == 0, 'unsafe_receipt_directory')
        self.run_dir = self.receipts / (self.phase + '-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        self.run_dir.mkdir(mode=0o700)
        self.announce('build')
        context, image_id = self.build()
        check(self.image_id() == s['expected_image_id'], 'runtime_changed_during_build')
        self.announce('activate')
        changed = utils.cas_service_update(self.compose_file, s['service'], before_hash,
            lambda service: self.update(service, context), self.run_dir / 'backup')
        activated_hash = changed['after_service_sha256']
        restarted = False
        try:
            self.compose('config', '--quiet')
            restarted = True
            self.compose('up', '-d', '--no-deps', '--no-build', '--pull', 'never', s['service'])
            self.announce('health')
            self.healthy()
            check(self.image_id() == image_id, 'running_image_mismatch')
            actual = self.source_hashes(list(s['desired_source']), s['source_root'], s['interpreter'])
            check(actual == s['desired_source'], 'running_source_mismatch')
        except Exception as error:
            self.announce('rollback')
            restored = False
            try:
                def restore(service):
                    service.clear()
                    service.update(copy.deepcopy(old_service))
                utils.cas_service_update(self.compose_file, s['service'], activated_hash, restore, self.run_dir / 'rollback-backup')
                self.compose('config', '--quiet')
                if restarted:
                    self.compose('up', '-d', '--no-deps', '--no-build', '--pull', 'never', s['service'])
                    self.healthy()
                    check(self.image_id() == s['expected_image_id'], 'rollback_image_mismatch')
                restored = True
            except Exception:
                pass
            receipt = {'success': False, 'phase': self.phase, 'failure_type': type(error).__name__,
                       'rollback_healthy': restored, 'receipt_dir': str(self.run_dir)}
            (self.run_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2))
            print(json.dumps(receipt), flush=True)
            raise RuntimeError('activation_failed') from None
        result = {'success': True, 'phase': self.phase, 'service': s['service'], 'image': s['new_image'],
            'image_id': image_id, 'source_sha256': actual, 'receipt_dir': str(self.run_dir),
            'other_services_changed': False, 'secrets_changed': False, 'model_requests': 0}
        (self.run_dir / 'receipt.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope')
    parser.add_argument('phase', choices=['main', 'bridge', 'chat'])
    args = parser.parse_args()
    os.umask(0o077)
    release = Release(json.loads(Path(args.scope).read_text()), args.phase)
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'phase': args.phase, 'stage': release.stage,
                          'failure_type': type(error).__name__, 'detail': 'Deployment stopped; no secret diagnostics emitted'}), flush=True)
        raise SystemExit(1) from None
