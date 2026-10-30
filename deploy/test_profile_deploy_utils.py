"""Only synthetic Compose files in temporary directories; no Docker or network."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest

MODULE = Path(__file__).with_name('profile-deploy-utils.py')
spec = importlib.util.spec_from_file_location('profile_deploy_utils', MODULE)
utils = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = utils
spec.loader.exec_module(utils)

SAMPLE = '''# Keep this leading comment.
services:
  chat:
    image: chat:old # Target-owned comment
    environment:
      LABEL: "synthetic only"
    ports: !reset []
    volumes: !override
      - /data/chat:/app/data

  # Keep the API introduction.
  api:
    image: api:old # Keep this formatting exactly.
    ports: !reset []
volumes:
  shared: {}
'''


class UtilsTests(unittest.TestCase):
    def test_target_patch_retains_other_bytes_and_compose_tags(self):
        result = utils.patch_service(SAMPLE, 'chat', lambda service: service.update(image='chat:new'))
        prefix = SAMPLE[:SAMPLE.index('    image: chat:old')]
        suffix = SAMPLE[SAMPLE.index('\n  # Keep the API'):]
        self.assertTrue(result.startswith(prefix))
        self.assertTrue(result.endswith(suffix))
        doc = utils.load_compose(result)
        self.assertEqual(doc['services']['chat']['image'], 'chat:new')
        self.assertEqual(doc['services']['chat']['ports'], utils.TaggedValue('!reset', []))
        self.assertEqual(doc['services']['chat']['volumes'],
                         utils.TaggedValue('!override', ['/data/chat:/app/data']))
        self.assertEqual(utils.service_hash(SAMPLE, 'api'), utils.service_hash(result, 'api'))

    def test_scalar_map_tags_and_multiline_final_field(self):
        text = 'services:\n  chat:\n    ports: !reset null\n    environment: !override {A: value}\n    command: |\n      first\n      # part of the command\n  api:\n    image: old\n'
        result = utils.patch_service(text, 'chat', lambda service: service.update(image='new'))
        doc = utils.load_compose(result)
        self.assertEqual(doc['services']['chat']['ports'], utils.TaggedValue('!reset', None))
        self.assertEqual(doc['services']['chat']['environment'], utils.TaggedValue('!override', {'A': 'value'}))
        self.assertEqual(doc['services']['chat']['command'], 'first\n# part of the command\n')
        self.assertTrue(result.endswith('  api:\n    image: old\n'))

    def test_compose_tag_scalar_types_survive_round_trip(self):
        for scalar, value in [('null', None), ('false', False), ('23', 23), ("'true'", 'true'), ("'23'", '23')]:
            text = 'services:\n  chat:\n    property: !override ' + scalar + '\n    image: old\n'
            result = utils.patch_service(text, 'chat', lambda service: service.update(image='new'))
            self.assertEqual(utils.load_yaml(result)['services']['chat']['property'],
                             utils.TaggedValue('!override', value))

    def test_flow_nested_values_unicode_crlf_no_final_newline(self):
        text = 'services:\r\n  chat:\r\n    labels: {display: 合成}\r\n    image: old\r\n  api:\r\n    image: keep'
        result = utils.patch_service(text, 'chat', lambda service: service.update(image='new'))
        self.assertTrue(result.endswith('  api:\r\n    image: keep'))
        self.assertNotIn('\n', result.replace('\r\n', ''))
        self.assertEqual(utils.load_compose(result)['services']['chat']['labels']['display'], '合成')
        final = utils.patch_service(result, 'api', lambda service: service.update(image='last'))
        self.assertFalse(final.endswith('\n'))
        self.assertEqual(utils.service_hash(result, 'chat'), utils.service_hash(final, 'chat'))

    def test_duplicate_alias_unknown_tag_and_flow_service_rejected_without_values(self):
        invalid = [SAMPLE.replace('    image: chat:old', '    image: duplicate\n    image: chat:old'),
                   SAMPLE.replace('    ports: !reset []', '    ports: !unknown []', 1),
                   'services:\n  chat: {image: secret-value}\n',
                   'services:\n  chat: &secret_alias\n    image: secret-value\n  api: *secret_alias\n']
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(utils.ServicePatchError) as error:
                utils.patch_service(text, 'chat', lambda service: service.update(image='new'))
            self.assertNotIn('secret-value', str(error.exception))

    def test_compare_and_swap_private_backup_and_same_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'compose.yaml'
            path.write_bytes(SAMPLE.encode('utf-8'))
            before = path.stat().st_ino
            @contextmanager
            def test_lock(stream):
                self.assertTrue(stream.readable())
                yield
            receipt = utils.cas_service_update(path, 'chat', utils.service_hash(SAMPLE, 'chat'),
                lambda service: service.update(image='chat:new'), Path(directory) / 'backup', lock_factory=test_lock)
            self.assertEqual(path.stat().st_ino, before)
            self.assertEqual(Path(receipt['backup_path']).read_bytes(), SAMPLE.encode('utf-8'))
            self.assertEqual(receipt['after_file_sha256'], utils.digest(path.read_bytes()))
            self.assertNotIn('synthetic only', str(receipt))
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(Path(receipt['backup_path']).stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE((Path(directory) / 'backup').stat().st_mode), 0o700)

    def test_lock_rereads_latest_other_service_and_cas_rollback_preserves_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'compose.yaml'
            path.write_bytes(SAMPLE.encode('utf-8'))
            backups = Path(directory) / 'backup'
            @contextmanager
            def concurrent_other_update(stream):
                latest = utils.patch_service(SAMPLE, 'api', lambda service: service.update(image='api:new'))
                path.write_bytes(latest.encode('utf-8'))
                yield
            receipt = utils.cas_service_update(path, 'chat', utils.service_hash(SAMPLE, 'chat'),
                lambda service: service.update(image='chat:new'), backups, lock_factory=concurrent_other_update)
            latest = path.read_text(encoding='utf-8')
            expected_api = utils.service_hash(latest, 'api')
            original_service = utils.load_compose(SAMPLE)['services']['chat']
            def restore(service):
                service.clear()
                service.update(original_service)
            utils.cas_service_update(path, 'chat', receipt['after_service_sha256'], restore,
                                     backups, lock_factory=no_lock)
            current = path.read_text(encoding='utf-8')
            self.assertEqual(utils.service_hash(current, 'api'), expected_api)
            self.assertEqual(utils.load_compose(current)['services']['chat'], original_service)

    def test_changed_target_refuses_update_and_rollback_without_touch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'compose.yaml'
            changed = utils.patch_service(SAMPLE, 'chat', lambda service: service.update(image='concurrent:new'))
            path.write_bytes(changed.encode('utf-8'))
            with self.assertRaisesRegex(utils.ServicePatchError, 'CAS refused'):
                utils.cas_service_update(path, 'chat', utils.service_hash(SAMPLE, 'chat'),
                    lambda service: service.update(image='wrong'), Path(directory) / 'backup', lock_factory=no_lock)
            self.assertEqual(path.read_bytes(), changed.encode('utf-8'))
            self.assertEqual(list((Path(directory) / 'backup').iterdir()), [])

    def test_multiple_cooperating_writers_preserve_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'compose.yaml'
            path.write_bytes(SAMPLE.encode('utf-8'))
            backups = Path(directory) / 'backup'
            backups.mkdir(mode=0o700)
            lock = threading.Lock()
            errors = []
            @contextmanager
            def lock_factory(stream):
                with lock:
                    yield
            def worker(service):
                try:
                    utils.cas_service_update(path, service, utils.service_hash(SAMPLE, service),
                        lambda data: data.update(image=service + ':new'), backups, lock_factory=lock_factory)
                except Exception as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=worker, args=(name,)) for name in ('chat', 'api')]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            doc = utils.load_compose(path.read_text(encoding='utf-8'))
            self.assertEqual(doc['services']['chat']['image'], 'chat:new')
            self.assertEqual(doc['services']['api']['image'], 'api:new')


@contextmanager
def no_lock(stream):
    yield


if __name__ == '__main__':
    unittest.main(verbosity=2)
