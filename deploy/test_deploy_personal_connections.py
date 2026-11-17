"""Chat 专用部署器的隔离合同；仅临时合成 SQLite，不连接 Docker 或服务器。"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('personal_release', Path(__file__).with_name('deploy-personal-connections.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def database(path, cipher=False):
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE user (settings TEXT)')
        db.execute('CREATE TABLE config (key TEXT PRIMARY KEY,value JSON)')
        db.execute('INSERT INTO user VALUES (?)', (json.dumps({'key': 'plpc:v1:synthetic' if cipher else 'old-setting'}),))
        db.execute('INSERT INTO config VALUES (?,?)', ('direct.enable', 'false'))
        db.commit()


class PersonalDeployTests(unittest.TestCase):
    def test_update_preserves_all_config_except_image_build(self):
        release = object.__new__(module.PersonalConnectionsRelease)
        release.s = {'expected_compose_image': 'chat:old', 'new_image': 'chat:new'}
        service = {'image': 'chat:old', 'command': ['bash', '/app/backend/unified-start.sh'], 'environment': {'UNCHANGED': 'synthetic'}, 'env_file': ['/data/runtime.env'], 'volumes': ['/data/chat:/app/backend/data'], 'networks': ['existing'], 'extra_hosts': ['main:host-gateway']}
        original = copy.deepcopy(service)
        release.update(service, Path('/data/public-context'))
        self.assertEqual({k: v for k, v in service.items() if k not in ('image', 'build')}, {k: v for k, v in original.items() if k not in ('image', 'build')})
        self.assertEqual(service['build']['dockerfile'], 'Dockerfile.personal-connections')
        service['image'] = 'concurrent:changed'
        with self.assertRaises(RuntimeError): release.update(service, Path('/data/public-context'))

    def test_sqlite_state_and_consistent_backup_do_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'webui.db'; database(source)
            before = source.read_bytes()
            state = module.database_state(source)
            self.assertEqual(state, {'encrypted_credential_rows': 0, 'direct_connections_persisted': False})
            release = object.__new__(module.PersonalConnectionsRelease)
            release.database = source; release.run_dir = root
            release.backup()
            self.assertEqual(before, source.read_bytes())
            self.assertEqual(module.database_state(root / 'webui-before.db'), state)
            with self.assertRaises(RuntimeError): release.backup()

    def test_new_cipher_is_detected_without_returning_user_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'webui.db'; database(path, cipher=True)
            state = module.database_state(path)
            self.assertEqual(state['encrypted_credential_rows'], 1)
            self.assertNotIn('synthetic', json.dumps(state))

    def test_runtime_key_probe_returns_only_opaque_continuity_proof(self):
        secret = 'synthetic-key-not-production'
        raw = b'WEBUI_SECRET_KEY=' + secret.encode() + b'\0DATABASE_URL=sqlite:////app/backend/data/webui.db\0'
        def execute(extra=None):
            output = io.StringIO()
            with mock.patch('pathlib.Path.read_bytes', return_value=raw), mock.patch.dict(os.environ, {'WEBUI_SECRET_KEY': secret, **(extra or {})}, clear=True), contextlib.redirect_stdout(output):
                exec(compile(module.RUNTIME_PROBE, '<synthetic runtime probe>', 'exec'), {})
            self.assertNotIn(secret, output.getvalue())
            return json.loads(output.getvalue())
        first = execute()
        self.assertTrue(first['key_present']); self.assertEqual(first['key_source'], 'environment')
        second = execute({'PL_KEY_CONTINUITY_PROOF': first['proof']})
        self.assertTrue(second['same_key'])

    def test_image_build_is_offline_and_uses_full_reviewed_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); release = object.__new__(module.PersonalConnectionsRelease)
            release.source = root / 'source'; context = root / 'context'; feature = release.source / 'openwebui-profile-overlay/personal-connections'
            feature.mkdir(parents=True); (context / 'personal-connections').mkdir(parents=True); (context / 'frontend-build').mkdir()
            docker = b'ARG CHAT_BASE_IMAGE\nFROM ${CHAT_BASE_IMAGE}\nCOPY personal-connections/apply.py personal-connections/manifest.json /tmp/personal-connections/\n'
            (release.source / 'openwebui-profile-overlay/Dockerfile.personal-connections').write_bytes(docker); (context / 'Dockerfile.personal-connections').write_bytes(docker)
            (context / '.dockerignore').write_text('frontend-debug/\n'); (context / 'frontend-build/index.html').write_bytes(b'web')
            manifest = {'upstream_commit': 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca', 'files': {'backend/open_webui/changed.py': {'before_sha256': 'before', 'after_sha256': 'after'}}}
            data = json.dumps(manifest).encode(); (feature / 'manifest.json').write_bytes(data); (feature / 'apply.py').write_text('# reviewed')
            for name in ('manifest.json', 'apply.py'): (context / 'personal-connections' / name).write_bytes((feature / name).read_bytes())
            public = json.dumps({'upstream_commit': manifest['upstream_commit'], 'feature_manifests': {'personal-connections': module.base.digest(data)}, 'files': {'index.html': module.base.digest(b'web')}}).encode()
            (context / 'frontend-build-manifest.json').write_bytes(public)
            release.s = {'frontend_context': str(context), 'expected_source': {'backend/open_webui/changed.py': 'before'}, 'frontend_manifest_sha256': module.base.digest(public), 'new_image': 'chat:personal', 'expected_image_id': 'sha256:' + '1' * 64}
            commands = []
            def run(args, **kwargs):
                commands.append(args)
                if args[1:3] == ['image', 'ls']: return ''
                if args[1:3] == ['image', 'inspect']: return release.s['expected_image_id'] if str(args[-1]).endswith('-base') else 'sha256:' + '2' * 64
                if args[1] == 'run': return 'personal_candidate_modules_ok'
                return ''
            release.run = run
            result, image = release.build()
            self.assertEqual(result, context); self.assertEqual(image, 'sha256:' + '2' * 64)
            build = next(args for args in commands if args[1] == 'build')
            self.assertIn('--network=none', build); self.assertIn('--pull=false', build)
            isolated = next(args for args in commands if args[1] == 'run')
            self.assertIn('--read-only', isolated); self.assertIn('--network=none', isolated)
            self.assertNotIn('-v', isolated)
            self.assertEqual(release.s['desired_source']['backend/open_webui/changed.py'], 'after')
            (context / 'personal-connections/apply.py').write_text('# changed')
            commands.clear()
            with self.assertRaises(RuntimeError): release.build()
            self.assertEqual(commands, [])

    def recovery_fixture(self, root, *, initial_cipher=0, cipher_on_drain=None, database_error=False):
        """真实临时 Compose CAS 配合确定性的容器/数据库时序夹具。"""
        release = object.__new__(module.PersonalConnectionsRelease)
        release.phase = 'chat'; release.stage = 'health'; release.run_dir = root; release.database = root / 'webui.db'
        release.compose_file = root / 'compose.yaml'
        text = 'services:\n  openwebui:\n    image: chat:new\n    environment:\n      KEEP: unchanged\n  unrelated:\n    image: do-not-touch\n'
        release.compose_file.write_text(text, encoding='utf-8')
        release.s = {'container': 'chat', 'service': 'openwebui', 'expected_image_id': 'old-image', 'new_image': 'chat:new', 'expected_compose_image': 'chat:old'}
        state = {'running': True, 'image': 'new-image', 'cipher': initial_cipher, 'events': []}
        cid = 'b' * 64
        def run(args, **kwargs):
            if args[1] == 'stop':
                self.assertEqual(args[-1], cid)
                state['events'].append('stop')
                if cipher_on_drain is not None: state['cipher'] = cipher_on_drain
                state['running'] = False
                return cid
            if args[1] == 'inspect':
                template = args[3]
                if template == '{{.State.Running}}':
                    state['events'].append('stopped-confirmed')
                    return str(state['running']).lower()
                if '"id"' in template:
                    return json.dumps({'id': cid, 'name': '/chat', 'image': state['image'], 'running': state['running']})
                return json.dumps({'running': state['running'], 'restarting': False, 'dead': False, 'oom': False, 'health': 'healthy'})
            raise AssertionError('Unexpected command: ' + repr(args))
        def compose(*args):
            if args[0] == 'up':
                current = module.base.utils.load_yaml(release.compose_file.read_text(encoding='utf-8'))['services']['openwebui']['image']
                state['events'].append('start-' + current)
                state['image'] = 'old-image' if current == 'chat:old' else 'new-image'
                state['running'] = True
            return ''
        def db(_):
            self.assertFalse(state['running'], 'cipher check must happen only after Chat is stopped')
            self.assertIn('stopped-confirmed', state['events'])
            state['events'].append('read-cipher')
            if database_error: raise sqlite3.OperationalError('synthetic database unavailable')
            return {'encrypted_credential_rows': state['cipher']}
        release.run = run; release.compose = compose; release.image_id = lambda: state['image']
        return release, state, db, module.base.utils.service_hash(release.compose_file.read_bytes().decode('utf-8'), 'openwebui')

    def recover_with_real_cas(self, release, activated_hash, database_reader):
        real = module.base.utils.cas_service_update
        def cas(*args, **kwargs):
            return real(*args, **kwargs, lock_factory=lambda stream: contextlib.nullcontext())
        with mock.patch.object(module.base.utils, 'cas_service_update', side_effect=cas), mock.patch.object(module, 'database_state', side_effect=database_reader):
            return release.recover({'image': 'chat:old'}, activated_hash, 'new-image', True)

    def test_cipher_appearing_before_drain_restarts_candidate_not_old_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            release, state, db, digest = self.recovery_fixture(Path(directory), initial_cipher=0, cipher_on_drain=1)
            result = self.recover_with_real_cas(release, digest, db)
            self.assertEqual(state['events'], ['stop', 'stopped-confirmed', 'read-cipher', 'start-chat:new'])
            self.assertTrue(result['rollback_blocked_by_cipher'])
            self.assertTrue(result['candidate_restarted'])
            self.assertTrue(result['candidate_health']['running'])
            self.assertTrue(result['forward_repair_required'])
            self.assertFalse(result['old_definition_restored'])

    def test_zero_cipher_is_read_after_drain_before_old_definition_start(self):
        with tempfile.TemporaryDirectory() as directory:
            release, state, db, digest = self.recovery_fixture(Path(directory))
            result = self.recover_with_real_cas(release, digest, db)
            self.assertEqual(state['events'], ['stop', 'stopped-confirmed', 'read-cipher', 'start-chat:old'])
            self.assertTrue(result['rollback_healthy'])
            self.assertTrue(result['old_definition_restored'])
            self.assertFalse(result['forward_repair_required'])
            self.assertIn('unrelated:\n    image: do-not-touch', release.compose_file.read_text(encoding='utf-8'))

    def test_changed_target_or_image_cannot_be_stopped(self):
        for change in ('compose', 'image'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                release, state, db, digest = self.recovery_fixture(Path(directory))
                if change == 'compose':
                    release.compose_file.write_text(release.compose_file.read_text(encoding='utf-8').replace('chat:new', 'chat:someone-else'), encoding='utf-8')
                else: state['image'] = 'other-image'
                result = self.recover_with_real_cas(release, digest, db)
                self.assertEqual(state['events'], [])
                self.assertFalse(result['drain_requested'])
                self.assertFalse(result['candidate_restarted'])

    def test_unreadable_database_restarts_candidate_without_old_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            release, state, db, digest = self.recovery_fixture(Path(directory), database_error=True)
            result = self.recover_with_real_cas(release, digest, db)
            self.assertEqual(state['events'], ['stop', 'stopped-confirmed', 'read-cipher', 'start-chat:new'])
            self.assertIsNone(result['encrypted_credential_rows'])
            self.assertTrue(result['rollback_blocked_by_cipher'])
            self.assertTrue(result['candidate_restarted'])
            self.assertFalse(result['old_definition_restored'])

    def test_failed_up_before_replacement_restores_definition_without_stopping_original(self):
        with tempfile.TemporaryDirectory() as directory:
            release, state, db, digest = self.recovery_fixture(Path(directory))
            state['image'] = 'old-image'
            result = self.recover_with_real_cas(release, digest, db)
            self.assertEqual(state['events'], [])
            self.assertTrue(result['original_container_observed'])
            self.assertTrue(result['original_container_running'])
            self.assertTrue(result['rollback_healthy'])
            self.assertTrue(result['old_definition_restored'])
            self.assertFalse(result['drain_requested'])
            self.assertIsNone(result['encrypted_credential_rows'])


if __name__ == '__main__': unittest.main()
