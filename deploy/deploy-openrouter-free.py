"""在已授权服务器增量发布免费 Chat 隔离；网关免费门必须另行保持关闭至配置完成。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


spec = importlib.util.spec_from_file_location('profile_release', Path(__file__).with_name('deploy-profile-sync.py'))
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

SOURCE_FILES = frozenset(('utils/task.py', 'routers/tasks.py', 'utils/chat.py',
                          'routers/openai.py', 'utils/context_compaction.py'))


class FreeChatRelease(base.Release):
    """复用已有健康、目标服务 CAS 和恢复；不使用旧默认构建或 CHAT_ENV 更新。"""

    def build(self):
        """以当前镜像的唯一别名离线增量构建，从本次 RUN 获取五个真实文件摘要。"""
        s = self.s
        base.check(re.fullmatch(r'[a-f0-9]{40}', s['source_commit']), 'source_commit_required')
        context = self.source / 'openwebui-profile-overlay'
        base_tag = s['new_image'] + '-base'
        for tag in (s['new_image'], base_tag):
            base.check(not self.run(['docker', 'image', 'ls', '--quiet', tag]), 'release_tag_already_exists')
        self.run(['docker', 'image', 'tag', s['expected_image_id'], base_tag])
        result = subprocess.run(
            ['docker', 'build', '--pull=false', '--network=none', '--no-cache', '--progress=plain',
             '--label', 'org.opencontainers.image.revision=' + s['source_commit'],
             '--label', 'org.opencontainers.image.source=https://github.com/ixiaoyy/parallellines-chat',
             '--build-arg', 'CHAT_BASE_IMAGE=' + base_tag,
             '-f', str(context / 'Dockerfile.openrouter-free'), '-t', s['new_image'], str(context)],
            capture_output=True, timeout=300,
        )
        output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
        (self.run_dir / 'build.log').write_text(output, encoding='utf-8')
        base.check(result.returncode == 0, 'free_chat_image_build_failed')
        hashes = {}
        for path, value in re.findall(r'PL_FREE_TASK_SOURCE_SHA256=([^:\s]+):([a-f0-9]{64})', output):
            base.check(path in SOURCE_FILES and (path not in hashes or hashes[path] == value),
                       'built_source_receipt_invalid')
            hashes[path] = value
        base.check(set(hashes) == SOURCE_FILES, 'built_source_receipt_incomplete')
        s['desired_source'] = hashes
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag]) == s['expected_image_id'],
                   'base_image_changed')
        image_id = self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', s['new_image']])
        base.check(re.fullmatch(r'sha256:[a-f0-9]{64}', image_id), 'invalid_built_image')
        return context, image_id

    def update(self, service, context):
        """只更新本次 Chat 的镜像与构建来源，原环境、挂载、任务和其他服务原样保留。"""
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        service['image'] = self.s['new_image']
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.openrouter-free',
                            'args': {'CHAT_BASE_IMAGE': self.s['new_image'] + '-base'}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', help='已审核的非敏感服务器 scope JSON；不含管理员凭据')
    args = parser.parse_args()
    os.umask(0o077)
    release = FreeChatRelease(json.loads(Path(args.scope).read_text()), 'chat')
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'phase': 'chat', 'stage': release.stage,
                          'failure_type': type(error).__name__, 'detail': 'Free Chat release stopped; inspect private receipt'}), flush=True)
        raise SystemExit(1) from None
