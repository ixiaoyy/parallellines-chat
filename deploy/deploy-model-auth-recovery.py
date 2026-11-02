"""Build and activate only the Chat authorization-recovery overlay."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('profile_release', Path(__file__).with_name('deploy-profile-sync.py'))
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


class RecoveryRelease(base.Release):
    def build(self):
        base.check(self.phase == 'chat', 'only_chat_supported')
        context = self.source / 'openwebui-profile-overlay'
        assets = context / 'frontend-build'
        manifest = json.loads((context / 'frontend-build-manifest.json').read_text())
        base.check(manifest.get('upstream_commit') == 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca', 'frontend_upstream_mismatch')
        expected = manifest.get('files', {})
        base.check(expected and 'index.html' in expected, 'frontend_manifest_missing')
        actual = {}
        for path in assets.rglob('*'):
            base.check(not path.is_symlink(), 'frontend_symlink_rejected')
            if path.is_file(): actual[path.relative_to(assets).as_posix()] = base.digest(path.read_bytes())
        base.check(actual == expected, 'frontend_artifact_changed')
        base.check(not self.run(['docker', 'image', 'ls', '--quiet', self.s['new_image']]), 'release_tag_already_exists')
        self.run(['docker', 'build', '--pull=false', '--network=none', '-f', context / 'Dockerfile.recovery',
                  '-t', self.s['new_image'], context], timeout=300)
        image_id = self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.s['new_image']])
        base.check(re.fullmatch(r'sha256:[a-f0-9]{64}', image_id), 'invalid_built_image')
        return context, image_id

    def update(self, service, context):
        base.check(service.get('image') == self.s.get('expected_compose_image'), 'compose_image_changed')
        service['image'] = self.s['new_image']
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.recovery'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope')
    args = parser.parse_args()
    os.umask(0o077)
    release = RecoveryRelease(json.loads(Path(args.scope).read_text()), 'chat')
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'phase': 'chat', 'stage': release.stage,
                          'failure_type': type(error).__name__, 'detail': 'Recovery release stopped; no secret diagnostics emitted'}), flush=True)
        raise SystemExit(1) from None
