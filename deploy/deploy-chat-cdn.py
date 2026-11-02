"""为现有 Chat vhost 启用公共哈希资源缓存，保留其他站点和流式/认证行为。"""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys


START = '    # BEGIN pl-chat-immutable-cache\n'
END = '    # END pl-chat-immutable-cache\n'
DYNAMIC = ('        # HTML、登录、接口和流式对话不得进入浏览器或 CDN 内容缓存。\n'
           '        proxy_hide_header Cache-Control;\n'
           '        add_header Cache-Control "no-store" always;\n')


def run(args):
    """有界执行本次部署命令；不将可能包含配置内容的 stderr 写入回执。"""
    result = subprocess.run(args, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('deployment_command_failed')
    return result.stdout.decode('utf-8')


def region(text, begin, end):
    """定位已存在且唯一的 Chat 发布标记，不推断或扩大到其他虚拟主机。"""
    if text.count(begin) != 1 or text.count(end) != 1:
        raise RuntimeError('chat_vhost_markers_changed')
    lo = text.index(begin)
    hi = text.index(end, lo) + len(end)
    return lo, hi, text[lo:hi]


def replace_region(path, begin, end, expected, replacement):
    """只写本次 Chat 区段，锁定并保留 inode；目标已被修改则停止。"""
    with path.open('r+b') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        text = stream.read().decode('utf-8')
        lo, hi, current = region(text, begin, end)
        opened, actual = os.fstat(stream.fileno()), path.stat()
        if current != expected or (opened.st_dev, opened.st_ino) != (actual.st_dev, actual.st_ino):
            raise RuntimeError('chat_vhost_changed_concurrently')
        stream.seek(0)
        stream.write((text[:lo] + replacement + text[hi:]).encode('utf-8'))
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def deploy(scope):
    """使用非敏感范围文件部署；先保存私有区段备份，失败只恢复本区段。"""
    os.umask(0o077)
    path = Path(scope['config'])
    backup = Path(scope['backup_dir'])
    if not backup.is_absolute() or not backup.is_relative_to('/data') or backup.exists():
        raise RuntimeError('new_private_backup_directory_required')
    if not re.fullmatch(r'\$[A-Za-z_][A-Za-z0-9_]*', scope['upstream_variable']):
        raise RuntimeError('invalid_upstream_variable')
    begin, end = scope['begin'], scope['end']
    _, _, original = region(path.read_text(encoding='utf-8'), begin, end)
    if START in original:
        raise RuntimeError('cache_policy_already_present_inspect_before_retry')
    template = Path(__file__).with_name('nginx-chat.conf').read_text(encoding='utf-8')
    block = template[template.index(START):template.index(END) + len(END)]
    block = block.replace('$pl_chat_upstream', scope['upstream_variable'])
    marker = '    client_max_body_size 50m;\n'
    location = '    location / {\n'
    if original.count(marker) != 1 or original.count(location) != 1:
        raise RuntimeError('expected_http_redirect_and_https_proxy_layout')
    # HTTP location 为单行重定向，不参与替换；只改 HTTPS 的多行代理 location。
    proposed = original.replace(marker, marker + block, 1)
    proxy = location + '        proxy_pass http://' + scope['upstream_variable'] + ';\n'
    if proposed.count(proxy) != 1:
        raise RuntimeError('chat_proxy_entry_changed')
    proposed = proposed.replace(proxy, location + DYNAMIC + proxy[len(location):], 1)
    container = scope['nginx_container']
    health = ['curl', '--noproxy', '*', '-fsS', '--connect-timeout', '3', '--max-time', '10',
              '--resolve', 'chat.pingxingxian.space:443:127.0.0.1', 'https://chat.pingxingxian.space/health']
    run(health)
    backup.mkdir(mode=0o700)
    (backup / 'chat-vhost.before.conf').write_text(original, encoding='utf-8')
    (backup / 'chat-vhost.after.conf').write_text(proposed, encoding='utf-8')
    result = {'success': False, 'backup_dir': str(backup), 'model_requests': 0,
              'other_vhosts_changed': False, 'application_restarted': False}
    try:
        replace_region(path, begin, end, original, proposed)
        run(['docker', 'exec', container, 'nginx', '-t'])
        run(['docker', 'exec', container, 'nginx', '-s', 'reload'])
        run(health)
        result['success'] = True
    except Exception as error:
        result['failure_type'] = type(error).__name__
        result['rollback_healthy'] = False
        try:
            replace_region(path, begin, end, proposed, original)
            run(['docker', 'exec', container, 'nginx', '-t'])
            run(['docker', 'exec', container, 'nginx', '-s', 'reload'])
            run(health)
            result['rollback_healthy'] = True
        except Exception:
            pass
    (backup / 'receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    try:
        code = deploy(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception as error:
        print(json.dumps({'success': False, 'failure_type': type(error).__name__, 'stage': 'preparation_stopped'}))
        code = 1
    raise SystemExit(code)
