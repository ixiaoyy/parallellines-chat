"""归位共享身份桥、跳转旧入口并移除 LibreChat；数据只备份封存，不删除卷。"""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time

import yaml

spec = importlib.util.spec_from_file_location('legacy_redirect', Path(__file__).with_name('legacy-chat-redirect.py'))
redirect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redirect)
LEGACY = Path('/opt/parallellines/deploy/librechat')
NGINX = Path('/opt/parallellines/deploy/nginx/default.conf')
BRIDGE = 'parallellines-chat-chat-oidc-1'
OLD = ['parallellines-chat-librechat-1', 'parallellines-chat-librechat-admin-1', 'parallellines-chat-librechat-mongodb-1']


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def run(args, timeout=120):
    """有界执行，仅返回 stdout；不将可能展开秘密的 Docker 错误输出到日志。"""
    result = subprocess.run([str(x) for x in args], capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'operation_failed')
    return result.stdout.decode().strip()


def inspect(name):
    """读取容器元数据，供身份、挂载和恢复点核对；不输出运行环境值。"""
    return json.loads(run(['docker', 'inspect', name]))[0]


def health():
    """独立核对共享入口和新站健康，不登录、不调用模型。"""
    for host, path in [('chat.pingxingxian.space', '/health'), ('pingxingxian.space', '/auth/oidc/.well-known/openid-configuration'), ('api.pingxingxian.space', '/api/status')]:
        run(['curl', '--noproxy', '*', '-fsS', '--max-time', '10', '--resolve', host + ':443:127.0.0.1', 'https://' + host + path], timeout=12)


def nginx_replace(before, after):
    """保留 bind mount inode；只在完整配置仍为本次基线时写入，拒绝覆盖并发维护。"""
    with NGINX.open('r+b') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        require(stream.read().decode() == before, 'nginx_changed_concurrently')
        require(os.fstat(stream.fileno()).st_ino == NGINX.stat().st_ino, 'nginx_inode_changed')
        stream.seek(0);stream.write(after.encode());stream.truncate();stream.flush();os.fsync(stream.fileno())


def deploy(scope):
    """使用发布范围和固定旧实例，完成后写私有回执；不回灌库或读取凭据值。"""
    os.umask(0o077)
    work = Path(scope['work'])
    require(work.is_relative_to('/data') and work.is_dir(), 'private_release_directory_required')
    require(LEGACY.resolve() == LEGACY and not LEGACY.is_symlink(), 'legacy_path_changed')
    old_bridge = inspect(BRIDGE)
    require(old_bridge['Image'] == scope['bridge_image_id'], 'bridge_image_changed')
    legacy = [inspect(name) for name in OLD]
    require([d['Id'] for d in legacy] == scope['legacy_container_ids'], 'legacy_instances_changed')
    health()
    before = NGINX.read_text();after = redirect.redirect_config(before)
    backup = work / 'backup';backup.mkdir(mode=0o700)
    (backup / 'nginx.before.conf').write_text(before)
    run(['nice', '-n', '15', 'tar', '-czf', backup / 'legacy-code.tar.gz', '-C', LEGACY.parent, LEGACY.name], timeout=120)
    service = yaml.safe_load((LEGACY / 'compose.sso.yaml').read_text())['services']['chat-oidc']
    old_cli_env = [LEGACY / '.env', Path('/opt/parallellines/deploy/chat-sso/.env'), LEGACY / '.runtime/session-release.env']
    old_command = ['docker', 'compose', '--project-name', 'parallellines-chat', '--project-directory', LEGACY]
    for p in old_cli_env:old_command += ['--env-file', p]
    old_command += ['-f', LEGACY / 'compose.production.yaml', '-f', LEGACY / 'compose.sso.yaml']
    identity = Path('/data/deployment/parallellines-identity')
    require(not identity.exists(), 'identity_deployment_exists_inspect_before_retry')
    identity.mkdir(parents=True, mode=0o700);secrets = identity / 'secrets';secrets.mkdir(mode=0o700)
    cli_env = []
    # 不解析或打印秘密；同机原样迁移现有配置输入，只有 service 的已批准字段进入进程。
    for i, p in enumerate(old_cli_env):
        destination = secrets / ('input-' + str(i) + '.env')
        shutil.copy2(p, destination);destination.chmod(0o600);cli_env.append(destination)
    env_files = service.get('env_file', [])
    require(isinstance(env_files, list) and all(Path(p).is_absolute() for p in env_files), 'unexpected_client_env_paths')
    service['env_file'] = []
    for i, p in enumerate(env_files):
        destination = secrets / ('clients-' + str(i) + '.env')
        shutil.copy2(p, destination);destination.chmod(0o600);service['env_file'].append(str(destination))
    service['environment'].pop('CHAT_OIDC_CLIENT_SECRET', None)
    mounts = {m['Destination']:m['Source'] for m in old_bridge['Mounts']}
    require(mounts == {'/data':'/opt/parallellines/deploy/chat-sso/data', '/secrets/signing.jwks.json':'/opt/parallellines/deploy/chat-sso/secrets/signing.jwks.json'}, 'shared_mounts_changed')
    for v in service['volumes']:v['source'] = mounts[v['target']]
    context = work / 'bridge-image';context.mkdir(mode=0o700)
    for filename, expected in scope['before_source'].items():
        actual = run(['docker', 'exec', BRIDGE, 'sha256sum', '/app/' + filename]).split()[0]
        require(actual == expected, 'bridge_source_changed')
        shutil.copy2(Path(scope['source']) / 'identity-bridge' / filename, context / filename)
    base_tag = scope['image'] + '-base'
    require(not run(['docker', 'image', 'ls', '--quiet', scope['image']]), 'image_already_exists')
    run(['docker', 'image', 'tag', scope['bridge_image_id'], base_tag])
    (context / 'Dockerfile').write_text('FROM ' + base_tag + '\nCOPY ' + ' '.join(scope['before_source']) + ' /app/\n')
    run(['docker', 'build', '--pull=false', '--network=none', '-t', scope['image'], context], timeout=180)
    service['image'] = scope['image'];service['build'] = {'context':str(context)}
    document = {'services':{'chat-oidc':service},'networks':{'parallellines':{'external':True,'name':'parallellines_default'}}}
    compose = identity / 'compose.yaml';compose.write_text(yaml.safe_dump(document,sort_keys=False))
    command = ['docker','compose','--project-name','parallellines-chat','--project-directory',identity]
    for p in cli_env:command += ['--env-file',p]
    command += ['-f',compose]
    # 共享 issuer 数据库只做一致私有快照，不恢复旧 grant/令牌记录。
    db = Path(mounts['/data']) / 'oidc.sqlite'
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=5) as source:
        with sqlite3.connect(str(backup/'issuer.sqlite')) as destination:
            deadline=time.monotonic()+30
            def progress(status,remaining,total):
                require(time.monotonic()<deadline,'issuer_backup_deadline_exceeded')
            source.backup(destination,pages=256,sleep=0.05,progress=progress)
    result = {'success':False,'stage':'shared_identity','legacy_data_deleted':False,'model_requests':0,'backup':str(backup)}
    (work/'receipt.json').write_text(json.dumps(result,indent=2))
    redirected = False
    try:
        run(command + ['config','--quiet'])
        run(command + ['up','-d','--no-deps','--no-build','--pull','never','chat-oidc'])
        deadline = time.monotonic()+120
        while inspect(BRIDGE)['State'].get('Health',{}).get('Status') != 'healthy':
            require(time.monotonic()<deadline,'identity_readiness_failed');time.sleep(2)
        for filename,expected in scope['after_source'].items():
            require(run(['docker','exec',BRIDGE,'sha256sum','/app/'+filename]).split()[0]==expected,'identity_delivery_changed')
        health()
        result['stage']='redirect'
        nginx_replace(before,after);redirected=True
        run(['docker','exec','parallellines-nginx-1','nginx','-t'])
        run(['docker','exec','parallellines-nginx-1','nginx','-s','reload']);health()
    except Exception:
        if redirected:
            nginx_replace(after,before);run(['docker','exec','parallellines-nginx-1','nginx','-t']);run(['docker','exec','parallellines-nginx-1','nginx','-s','reload'])
        run(old_command+['up','-d','--no-deps','--no-build','--pull','never','chat-oidc'])
        result['recovery']='original_identity_restored_no_legacy_deletion'
        (work/'receipt.json').write_text(json.dumps(result,indent=2));raise
    result['stage']='archive_and_retire'
    (work/'receipt.json').write_text(json.dumps(result,indent=2))
    require([inspect(n)['Id'] for n in OLD]==scope['legacy_container_ids'],'legacy_instances_changed_before_stop')
    run(['docker','stop','--time','60',*OLD],timeout=190)
    require(all(not inspect(n)['State']['Running'] for n in OLD),'legacy_stop_not_confirmed')
    require(inspect(OLD[2])['State']['ExitCode']==0,'mongo_not_cleanly_stopped')
    volumes = {m['Source'] for d in legacy for m in d['Mounts'] if m['Type']=='volume'}
    volume_root = Path('/var/lib/docker/volumes')
    require(all(Path(p).resolve().is_relative_to(volume_root) for p in volumes),'volume_path_changed')
    run(['nice','-n','15','tar','--sparse','--numeric-owner','-czf',backup/'legacy-data.tar.gz','-C',volume_root,
         *sorted(str(Path(p).relative_to(volume_root)) for p in volumes)],timeout=180)
    with tarfile.open(backup/'legacy-data.tar.gz') as archive:require(bool(archive.getmembers()),'data_archive_empty')
    (backup/'retained-volumes.json').write_text(json.dumps(sorted(volumes),indent=2))
    result['stage']='data_archived';(work/'receipt.json').write_text(json.dumps(result,indent=2))
    require(all(inspect(n)['Id']==expected and not inspect(n)['State']['Running'] for n,expected in zip(OLD,scope['legacy_container_ids'])),'legacy_instances_changed_before_remove')
    run(['docker','rm',*OLD])  # 不加 -v，保留包括 Mongo configdb 在内的全部数据卷。
    result['stage']='containers_removed';(work/'receipt.json').write_text(json.dumps(result,indent=2))
    all_ids = run(['docker','ps','-aq']).split()
    containers = json.loads(run(['docker','inspect',*all_ids])) if all_ids else []
    require(not any(m['Source']==str(LEGACY) or m['Source'].startswith(str(LEGACY)+'/') for d in containers for m in d['Mounts']), 'remaining_legacy_bind_mount')
    require(str(LEGACY) not in inspect(BRIDGE)['Config']['Labels'].get('com.docker.compose.project.config_files',''),'identity_still_uses_legacy_compose')
    # 只删除明确命名且已封存的旧代码；拒绝链接或路径漂移，不删除数据库目录或数据卷。
    require(LEGACY.resolve()==LEGACY and LEGACY.is_dir() and not LEGACY.is_symlink(),'legacy_delete_target_changed')
    shutil.rmtree(LEGACY)
    workflow=Path('/opt/parallellines/.github/workflows/deploy-chat.yml')
    if workflow.exists():shutil.copy2(workflow,backup/'deploy-chat.yml');workflow.unlink()
    for network in ['parallellines-chat_private','parallellines-chat_admin-loopback']:
        data=json.loads(run(['docker','network','inspect',network]))[0]
        if not data.get('Containers'):run(['docker','network','rm',network])
    used_images={d['Image'] for d in containers}
    result['removed_images']=[]
    for d in legacy:
        if d['Image'] not in used_images:
            p=subprocess.run(['docker','image','rm',d['Config']['Image']],capture_output=True,timeout=30)
            if p.returncode==0:result['removed_images'].append(d['Config']['Image'])
    health();result.update(success=True,stage='complete',identity_compose=str(compose),retired_containers=OLD,retained_volumes=len(volumes))
    (work/'receipt.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)


if __name__=='__main__':
    os.umask(0o077)
    try:deploy(json.loads(Path(sys.argv[1]).read_text()))
    except Exception as error:
        print(json.dumps({'success':False,'failure_type':type(error).__name__,'reason':str(error) if isinstance(error,RuntimeError) else 'operation_stopped'}),flush=True)
        raise SystemExit(1) from None
