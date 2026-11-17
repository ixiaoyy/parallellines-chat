"""仅发布已审核的 Chat 个人连接增量，保留配置、数据及其他服务。"""
import copy
from contextlib import closing
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

spec = importlib.util.spec_from_file_location('personal_profile_release', Path(__file__).with_name('deploy-profile-sync.py'))
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


RUNTIME_PROBE = r'''
import base64,json,os
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
allowed={'WEBUI_SECRET_KEY','WEBUI_JWT_SECRET_KEY','WEBUI_SECRET_KEY_FILE','DATA_DIR','DATABASE_URL','DATABASE_TYPE'}
runtime={}
for item in Path('/proc/1/environ').read_bytes().split(b'\0'):
 name,sep,value=item.partition(b'=')
 if sep and name.decode(errors='ignore') in allowed: runtime[name.decode()]=value.decode()
secret=runtime.get('WEBUI_SECRET_KEY') or runtime.get('WEBUI_JWT_SECRET_KEY') or ''
configured=os.getenv('WEBUI_SECRET_KEY') or os.getenv('WEBUI_JWT_SECRET_KEY') or ''
source='environment' if configured and configured==secret else 'file'
keyfile=Path(runtime.get('WEBUI_SECRET_KEY_FILE') or '.webui_secret_key')
if not keyfile.is_absolute(): keyfile=Path('/app/backend')/keyfile
same_file=False
if source=='file' and keyfile.is_file() and not keyfile.is_symlink():
 same_file=keyfile.read_text().strip()==secret
url=runtime.get('DATABASE_URL') or 'sqlite:///'+str(Path(runtime.get('DATA_DIR') or '/app/backend/data')/'webui.db')
sqlite=url.startswith('sqlite:///') and runtime.get('DATABASE_TYPE','sqlite').startswith('sqlite') and '?' not in url
db=Path(url[len('sqlite:///'):]).resolve() if sqlite else None
out={'key_present':bool(secret),'key_source':source,'key_file':str(keyfile.resolve()) if source=='file' else None,'key_file_matches':same_file,'sqlite':sqlite,'database_path':str(db) if db else None}
if secret:
 key=HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=b'pl-personal-deploy-key-continuity-v1').derive(secret.encode())
 cipher=Fernet(base64.urlsafe_b64encode(key))
 proof=os.getenv('PL_KEY_CONTINUITY_PROOF')
 if proof:
  try: out['same_key']=cipher.decrypt(proof.encode())==b'personal-connections-key-continuity'
  except Exception: out['same_key']=False
 else: out['proof']=cipher.encrypt(b'personal-connections-key-continuity').decode()
print(json.dumps(out))
'''


def mounted_path(path, mounts):
    """将明确应用路径映射到已有持久挂载；拒绝临时挂载和未知数据位置。"""
    path = Path(path)
    choices = [m for m in mounts if m.get('Type') in ('bind', 'volume')
               and path.is_relative_to(Path(m['Destination']))]
    base.check(choices, 'persistent_mount_missing')
    mount = max(choices, key=lambda m: len(m['Destination']))
    source = base.safe_path(mount['Source'])
    base.check(source.is_relative_to(Path('/data')), 'data_disk_mount_required')
    return source / path.relative_to(Path(mount['Destination']))


def database_state(path):
    """只读核对 SQLite 类型、原生开关和密文数量，不返回用户记录或配置内容。"""
    path = base.safe_path(path)
    base.check(path.is_file(), 'sqlite_database_missing')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        base.check('user' in tables and 'config' in tables, 'native_database_schema_required')
        cipher_count = db.execute("SELECT COUNT(*) FROM user WHERE settings LIKE ?", ('%plpc:v1:%',)).fetchone()[0]
        columns = {row[1] for row in db.execute('PRAGMA table_info(config)')}
        base.check({'key', 'value'}.issubset(columns), 'native_config_schema_changed')
        row = db.execute('SELECT value FROM config WHERE key = ?', ('direct.enable',)).fetchone()
        enabled = json.loads(row[0]) if row else None
        return {'encrypted_credential_rows': cipher_count, 'direct_connections_persisted': enabled}


class PersonalConnectionsRelease(base.Release):
    """复用服务级 CAS/私有备份和健康能力；仅扩展增量校验与密文安全恢复。"""

    def probe(self, proof=None):
        """容器内比较密钥来源和连续性，秘密及其哈希都不离开容器。"""
        command = ['docker', 'exec']
        if proof:
            command += ['-e', 'PL_KEY_CONTINUITY_PROOF=' + proof]
        command += [self.s['container'], 'python', '-B', '-c', RUNTIME_PROBE]
        return json.loads(self.run(command, timeout=30))

    def preflight(self):
        """只读核对当前镜像/源/挂载/稳定密钥；不修改开关或数据库。"""
        base.check(self.phase == 'chat' and self.s['source_root'] == '/app' and self.s['interpreter'] == 'python', 'personal_chat_scope_required')
        base.check(self.run(['findmnt', '-n', '-o', 'TARGET', '/data']) == '/data', 'data_mount_missing')
        base.check(self.image_id() == self.s['expected_image_id'], 'runtime_image_changed')
        self.healthy()
        base.check(self.source_hashes(list(self.s['expected_source']), '/app', 'python') == self.s['expected_source'], 'runtime_source_changed')
        self.compose('config', '--quiet')
        text = self.compose_file.read_text(encoding='utf-8')
        service = copy.deepcopy(base.utils.load_yaml(text)['services'][self.s['service']])
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        proof = self.probe()
        base.check(proof.get('key_present') and proof.get('proof'), 'stable_webui_secret_required')
        mounts = json.loads(self.run(['docker', 'inspect', '--format', '{{json .Mounts}}', self.s['container']]))
        if proof['key_source'] == 'file':
            base.check(proof['key_file_matches'], 'runtime_key_file_mismatch')
            mounted_path(proof['key_file'], mounts)
        else:
            environment = service.get('environment') or {}
            names = set(environment) if isinstance(environment, dict) else {str(x).partition('=')[0] for x in environment}
            base.check('WEBUI_SECRET_KEY' in names or 'WEBUI_JWT_SECRET_KEY' in names or service.get('env_file'), 'persistent_secret_source_unconfirmed')
        base.check(proof.get('sqlite') and proof.get('database_path'), 'sqlite_database_required')
        self.database = mounted_path(proof['database_path'], mounts)
        self.database_snapshot = database_state(self.database)
        base.check(self.database_snapshot['encrypted_credential_rows'] == 0, 'preexisting_personal_cipher_requires_review')
        self.key_proof = proof['proof']
        return text, service

    def build(self):
        """只从当前完整镜像离线叠加已核前后清单和公开前端，不安装生产依赖。"""
        context = base.safe_path(self.s['frontend_context'])
        base.check({p.name for p in context.iterdir()} == {'Dockerfile.personal-connections', '.dockerignore', 'frontend-build-manifest.json', 'frontend-build', 'personal-connections'}, 'personal_context_inventory_changed')
        for p in context.rglob('*'):
            base.check(not p.is_symlink(), 'personal_context_symlink_rejected')
        dockerfile = self.source / 'openwebui-profile-overlay/Dockerfile.personal-connections'
        docker = dockerfile.read_bytes()
        base.check((context / dockerfile.name).read_bytes() == docker, 'personal_dockerfile_changed')
        feature = self.source / 'openwebui-profile-overlay/personal-connections'
        required = set(re.findall(r'personal-connections/([a-z_]+\.py|manifest\.json)', docker.decode()))
        base.check(required and 'manifest.json' in required and 'apply.py' in required, 'personal_copy_inventory_missing')
        base.check({p.name for p in (context / 'personal-connections').iterdir()} == required, 'personal_backend_context_changed')
        for name in required:
            base.check((context / 'personal-connections' / name).read_bytes() == (feature / name).read_bytes(), 'personal_backend_input_changed')
        feature_bytes = (feature / 'manifest.json').read_bytes()
        feature_manifest = json.loads(feature_bytes)
        public_bytes = (context / 'frontend-build-manifest.json').read_bytes()
        base.check(base.digest(public_bytes) == self.s['frontend_manifest_sha256'], 'personal_frontend_manifest_changed')
        public_manifest = json.loads(public_bytes)
        base.check(public_manifest.get('upstream_commit') == feature_manifest.get('upstream_commit') == 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca', 'personal_upstream_changed')
        base.check(public_manifest.get('feature_manifests', {}).get('personal-connections') == base.digest(feature_bytes), 'personal_frontend_recipe_changed')
        expected = public_manifest.get('files', {})
        base.check(expected and 'index.html' in expected and all(not p.endswith('.map') and not p.startswith('pyodide/') for p in expected), 'public_frontend_manifest_required')
        assets = context / 'frontend-build'
        actual = {p.relative_to(assets).as_posix(): base.digest(p.read_bytes()) for p in assets.rglob('*') if p.is_file()}
        base.check(actual == expected, 'personal_frontend_inventory_changed')
        backend = {p: v for p, v in feature_manifest['files'].items() if p.startswith('backend/')}
        for p, value in backend.items():
            if value['before_sha256'] is not None:
                base.check(self.s['expected_source'].get(p) == value['before_sha256'], 'personal_backend_before_changed')
        self.public_manifest_sha = self.s['frontend_manifest_sha256']
        self.s['desired_source'] = {**self.s['expected_source'], **{p: v['after_sha256'] for p, v in backend.items()}, 'build/index.html': expected['index.html']}
        tag = self.s['new_image']
        for value in (tag, tag + '-base'):
            base.check(not self.run(['docker', 'image', 'ls', '--quiet', value]), 'personal_image_tag_exists')
        self.run(['docker', 'image', 'tag', self.s['expected_image_id'], tag + '-base'])
        self.run(['docker', 'build', '--pull=false', '--network=none', '--build-arg', 'CHAT_BASE_IMAGE=' + tag + '-base', '-f', context / dockerfile.name, '-t', tag, context], timeout=600)
        image = self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag])
        base.check(re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'personal_image_id_invalid')
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag + '-base']) == self.s['expected_image_id'], 'personal_base_image_changed')
        # 用空临时数据目录检查真实镜像内的新加密/模型模块；不挂生产数据、不连接网络。
        probe = (
            'from open_webui.models.users import UserModel\n'
            'from open_webui.utils.main_personal_connections import encrypt_personal_connection_settings as seal,decrypt_personal_connection_settings as open_settings\n'
            'settings={"ui":{"directConnections":{"OPENAI_API_KEYS":["synthetic-candidate-key"]}}}\n'
            'encrypted=seal(settings,"candidate-user","synthetic-candidate-secret")\n'
            'assert "synthetic-candidate-key" not in str(encrypted)\n'
            'assert open_settings(encrypted,"candidate-user","synthetic-candidate-secret")==settings\n'
            'print("personal_candidate_modules_ok")\n'
        )
        output = self.run(['docker', 'run', '--rm', '--network=none', '--read-only', '--memory=1536m', '--cpus=1', '--pids-limit=256',
                          '--tmpfs', '/tmp:rw,size=128m,mode=1777', '-e', 'WEBUI_SECRET_KEY=synthetic-candidate-secret',
                          '-e', 'DATA_DIR=/tmp/chat-candidate', '-e', 'DATABASE_URL=sqlite:////tmp/chat-candidate/webui.db',
                          '-e', 'ENABLE_DB_MIGRATIONS=false', '--entrypoint', 'python', tag, '-B', '-c', probe], timeout=90)
        base.check(output.endswith('personal_candidate_modules_ok'), 'personal_candidate_module_probe_failed')
        return context, image

    def update(self, service, context):
        """只换 Chat image/build；不改 command、环境、挂载、网络、原生开关和模型配置。"""
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        service['image'] = self.s['new_image']
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.personal-connections', 'args': {'CHAT_BASE_IMAGE': self.s['new_image'] + '-base'}}

    def backup(self):
        """对已有 SQLite 做有界一致备份；源只读，不停服务或回灌数据库。"""
        target = self.run_dir / 'webui-before.db'
        base.check(not target.exists(), 'personal_database_backup_exists')
        deadline = time.monotonic() + 120
        def progress(status, remaining, total):
            base.check(time.monotonic() < deadline, 'personal_database_backup_timeout')
        with closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=5)) as source:
            with closing(sqlite3.connect(target)) as destination:
                os.chmod(target, 0o600)
                source.backup(destination, pages=256, progress=progress, sleep=0.05)
                base.check(destination.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'personal_database_backup_invalid')
                destination.commit()

    def healthy(self):
        """沿用 readiness；激活后核全部公开资产、后端以及同一密钥，不发模型请求。"""
        state = super().healthy()
        if self.stage == 'health':
            program = (
                'import hashlib,json;from pathlib import Path\n'
                'root=Path("/app/build");p=Path("/app/frontend-personal-connections-manifest.json")\n'
                'assert hashlib.sha256(p.read_bytes()).hexdigest()==' + repr(self.public_manifest_sha) + '\n'
                'manifest=json.loads(p.read_text())\n'
                'for name,expected in manifest["files"].items():\n'
                ' target=root/name\n'
                ' assert target.resolve().is_relative_to(root) and not target.is_symlink()\n'
                ' assert hashlib.sha256(target.read_bytes()).hexdigest()==expected\n'
                'assert not any((root/"_app").rglob("*.map"))\n'
                'print(json.dumps({"files_verified":len(manifest["files"]),"compiled_maps":0}))\n'
            )
            self.delivery = json.loads(self.run(['docker', 'exec', self.s['container'], 'python', '-B', '-c', program], timeout=120))
            base.check(self.probe(self.key_proof).get('same_key'), 'personal_secret_continuity_failed')
        return state

    def recover(self, old_service, activated_hash, candidate_image, restarted):
        """在既有服务 CAS 锁内排空候选后决定是否可退旧读取器，避免密文写入竞态。"""
        state = {'rollback_healthy': False, 'drain_requested': False, 'drain_confirmed': False,
                 'encrypted_credential_rows': None, 'old_definition_restored': False,
                 'original_container_observed': False, 'original_container_running': False,
                 'candidate_restarted': False, 'candidate_health': None, 'recovery_error_type': None}
        container_id = None

        def current_candidate(allow_original=False):
            template = '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"running":{{json .State.Running}}}'
            current = json.loads(self.run(['docker', 'inspect', '--format', template, self.s['container']]))
            allowed_images = {candidate_image}
            if allow_original:
                allowed_images.add(self.s['expected_image_id'])
            base.check(current.get('name') == '/' + self.s['container']
                       and current.get('image') in allowed_images
                       and re.fullmatch(r'[0-9a-f]{64}', current.get('id', '')), 'rollback_candidate_changed')
            return current

        def restore(service):
            nonlocal container_id
            if restarted:
                # 回调在既有 Compose inode 锁下执行，CAS 前像变更会在任何 stop 之前拒绝。
                current = current_candidate(allow_original=True)
                if current['image'] == self.s['expected_image_id']:
                    # up 仅表示已尝试；候选替换前失败时，原容器不能被当作候选停止。
                    state['original_container_observed'] = True
                    state['original_container_running'] = current.get('running') is True
                else:
                    container_id = current['id']
                    state['drain_requested'] = True
                    self.run(['docker', 'stop', '--time', '30', container_id], timeout=45)
                    base.check(self.run(['docker', 'inspect', '--format', '{{.State.Running}}', container_id]) == 'false', 'rollback_candidate_not_stopped')
                    state['drain_confirmed'] = True
                    # 此时该 Chat 唯一候选已停止接受写入；必须在这里才判断新密文。
                    state['encrypted_credential_rows'] = database_state(self.database)['encrypted_credential_rows']
                    base.check(state['encrypted_credential_rows'] == 0, 'rollback_personal_cipher_present')
            else:
                base.check(self.image_id() == self.s['expected_image_id'], 'rollback_original_runtime_changed')
                state['encrypted_credential_rows'] = 0
            service.clear()
            service.update(copy.deepcopy(old_service))

        try:
            base.utils.cas_service_update(self.compose_file, self.s['service'], activated_hash, restore, self.run_dir / 'rollback-backup')
            state['old_definition_restored'] = True
            self.compose('config', '--quiet')
            self.stage = 'rollback'
            if restarted and not state['original_container_running']:
                self.compose('up', '-d', '--no-deps', '--no-build', '--pull', 'never', self.s['service'])
            self.healthy()
            base.check(self.image_id() == self.s['expected_image_id'], 'rollback_image_mismatch')
            state['rollback_healthy'] = True
        except Exception as error:
            state['recovery_error_type'] = type(error).__name__
            if state['drain_requested'] and not state['old_definition_restored']:
                # 新密文/未知 DB 状态不能退旧版。用同一激活前像的 no-op CAS 重起候选，
                # 若他人已接管服务则拒绝重起；不改变其定义或任何数据库内容。
                try:
                    def restart_candidate(service):
                        base.check(current_candidate()['id'] == container_id, 'candidate_recovery_container_changed')
                        self.compose('config', '--quiet')
                        self.compose('up', '-d', '--no-deps', '--no-build', '--pull', 'never', self.s['service'])
                        state['candidate_restarted'] = True
                        state['candidate_health'] = base.Release.healthy(self)
                    base.utils.cas_service_update(self.compose_file, self.s['service'], activated_hash, restart_candidate, self.run_dir / 'candidate-recovery-backup')
                except Exception as recovery_error:
                    state['candidate_recovery_error_type'] = type(recovery_error).__name__
        state['rollback_blocked_by_cipher'] = (
            not state['old_definition_restored']
            and (state['encrypted_credential_rows'] is None or state['encrypted_credential_rows'] > 0)
        )
        state['candidate_retained'] = not state['old_definition_restored']
        state['forward_repair_required'] = not state['rollback_healthy']
        return state

    def deploy(self):
        """有条件恢复继承原服务；新密文出现后不得退回旧解密器，不恢复任何数据库。"""
        self.announce('preflight')
        text, old_service = self.preflight()
        before_hash = base.utils.service_hash(text, self.s['service'])
        self.receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
        base.check(self.receipts.stat().st_mode & 0o077 == 0, 'unsafe_receipt_directory')
        self.run_dir = self.receipts / ('chat-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        self.run_dir.mkdir(mode=0o700)
        self.announce('build')
        context, image = self.build()
        base.check(self.image_id() == self.s['expected_image_id'], 'runtime_changed_during_build')
        self.announce('backup')
        self.backup()
        base.check(database_state(self.database)['encrypted_credential_rows'] == 0, 'cipher_appeared_before_activation')
        self.announce('activate')
        changed = base.utils.cas_service_update(self.compose_file, self.s['service'], before_hash, lambda service: self.update(service, context), self.run_dir / 'backup')
        restarted = False
        try:
            self.compose('config', '--quiet')
            restarted = True
            self.compose('up', '-d', '--no-deps', '--no-build', '--pull', 'never', self.s['service'])
            self.announce('health')
            self.healthy()
            base.check(self.image_id() == image, 'running_image_mismatch')
            base.check(self.source_hashes(list(self.s['desired_source']), '/app', 'python') == self.s['desired_source'], 'running_source_mismatch')
        except Exception as error:
            recovery = self.recover(old_service, changed['after_service_sha256'], image, restarted)
            receipt = {'success': False, 'stage': self.stage, 'failure_type': type(error).__name__, **recovery,
                       'database_restored': False, 'receipt_dir': str(self.run_dir)}
            (self.run_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
            print(json.dumps(receipt), flush=True)
            raise RuntimeError('personal_activation_failed') from None
        receipt = {'success': True, 'image': self.s['new_image'], 'image_id': image, 'source_sha256': self.s['desired_source'],
                   'frontend_delivery': self.delivery, 'stable_secret_preserved': True, 'database_backup': 'webui-before.db',
                   'native_direct_connections': self.database_snapshot['direct_connections_persisted'], 'configuration_changed': False,
                   'database_restored': False, 'model_requests': 0, 'other_services_changed': False, 'receipt_dir': str(self.run_dir)}
        (self.run_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in receipt.items() if k not in ('image_id', 'source_sha256')}), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope')
    args = parser.parse_args()
    os.umask(0o077)
    release = PersonalConnectionsRelease(json.loads(Path(args.scope).read_text()), 'chat')
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'stage': release.stage, 'failure_type': type(error).__name__, 'receipt_dir': str(release.run_dir) if release.run_dir else None}), flush=True)
        raise SystemExit(1) from None
