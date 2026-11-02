"""分阶段部署内网解析与 Chat 前端，复用现有局部 Compose 激活/恢复能力。"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request


spec = importlib.util.spec_from_file_location('profile_release', Path(__file__).with_name('deploy-profile-sync.py'))
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)
HOSTS = {'pingxingxian.space', 'api.pingxingxian.space'}


def add_internal_hosts(service, hosts):
    """只合并批准域名的 host-gateway 解析，保留已有 extra_hosts 的类型和其他条目。"""
    base.check(set(hosts) <= HOSTS and hosts, 'unapproved_routing_hosts')
    current = copy.deepcopy(service.get('extra_hosts'))
    if current is None:
        current = []
    if isinstance(current, dict):
        for host in hosts:
            base.check(current.get(host) in (None, 'host-gateway'), 'existing_host_mapping_conflict')
            current[host] = 'host-gateway'
    elif isinstance(current, list):
        for host in hosts:
            entries = [item for item in current if isinstance(item, str) and
                       (item.startswith(host + ':') or item.startswith(host + '='))]
            base.check(not entries or entries in ([host + ':host-gateway'], [host + '=host-gateway']),
                       'existing_host_mapping_conflict')
            if not entries:
                current.append(host + ':host-gateway')
    else:
        raise RuntimeError('unsupported_extra_hosts_shape')
    service['extra_hosts'] = current


class InternalRelease(base.Release):
    """仅改网络解析，使用原镜像；身份、数据挂载、环境与 TLS 名称不变。"""

    def build(self):
        """网络阶段不构建镜像；返回空上下文与已经确认的原镜像 ID。"""
        return None, self.s['expected_image_id']

    def update(self, service, context):
        """原地修改目标 service 的解析条目，不改镜像、环境或挂载。"""
        base.check(service.get('image') == self.s['expected_compose_image'], 'compose_image_changed')
        add_internal_hosts(service, self.s['routing_hosts'])

    def source_hashes(self, files, root, interpreter):
        """有源码清单时沿用父类；纯网络阶段以激活前后镜像 ID 为完整性依据。"""
        # 网络阶段没有新源码；父类已在激活前后核对原镜像 ID，不启动无关解释器。
        return super().source_hashes(files, root, interpreter) if files else {}

    def private_routes(self):
        """核对容器内实际解析及原 HTTPS 名称的免费接口，不读取令牌或请求模型。"""
        hosts = self.s['routing_hosts']
        expected = self.s['expected_private_address']
        base.check(set(hosts) <= HOSTS, 'unapproved_routing_hosts')
        container = self.s['container']
        if self.phase == 'newapi':
            text = self.run(['docker', 'exec', container, 'cat', '/etc/hosts'])
            result = []
            for host, path in hosts.items():
                addresses = [line.split()[0] for line in text.splitlines()
                             if line.strip() and not line.startswith('#') and host in line.split()[1:]]
                base.check(addresses == [expected], 'newapi_private_dns_mismatch')
                self.run(['docker', 'exec', container, 'wget', '-q', '-T', '6', '-O', '/dev/null',
                          'https://' + host + path], timeout=10)
                result.append({'host': host, 'addresses': addresses, 'https_verified': True})
            return result
        if self.phase == 'bridge':
            code = (
                "const dns=require('node:dns').promises,https=require('node:https');"
                "const routes=" + json.dumps(hosts) + ";const expected=" + json.dumps(expected) + ";"
                "(async()=>{const results=[];for(const [host,path] of Object.entries(routes)){"
                "const ips=(await dns.lookup(host,{all:true})).map(x=>x.address);"
                "if(!ips.length||ips.some(ip=>ip!==expected))throw Error('private_dns_mismatch');"
                "await new Promise((resolve,reject)=>{const req=https.get('https://'+host+path,"
                "{signal:AbortSignal.timeout(6000)},res=>{res.resume();res.on('error',reject);"
                "res.on('end',()=>res.statusCode===200?resolve():reject(Error('private_https_failed')))});req.on('error',reject)});"
                "results.push({host,addresses:ips,https_verified:true})}console.log(JSON.stringify(results))})()"
                ".catch(()=>{console.log(JSON.stringify({ok:false}));process.exitCode=1})"
            )
            return json.loads(self.run(['docker', 'exec', container, 'node', '-e', code], timeout=20))
        code = (
            'import socket,json,urllib.request\n'
            'routes=' + repr(hosts) + '\nexpected=' + repr(expected) + '\nresults=[]\n'
            'for host,path in routes.items():\n'
            ' ips=sorted(set(x[4][0] for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)))\n'
            ' assert ips and all(ip==expected for ip in ips), "private_dns_mismatch"\n'
            ' with urllib.request.urlopen("https://"+host+path,timeout=6) as response:\n'
            '  assert response.status==200, "private_https_failed"\n'
            '  response.read(32768)\n'
            ' results.append({"host":host,"addresses":ips,"https_verified":True})\n'
            'print(json.dumps(results))\n'
        )
        return json.loads(self.run(['docker', 'exec', container, 'python', '-c', code], timeout=20))

    def healthy(self):
        """返回实际就绪状态；仅激活后核对私网 HTTPS，恢复旧配置时不强求新映射。"""
        if self.phase == 'newapi':
            deadline = time.monotonic() + 120
            while True:
                try:
                    with urllib.request.urlopen(self.s['status_url'], timeout=5) as response:
                        status = json.load(response)
                    if status.get('success') and status.get('data', {}).get('version') == self.s['version']:
                        break
                except (OSError, ValueError):
                    pass
                base.check(time.monotonic() < deadline, 'newapi_readiness_failed')
                time.sleep(2)
            state = {'version': self.s['version'], 'healthy': True}
        else:
            state = super().healthy()
        if self.stage == 'health':
            self.routes = self.private_routes()
        return state

    def deploy(self):
        """激活一个独立阶段并补充私网证明；有上游连接时不打断 New API。"""
        if self.phase == 'newapi':
            # 此二进制没有 HTTP 优雅停机入口；先避开仍存在的上游 HTTPS 连接。
            text = self.run(['docker', 'exec', self.s['container'], 'sh', '-c',
                             'cat /proc/net/tcp; if [ -f /proc/net/tcp6 ]; then cat /proc/net/tcp6; fi'], timeout=10)
            active = [parts for line in text.splitlines() if len(parts := line.split()) > 3
                      and parts[0].endswith(':') and parts[3] == '01'
                      and int(parts[2].rsplit(':', 1)[1], 16) == 443]
            base.check(not active, 'outbound_https_connections_present_wait_before_restart')
        super().deploy()
        result = json.loads((self.run_dir / 'receipt.json').read_text())
        result.update({'private_routes': self.routes, 'public_urls_unchanged': True,
                       'issuer_and_callbacks_unchanged': True, 'model_requests': 0})
        (self.run_dir / 'receipt.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


class FrontendRelease(InternalRelease):
    """在当前 Chat 镜像上只覆盖正常构建的前端；后端补丁与 Pyodide 原样保留。"""

    def build(self):
        """核对完整前端清单与提交中的补丁，基于原镜像构建并返回上下文/新镜像 ID。"""
        context = base.safe_path(self.s['frontend_context'])
        manifest = json.loads((context / 'frontend-build-manifest.json').read_text())
        base.check(manifest.get('upstream_commit') == 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca',
                   'frontend_upstream_changed')
        base.check(base.digest((context / 'frontend-build-manifest.json').read_bytes()) == self.s['frontend_manifest_sha256'],
                   'frontend_manifest_changed')
        patch = self.source / 'openwebui-profile-overlay/frontend-performance/frontend-performance.patch'
        base.check(manifest.get('performance_patch_sha256') == base.digest(patch.read_bytes()), 'frontend_patch_mismatch')
        files = manifest.get('files', {})
        actual = {}
        for path in (context / 'frontend-build').rglob('*'):
            base.check(not path.is_symlink(), 'frontend_symlink_rejected')
            if path.is_file():
                actual[path.relative_to(context / 'frontend-build').as_posix()] = base.digest(path.read_bytes())
        base.check(actual == files and 'index.html' in files, 'frontend_artifact_changed')
        self.manifest_sha = base.digest((context / 'frontend-build-manifest.json').read_bytes())
        base.check(not self.run(['docker', 'image', 'ls', '--quiet', self.s['new_image']]), 'image_tag_exists')
        base_tag = self.s['new_image'] + '-base'
        base.check(not self.run(['docker', 'image', 'ls', '--quiet', base_tag]), 'base_tag_exists')
        self.run(['docker', 'image', 'tag', self.s['expected_image_id'], base_tag])
        self.run(['docker', 'build', '--pull=false', '--network=none', '--build-arg', 'CHAT_BASE_IMAGE=' + base_tag,
                  '-f', context / 'Dockerfile.performance', '-t', self.s['new_image'], context], timeout=300)
        base.check(self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag]) == self.s['expected_image_id'],
                   'base_image_changed')
        self.s['desired_source'] = {'build/index.html': files['index.html']}
        return context, self.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.s['new_image']])

    def update(self, service, context):
        """仅合并解析条目并切换 Chat 前端镜像/构建入口，其他服务字段保持不变。"""
        super().update(service, context)
        service['image'] = self.s['new_image']
        service['build'] = {'context': str(context), 'dockerfile': 'Dockerfile.performance',
                            'args': {'CHAT_BASE_IMAGE': self.s['new_image'] + '-base'}}

    def healthy(self):
        """返回父类就绪结果，并在激活后读回全部发布文件，不运行模型或模拟请求。"""
        state = super().healthy()
        if self.stage == 'health':
            # 验证完整发布清单，避免仅首页/健康通过而遗漏延迟加载的资源。
            code = (
                'import pathlib,json,hashlib\n'
                'p=pathlib.Path("/app/frontend-performance-manifest.json")\n'
                'assert hashlib.sha256(p.read_bytes()).hexdigest()==' + repr(self.manifest_sha) + '\n'
                'manifest=json.loads(p.read_text());root=pathlib.Path("/app/build")\n'
                'for name,expected in manifest["files"].items():\n'
                ' target=root/name\n'
                ' assert target.resolve().is_relative_to(root), "invalid_asset_path"\n'
                ' assert hashlib.sha256(target.read_bytes()).hexdigest()==expected, "asset_changed"\n'
                'print(json.dumps({"files_verified":len(manifest["files"])}))\n'
            )
            self.delivery = json.loads(self.run(['docker', 'exec', self.s['container'], 'python', '-c', code], timeout=90))
        return state

    def deploy(self):
        """沿用阶段恢复后记录完整前端交付结果，不额外重启应用。"""
        super().deploy()
        result = json.loads((self.run_dir / 'receipt.json').read_text())
        result.update({'frontend_delivery': self.delivery, 'frontend_manifest_sha256': self.manifest_sha,
                       'backend_and_pyodide_preserved': True})
        (self.run_dir / 'receipt.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope')
    parser.add_argument('phase', choices=['bridge', 'newapi', 'chat'])
    args = parser.parse_args()
    os.umask(0o077)
    release = (FrontendRelease if args.phase == 'chat' else InternalRelease)(
        json.loads(Path(args.scope).read_text()), args.phase)
    try:
        release.deploy()
    except Exception as error:
        reason = str(error)
        print(json.dumps({'success': False, 'phase': args.phase, 'stage': release.stage,
                          'reason': reason if re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', reason) else type(error).__name__,
                          'receipt_dir': str(release.run_dir) if release.run_dir else None}), flush=True)
        raise SystemExit(1) from None
