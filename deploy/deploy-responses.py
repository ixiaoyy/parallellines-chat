"""在当前 Chat 镜像上增量部署三个模型的原生 Responses 兼容，不执行离线验证。"""
import argparse
import copy
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


class ResponsesRelease(base.Release):
    """复用现有镜像、健康检查与 Compose 局部恢复，只额外维护一个连接配置行。"""

    def storage(self, action):
        """在目标容器中执行已提交的维护脚本，仅返回不含配置内容的回执。"""
        code = (self.source / 'deploy/configure-responses-storage.py').read_bytes()
        result = self.run(['docker', 'exec', '-i', self.s['container'], 'python', '-', action,
                           self.s['config_backup']], input=code, timeout=30)
        # 上游初始化信息可能出现在前面；最后一行是维护脚本的固定 JSON 回执。
        value = json.loads(result.splitlines()[-1])
        base.check(value.get('ok') is True, 'native_configuration_failed')
        return value

    def build(self):
        """只做部署所需的增量镜像构建；由构建输出提供运行源文件的预期摘要。"""
        s = self.s
        context = self.source / 'openwebui-profile-overlay'
        base_tag = s['new_image'] + '-base'
        base.check(not self.run(['docker', 'image', 'ls', '--quiet', s['new_image']]), 'release_tag_already_exists')
        base.check(not self.run(['docker', 'image', 'ls', '--quiet', base_tag]), 'base_tag_already_exists')
        self.run(['docker', 'image', 'tag', s['expected_image_id'], base_tag])
        # 构建回执来自这次应用补丁的输出，不能复用没有该输出的 RUN 缓存。
        result = subprocess.run(['docker', 'build', '--pull=false', '--network=none', '--no-cache', '--progress=plain',
                                 '--label', 'org.opencontainers.image.revision=' + self.s['source_commit'],
                                 '--build-arg', 'CHAT_BASE_IMAGE=' + base_tag,
                                 '-f', str(context / 'Dockerfile.responses'), '-t', s['new_image'], str(context)],
                                capture_output=True, timeout=300)
        base.check(result.returncode == 0, 'responses_image_build_failed')
        output = (result.stdout + result.stderr).decode('utf-8')
        hashes = set(re.findall(r'PL_RESPONSES_SOURCE_SHA256=([a-f0-9]{64})', output))
        base.check(len(hashes) == 1, 'built_source_receipt_missing')
        s['desired_source'] = {'routers/openai.py': hashes.pop()}
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag]) == s['expected_image_id'],
                   'base_image_changed')
        image_id = self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', s['new_image']])
        base.check(re.fullmatch(r'sha256:[a-f0-9]{64}', image_id), 'invalid_built_image')
        return context, image_id

    def update(self, service, context):
        """只切换目标服务的镜像和构建入口，保留所有现有环境与挂载。"""
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        service['image'] = self.s['new_image']
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.responses',
                            'args': {'CHAT_BASE_IMAGE': self.s['new_image'] + '-base'}}

    def deploy(self):
        """镜像健康后再启用连接协议；配置失败时按本次快照恢复配置和镜像。"""
        base.check(re.fullmatch(r'[a-f0-9]{40}', self.s['source_commit']), 'source_commit_required')
        backup = Path(self.s['config_backup'])
        base.check(backup.is_absolute() and backup.is_relative_to('/app/backend/data'), 'invalid_config_backup')
        self.storage('inspect')
        super().deploy()
        activated = None
        config_attempted = False
        try:
            # 使用内层实际 CAS 的备份重建前后状态，不能重新截取可能已被别人修改的基线。
            backups = list((self.run_dir / 'backup').glob('compose-*.yaml'))
            base.check(len(backups) == 1, 'activation_backup_missing_or_ambiguous')
            original = backups[0].read_text()
            old_service = copy.deepcopy(base.utils.load_yaml(original)['services'][self.s['service']])
            intended = base.utils.patch_service(original, self.s['service'],
                                                lambda service: self.update(service, self.source / 'openwebui-profile-overlay'))
            activated = base.utils.service_hash(intended, self.s['service'])
            base.check(base.utils.service_hash(self.compose_file.read_text(), self.s['service']) == activated,
                       'target_service_changed_after_activation')
            self.announce('native-responses-configuration')
            config_attempted = True
            configuration = self.storage('apply')
            base.check(self.storage('inspect').get('api_type') == 'responses', 'responses_configuration_readback_failed')
            self.healthy()
        except Exception as error:
            self.announce('configuration-rollback')
            config_restored, image_restored = not config_attempted, False
            if config_attempted:
                try:
                    self.storage('rollback')
                    config_restored = True
                except Exception:
                    pass
            try:
                # 配置状态不明时保留已健康的新镜像，不能再切到缺少兼容处理的旧镜像。
                base.check(config_restored, 'configuration_restore_required_before_image_rollback')
                base.check(activated is not None, 'activation_baseline_missing')
                def restore(service):
                    service.clear()
                    service.update(copy.deepcopy(old_service))
                base.utils.cas_service_update(self.compose_file, self.s['service'], activated, restore,
                                              self.run_dir / 'configuration-rollback-backup')
                self.compose('config', '--quiet')
                self.compose('up', '-d', '--no-deps', '--no-build', '--pull', 'never', self.s['service'])
                self.healthy()
                base.check(self.image_id() == self.s['expected_image_id'], 'rollback_image_mismatch')
                image_restored = True
            except Exception:
                pass
            receipt = {'success': False, 'failure_type': type(error).__name__,
                       'configuration_restored': config_restored, 'rollback_healthy': image_restored,
                       'receipt_dir': str(self.run_dir)}
            (self.run_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2))
            print(json.dumps(receipt), flush=True)
            raise RuntimeError('responses_configuration_activation_failed') from None
        result = json.loads((self.run_dir / 'receipt.json').read_text())
        result.update({'api_type': 'responses', 'configuration_changed': configuration['changed'],
                       'config_backup': str(backup), 'source_commit': self.s['source_commit'],
                       'models': ['gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol'],
                       'offline_validation_run': False, 'model_requests': 0})
        (self.run_dir / 'receipt.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', help='服务器上已审核的非敏感部署 scope JSON')
    args = parser.parse_args()
    os.umask(0o077)
    release = ResponsesRelease(json.loads(Path(args.scope).read_text()), 'chat')
    try:
        release.deploy()
    except Exception as error:
        print(json.dumps({'success': False, 'phase': 'chat', 'stage': release.stage,
                          'failure_type': type(error).__name__, 'detail': 'Responses release stopped; inspect the private receipt'}), flush=True)
        raise SystemExit(1) from None
