// Compile the source patch normally; never rewrite generated bundle files.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { packageFrontendBuild } from './frontend-build-artifacts.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.resolve(process.argv[2] ?? '');
const overlay = path.join(root, 'openwebui-profile-overlay');
const output = path.join(overlay, 'frontend-build');
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
const check = (value, reason) => { if (!value) throw new Error(reason); };
check(process.argv.length === 3, 'usage: node deploy/build-recovery-frontend.mjs EXACT_PREPARED_SOURCE');
const hashFile = relative => sha(fs.readFileSync(path.join(source, relative)));
check(hashFile('package-lock.json') === 'a16007f1de06fe3ca6cbac2a0025055495501a26c9bf067562320dc2e5260c9d', 'lockfile_changed');
check(hashFile('src/lib/apis/index.ts') === '2b95dfed036e202ea53c68b6937da0ea3edf91de5e61bd4708705a9d63ef8a1e', 'apply_the_pinned_frontend_patch_first');
check(hashFile('src/lib/apis/main-oauth-auth-recovery.mjs') === sha(fs.readFileSync(path.join(overlay, 'frontend-auth-recovery/main-oauth-auth-recovery.mjs'))), 'recovery_module_changed');
check(!fs.existsSync(output), 'output_exists_inspect_it_before_another_build');
check(!fs.existsSync(path.join(overlay, 'frontend-debug')) && !fs.existsSync(path.join(overlay, 'frontend-build-manifest.json')),
  'output_exists_inspect_it_before_another_build');
const patchHash = sha(fs.readFileSync(path.join(overlay, 'frontend-auth-recovery/frontend-auth-recovery.patch')));
const version = JSON.parse(fs.readFileSync(path.join(source, 'package.json'))).version;
check(version === '0.12.5', 'upstream_version_changed');
const env = { ...process.env, npm_package_version: version, APP_BUILD_HASH: patchHash, APP_BUILD_CHANNEL: 'unknown' };
// 映射保留为私有调试产物，公开 JS 不引用不可公开下载的映射文件。
for (const args of [['scripts/encryption/artifact.mjs'], ['--max-old-space-size=12288', 'node_modules/vite/bin/vite.js', 'build', '--sourcemap', 'hidden']]) {
  const result = spawnSync(process.execPath, args, { cwd: source, env, stdio: 'inherit', windowsHide: true });
  check(result.status === 0, 'frontend_build_failed');
}
const { manifest, debugFiles, bytes } = packageFrontendBuild({
  built: path.join(source, 'build'), publicDirectory: output,
  debugDirectory: path.join(overlay, 'frontend-debug'), manifestPath: path.join(overlay, 'frontend-build-manifest.json'),
  metadata: { upstream_commit: 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca', frontend_patch_sha256: patchHash, excluded_unchanged_runtime: ['pyodide/'] },
});
console.log(JSON.stringify({ built: true, version, patch_sha256: patchHash,
  file_count: Object.keys(manifest.files).length, debug_file_count: Object.keys(debugFiles).length,
  public_bytes: bytes.public, private_debug_bytes: bytes.debug, index_sha256: manifest.files['index.html'] }));
