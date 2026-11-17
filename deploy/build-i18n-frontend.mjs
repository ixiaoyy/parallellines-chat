// 在独立个人连接源码副本上准备并构建中英界面；旧构建入口与既有产物保持不变。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { packageFrontendBuild, privateDebugDockerIgnore } from './frontend-build-artifacts.mjs';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const fail = reason => { throw new Error(reason); };
const source = path.resolve(process.argv[2] || '');
const output = path.resolve(process.argv[3] || '');
const prepared = process.argv[4] === '--prepared';
if (![4, 5].includes(process.argv.length) || (process.argv.length === 5 && !prepared) || source === output || fs.existsSync(output)) fail('usage_or_output_exists_preserve_artifact');
const names = ['frontend-performance', 'frontend-bootstrap', 'frontend-auth-recovery', 'frontend-silent-recovery', 'unified-login', 'personal-connections', 'frontend-i18n'];
const expected = {};
const baseline = {};
const hashes = {};
for (const name of names) {
  const bytes = fs.readFileSync(path.join(repository, 'openwebui-profile-overlay', name, 'manifest.json'));
  const manifest = JSON.parse(bytes);
  if (manifest.upstream_commit !== 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca') fail('upstream_changed');
  hashes[name] = sha(bytes);
  for (const [relative, value] of Object.entries(manifest.files)) {
    expected[relative] = value.after_sha256;
    if (name !== 'frontend-i18n') baseline[relative] = value.after_sha256;
  }
  for (const [relative, value] of Object.entries(manifest.backend || {})) {
    expected['backend/open_webui/' + relative] = value.after_sha256;
    baseline['backend/open_webui/' + relative] = value.after_sha256;
  }
  if (name === 'frontend-i18n' && JSON.stringify(manifest.feature_order) !== JSON.stringify(names)) fail('feature_order_changed');
}
if (!prepared) {
  for (const [relative, value] of Object.entries(baseline)) {
    if (sha(fs.readFileSync(path.join(source, relative))) !== value) fail('personal_source_changed:' + relative);
  }
  // SOURCE 必须是可修改的隔离副本；先核对整套补丁，未知或半应用输入不写入。
  const overlay = path.join(repository, 'openwebui-profile-overlay/frontend-i18n/apply.py');
  for (const args of [[overlay, source, '--check'], [overlay, source]]) {
    const result = spawnSync(process.env.PYTHON || 'python', args, { stdio: 'inherit', windowsHide: true, timeout: 30000 });
    if (result.status !== 0) fail('i18n_preparation_failed');
  }
}
for (const [relative, value] of Object.entries(expected)) {
  if (sha(fs.readFileSync(path.join(source, relative))) !== value) fail('prepared_source_changed:' + relative);
}
if (sha(fs.readFileSync(path.join(source, 'package-lock.json'))) !== 'a16007f1de06fe3ca6cbac2a0025055495501a26c9bf067562320dc2e5260c9d') fail('lockfile_changed');
const version = JSON.parse(fs.readFileSync(path.join(source, 'package.json'))).version;
if (version !== '0.12.5') fail('version_changed');
const heap = Number(process.env.FRONTEND_BUILD_HEAP_MB || 8192);
if (!Number.isInteger(heap) || heap < 1024 || heap > 12288) fail('invalid_build_heap_limit');
const env = { ...process.env, npm_package_version: version, APP_BUILD_HASH: sha(JSON.stringify(hashes)), APP_BUILD_CHANNEL: 'unknown' };
// 复用锁定依赖和本地 Pyodide，不安装依赖、不下载运行时；每个构建步骤最长二十分钟。
for (const args of [['scripts/encryption/artifact.mjs'], [`--max-old-space-size=${heap}`, 'node_modules/vite/bin/vite.js', 'build', '--sourcemap', 'hidden']]) {
  const result = spawnSync(process.execPath, args, { cwd: source, env, stdio: 'inherit', windowsHide: true, timeout: 1_200_000 });
  if (result.status !== 0) fail('i18n_frontend_build_failed');
}
const metadata = { upstream_commit: 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca', feature_order: names, feature_manifests: hashes, preserved_runtime: ['pyodide/'] };
const result = packageFrontendBuild({ built: path.join(source, 'build'), publicDirectory: path.join(output, 'frontend-build'),
  debugDirectory: path.join(output, 'frontend-debug'), manifestPath: path.join(output, 'frontend-build-manifest.json'), metadata });
fs.writeFileSync(path.join(output, '.dockerignore'), privateDebugDockerIgnore, { flag: 'wx' });
console.log(JSON.stringify({ version, feature_order: names, file_count: Object.keys(result.manifest.files).length, public_bytes: result.bytes.public }));
