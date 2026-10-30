"""The recovery release must not reapply the older profile configuration."""
import copy
import importlib.util
from pathlib import Path, PurePosixPath
import unittest

spec = importlib.util.spec_from_file_location('recovery_release', Path(__file__).with_name('deploy-model-auth-recovery.py'))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.instance = release.RecoveryRelease.__new__(release.RecoveryRelease)
        self.instance.s = {'expected_compose_image': 'chat:before', 'new_image': 'chat:after'}

    def test_existing_environment_and_other_service_fields_are_preserved(self):
        service = {
            'image': 'chat:before', 'build': {'context': '/old'},
            'environment': {'MAIN_OAUTH_PROFILE_SYNC_ENABLED': 'false', 'CUSTOM_SETTING': 'unchanged'},
            'env_file': ['/data/example/runtime.env'],
            'volumes': [{'type': 'bind', 'source': '/data/example/chat', 'target': '/app/backend/data'}],
            'ports': ['127.0.0.1:18080:8080'], 'networks': ['existing'], 'restart': 'unless-stopped',
        }
        expected = copy.deepcopy(service)
        self.instance.update(service, PurePosixPath('/data/example/release'))
        self.assertEqual(service['image'], 'chat:after')
        self.assertEqual(service['build'], {'context': '/data/example/release', 'dockerfile': 'Dockerfile.recovery'})
        for key in expected.keys() - {'image', 'build'}:
            self.assertEqual(service[key], expected[key], key)

    def test_no_environment_is_created_and_image_mismatch_stops_before_mutation(self):
        service = {'image': 'chat:before'}
        self.instance.update(service, PurePosixPath('/data/example/release'))
        self.assertNotIn('environment', service)
        unrelated = {'image': 'chat:changed', 'environment': {'CUSTOM_SETTING': 'unchanged'}}
        before = copy.deepcopy(unrelated)
        with self.assertRaises(RuntimeError): self.instance.update(unrelated, PurePosixPath('/data/example/release'))
        self.assertEqual(unrelated, before)


if __name__ == '__main__': unittest.main()
