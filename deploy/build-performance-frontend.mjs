// 只构建实际发布所需的前端；保留官方 Pyodide 运行时，不执行测试或 lint。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { packageFrontendBuild, privateDebugDockerIgnore } from './frontend-build-artifacts.mjs';
import { parseBuildArguments, assertCacheLocation, collectBuildIdentity, restoreCachedBuild, publishCachedBuild } from './frontend-build-cache.mjs';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.resolve(process.argv[2] ?? '');
const output = path.resolve(process.argv[3] ?? '');
const feature = path.join(repository, 'openwebui-profile-overlay/frontend-performance');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const check = (value, reason) => { if (!value) throw new Error(reason); };
const options = parseBuildArguments(process.argv.slice(2));
check(!fs.existsSync(output), 'output_exists_preserve_existing_artifact');
check(sha(fs.readFileSync(path.join(source, 'package-lock.json'))) === 'a16007f1de06fe3ca6cbac2a0025055495501a26c9bf067562320dc2e5260c9d', 'lockfile_changed');
const manifest = JSON.parse(fs.readFileSync(path.join(feature, 'manifest.json'), 'utf8'));
const patchHash = sha(fs.readFileSync(path.join(feature, 'frontend-performance.patch')));
// 第二批必须明确选择并核对顺序；旧入口继续只接受第一批源码，不能混用旧前端冒充新结果。
const bootstrapFeature = path.join(repository, 'openwebui-profile-overlay/frontend-bootstrap');
const bootstrapManifest = options.mode === 'bootstrap'
  ? JSON.parse(fs.readFileSync(path.join(bootstrapFeature, 'manifest.json'), 'utf8')) : null;
if (bootstrapManifest) {
  check(bootstrapManifest.requires_performance_patch_sha256 === patchHash, 'bootstrap_performance_prerequisite_changed');
  check(bootstrapManifest.upstream_commit === manifest.upstream_commit, 'bootstrap_upstream_changed');
}
for (const [relative, expected] of Object.entries(manifest.files)) {
  const outputHash = bootstrapManifest?.files[relative]?.after_sha256 ?? expected.after_sha256;
  check(sha(fs.readFileSync(path.join(source, relative))) === outputHash, 'performance_source_changed:' + relative);
}
if (bootstrapManifest) {
  for (const [relative, expected] of Object.entries(bootstrapManifest.files)) {
    check(sha(fs.readFileSync(path.join(source, relative))) === expected.after_sha256, 'bootstrap_source_changed:' + relative);
  }
}
const version = JSON.parse(fs.readFileSync(path.join(source, 'package.json'), 'utf8')).version;
check(version === '0.12.5', 'upstream_version_changed');
const bootstrapHash = bootstrapManifest ? sha(fs.readFileSync(path.join(bootstrapFeature, 'frontend-bootstrap.patch'))) : null;
const bootstrapManifestHash = bootstrapManifest ? sha(fs.readFileSync(path.join(bootstrapFeature, 'manifest.json'))) : null;
const buildHash = bootstrapManifest ? sha(JSON.stringify([patchHash, bootstrapHash, bootstrapManifestHash])) : patchHash;
const heap = Number(process.env.FRONTEND_BUILD_HEAP_MB ?? 12288);
check(Number.isInteger(heap) && heap >= 1024 && heap <= 12288, 'invalid_build_heap_limit');
const env = { ...process.env, npm_package_version: version, APP_BUILD_HASH: buildHash, APP_BUILD_CHANNEL: 'unknown' };
const metadata = { upstream_commit: manifest.upstream_commit, performance_patch_sha256: patchHash, preserved_runtime: ['pyodide/'],
  ...(bootstrapManifest ? { bootstrap_patch_sha256: bootstrapHash, bootstrap_manifest_sha256: bootstrapManifestHash,
    feature_order: ['frontend-performance', 'frontend-bootstrap'] } : {}) };
const timing = {};
let assessed = null;
let packaged = null;
let cacheStatus = options.cacheDirectory ? 'disabled' : null;
if (options.cacheDirectory) {
  assertCacheLocation(options.cacheDirectory, source, output);
  const started = performance.now();
  assessed = collectBuildIdentity({ source, mode: options.mode, metadata, env, heap, buildHash, version });
  timing.input_identity_ms = Math.round(performance.now() - started);
  if (assessed.identity) {
    const restoreStarted = performance.now();
    packaged = restoreCachedBuild(options.cacheDirectory, assessed, output, metadata);
    timing.cache_verify_restore_ms = Math.round(performance.now() - restoreStarted);
    cacheStatus = packaged ? 'hit' : 'miss';
  } else {
    console.error('frontend_cache_disabled:' + assessed.reason + '; normal build only; private configuration was not read');
  }
}
if (!packaged) {
  // 保留私有调试映射但不在生成的公开 JS 中声明 sourceMappingURL，避免浏览器追取私有文件。
  for (const args of [['scripts/encryption/artifact.mjs'], [`--max-old-space-size=${heap}`, 'node_modules/vite/bin/vite.js', 'build', '--sourcemap', 'hidden']]) {
    const started = performance.now();
    const result = spawnSync(process.execPath, args, { cwd: source, env, stdio: 'inherit', windowsHide: true });
    check(result.status === 0, 'frontend_production_build_failed');
    timing[args[0] === 'scripts/encryption/artifact.mjs' ? 'crypto_verification_ms' : 'vite_build_ms'] = Math.round(performance.now() - started);
  }
  packaged = packageFrontendBuild({
    built: path.join(source, 'build'), publicDirectory: path.join(output, 'frontend-build'),
    debugDirectory: path.join(output, 'frontend-debug'), manifestPath: path.join(output, 'frontend-build-manifest.json'),
    metadata,
  });
  // 构建上下文也排除私有映射，不能只依赖 Dockerfile 没有 COPY 调试目录。
  fs.writeFileSync(path.join(output, '.dockerignore'), privateDebugDockerIgnore, { flag: 'wx' });
  if (assessed?.identity) {
    // 构建过程中输入若改变，保留本次输出但拒绝认作有效缓存，防止混合版本得到同一个缓存身份。
    const started = performance.now();
    const after = collectBuildIdentity({ source, mode: options.mode, metadata, env, heap, buildHash, version });
    check(after.key === assessed.key, 'frontend_cache_inputs_changed_during_build; preserve output and rebuild into a new directory');
    publishCachedBuild(options.cacheDirectory, assessed, output, metadata);
    timing.cache_publish_and_recheck_ms = Math.round(performance.now() - started);
  }
}
const { manifest: production, debugFiles, bytes } = packaged;
console.log(JSON.stringify({ built: cacheStatus !== 'hit', version, patch_sha256: patchHash,
  ...(options.cacheDirectory ? { cache: cacheStatus, cache_key: assessed?.key ?? null, cache_disabled_reason: assessed?.reason ?? null, timing } : {}),
  file_count: Object.keys(production.files).length, debug_file_count: Object.keys(debugFiles).length,
  public_bytes: bytes.public, private_debug_bytes: bytes.debug, index_sha256: production.files['index.html'] }));
