// 只构建实际发布所需的前端；保留官方 Pyodide 运行时，不执行测试或 lint。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.resolve(process.argv[2] ?? '');
const output = path.resolve(process.argv[3] ?? '');
const feature = path.join(repository, 'openwebui-profile-overlay/frontend-performance');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const check = (value, reason) => { if (!value) throw new Error(reason); };
check(process.argv.length === 4, 'usage: node deploy/build-performance-frontend.mjs PREPARED_SOURCE NEW_OUTPUT_DIRECTORY');
check(!fs.existsSync(output), 'output_exists_preserve_existing_artifact');
check(sha(fs.readFileSync(path.join(source, 'package-lock.json'))) === 'a16007f1de06fe3ca6cbac2a0025055495501a26c9bf067562320dc2e5260c9d', 'lockfile_changed');
const manifest = JSON.parse(fs.readFileSync(path.join(feature, 'manifest.json'), 'utf8'));
for (const [relative, expected] of Object.entries(manifest.files)) {
  check(sha(fs.readFileSync(path.join(source, relative))) === expected.after_sha256, 'performance_source_changed:' + relative);
}
const version = JSON.parse(fs.readFileSync(path.join(source, 'package.json'), 'utf8')).version;
check(version === '0.12.5', 'upstream_version_changed');
const patchHash = sha(fs.readFileSync(path.join(feature, 'frontend-performance.patch')));
const heap = Number(process.env.FRONTEND_BUILD_HEAP_MB ?? 12288);
check(Number.isInteger(heap) && heap >= 1024 && heap <= 12288, 'invalid_build_heap_limit');
const env = { ...process.env, npm_package_version: version, APP_BUILD_HASH: patchHash, APP_BUILD_CHANNEL: 'unknown' };
for (const args of [['scripts/encryption/artifact.mjs'], [`--max-old-space-size=${heap}`, 'node_modules/vite/bin/vite.js', 'build']]) {
  const result = spawnSync(process.execPath, args, { cwd: source, env, stdio: 'inherit', windowsHide: true });
  check(result.status === 0, 'frontend_production_build_failed');
}
const built = path.join(source, 'build');
check(fs.statSync(path.join(built, 'index.html')).isFile(), 'built_index_missing');
const files = {};
function copy(relative = '') {
  for (const entry of fs.readdirSync(path.join(built, relative), { withFileTypes: true })) {
    const child = relative ? `${relative}/${entry.name}` : entry.name;
    if (child === 'pyodide') continue;
    check(!entry.isSymbolicLink(), 'build_symlink_rejected');
    if (entry.isDirectory()) copy(child);
    else if (entry.isFile()) {
      const bytes = fs.readFileSync(path.join(built, child));
      const destination = path.join(output, 'frontend-build', child);
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      fs.writeFileSync(destination, bytes, { flag: 'wx' });
      files[child] = sha(bytes);
    } else throw new Error('unexpected_build_entry');
  }
}
copy();
fs.writeFileSync(path.join(output, 'frontend-build-manifest.json'), JSON.stringify({
  upstream_commit: manifest.upstream_commit, performance_patch_sha256: patchHash,
  preserved_runtime: ['pyodide/'], files
}, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ built: true, version, patch_sha256: patchHash, file_count: Object.keys(files).length, index_sha256: files['index.html'] }));
