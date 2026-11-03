import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { packageFrontendBuild, privateDebugDockerIgnore } from './frontend-build-artifacts.mjs';
import { parseBuildArguments, assertCacheLocation, snapshotTree, buildEnvironmentIdentity,
  collectBuildIdentity, verifyPackagedBuild, publishCachedBuild, restoreCachedBuild } from './frontend-build-cache.mjs';

const sha = value => crypto.createHash('sha256').update(value).digest('hex');
function fixture(t, mode = 'bootstrap') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pl-frontend-cache-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const built = path.join(root, 'source/build');
  for (const [relative, bytes] of Object.entries({ 'index.html': '<html>fixture</html>',
    '_app/immutable/main.hash.js': 'export const fixture = true;', '_app/immutable/main.hash.js.map': '{"version":3}',
    'pyodide/preserved.js': 'not packaged' })) {
    fs.mkdirSync(path.dirname(path.join(built, relative)), { recursive: true });
    fs.writeFileSync(path.join(built, relative), bytes);
  }
  const metadata = { upstream_commit: 'fixed-upstream', performance_patch_sha256: 'fixed-performance', preserved_runtime: ['pyodide/'],
    ...(mode === 'bootstrap' ? { bootstrap_patch_sha256: 'fixed-bootstrap', bootstrap_manifest_sha256: 'fixed-manifest', feature_order: ['frontend-performance', 'frontend-bootstrap'] } : {}) };
  const output = path.join(root, 'cold');
  packageFrontendBuild({ built, publicDirectory: path.join(output, 'frontend-build'), debugDirectory: path.join(output, 'frontend-debug'),
    manifestPath: path.join(output, 'frontend-build-manifest.json'), metadata });
  fs.writeFileSync(path.join(output, '.dockerignore'), privateDebugDockerIgnore);
  const identity = { format: 1, mode, metadata, input: 'fixture-source-and-toolchain' };
  return { root, output, metadata, assessed: { identity, key: sha(JSON.stringify(identity)) }, cache: path.join(root, 'cache') };
}

test('old default and bootstrap CLI forms remain valid; cache is explicit and modes stay distinct', () => {
  assert.deepEqual(parseBuildArguments(['SOURCE', 'OUT']), { mode: 'performance', cacheDirectory: null });
  assert.deepEqual(parseBuildArguments(['SOURCE', 'OUT', 'bootstrap']), { mode: 'bootstrap', cacheDirectory: null });
  assert.equal(parseBuildArguments(['SOURCE', 'OUT', '--cache-dir', 'CACHE']).mode, 'performance');
  assert.equal(parseBuildArguments(['SOURCE', 'OUT', 'bootstrap', '--cache-dir', 'CACHE']).mode, 'bootstrap');
  for (const args of [[], ['SOURCE'], ['SOURCE', 'OUT', 'unknown'], ['SOURCE', 'OUT', '--cache-dir']]) assert.throws(() => parseBuildArguments(args), /usage:/);
});

for (const mode of ['performance', 'bootstrap']) test(mode + ' cached artifacts restore byte-identically to a fresh output', t => {
  const value = fixture(t, mode);
  assert.equal(restoreCachedBuild(value.cache, value.assessed, path.join(value.root, 'miss'), value.metadata), null);
  publishCachedBuild(value.cache, value.assessed, value.output, value.metadata);
  const restored = path.join(value.root, 'restored');
  const result = restoreCachedBuild(value.cache, value.assessed, restored, value.metadata);
  assert.equal(result.manifest.files['index.html'], sha('<html>fixture</html>'));
  assert.equal(fs.existsSync(path.join(restored, 'cache-receipt.json')), false);
  assert.equal(fs.existsSync(path.join(restored, 'frontend-build/_app/immutable/main.hash.js.map')), false);
  assert.equal(fs.readFileSync(path.join(restored, 'frontend-debug/_app/immutable/main.hash.js.map'), 'utf8'), '{"version":3}');
  assert.deepEqual(verifyPackagedBuild(restored, value.metadata).manifest, verifyPackagedBuild(value.output, value.metadata).manifest);
  assert.throws(() => restoreCachedBuild(value.cache, value.assessed, restored, value.metadata), /output_exists_preserve_existing_artifact/);
});

test('source, static, installed tool files and environment changes produce different identities', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pl-cache-inputs-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, 'source.ts'), 'export const value = 1;');
  const before = snapshotTree(root);
  fs.writeFileSync(path.join(root, 'source.ts'), 'export const value = 2;');
  assert.notEqual(snapshotTree(root).sha256, before.sha256);
  fs.writeFileSync(path.join(root, 'static.png'), 'new static input');
  const afterStatic = snapshotTree(root);
  assert.notEqual(afterStatic.sha256, before.sha256);
  fs.writeFileSync(path.join(root, 'tool.js'), 'new installed tool input');
  assert.notEqual(snapshotTree(root, { dependencies: true }).sha256, afterStatic.sha256);
  fs.mkdirSync(path.join(root, 'static/assets/target'), { recursive: true });
  fs.writeFileSync(path.join(root, 'static/assets/target/visible-static.png'), 'not a Rust output');
  const targetBefore = snapshotTree(path.join(root, 'static')).sha256;
  fs.writeFileSync(path.join(root, 'static/assets/target/visible-static.png'), 'changed visible static');
  assert.notEqual(snapshotTree(path.join(root, 'static')).sha256, targetBefore);
  fs.mkdirSync(path.join(root, 'crypto/target'), { recursive: true });
  fs.writeFileSync(path.join(root, 'crypto/target/generated.wasm'), 'derived');
  assert.equal(snapshotTree(path.join(root, 'crypto'), { excludedDirectories: ['target'] }).files, 0);
  assert.notDeepEqual(buildEnvironmentIdentity({}, 6144, 'source-hash', '0.12.5'), buildEnvironmentIdentity({ ENV: 'dev' }, 6144, 'source-hash', '0.12.5'));
  assert.notDeepEqual(buildEnvironmentIdentity({}, 6144, 'source-hash', '0.12.5'), buildEnvironmentIdentity({ PUBLIC_CUSTOM: 'value' }, 6144, 'source-hash', '0.12.5'));
  assert.throws(() => buildEnvironmentIdentity({ NODE_OPTIONS: '--require unverified-module' }, 6144, 'hash', '0.12.5'), /unverified_environment:NODE_OPTIONS/);
  fs.writeFileSync(path.join(root, '.env'), 'MUST_NOT_BE_READ=fixture');
  assert.throws(() => snapshotTree(root), /private_file_not_read/);
});

test('bootstrap and default identities cannot consume each other even if entry names are copied', t => {
  const value = fixture(t);
  publishCachedBuild(value.cache, value.assessed, value.output, value.metadata);
  const different = { identity: { ...value.assessed.identity, mode: 'performance' } };
  different.key = sha(JSON.stringify(different.identity));
  assert.equal(restoreCachedBuild(value.cache, different, path.join(value.root, 'different-mode'), value.metadata), null);
  fs.cpSync(path.join(value.cache, value.assessed.key), path.join(value.cache, different.key), { recursive: true });
  assert.throws(() => restoreCachedBuild(value.cache, different, path.join(value.root, 'wrong-mode'), value.metadata), /frontend_cache_corrupt:.*cache_identity_mismatch/);
});

for (const damage of ['public-bytes', 'private-bytes', 'missing-file', 'extra-file', 'receipt', 'private-binding', 'self-consistent-artifact-swap']) {
  test('corrupt cache fails without a normal-build fallback or output: ' + damage, t => {
    const value = fixture(t);
    publishCachedBuild(value.cache, value.assessed, value.output, value.metadata);
    const entry = path.join(value.cache, value.assessed.key), artifacts = path.join(entry, 'artifacts');
    if (damage === 'public-bytes') fs.writeFileSync(path.join(artifacts, 'frontend-build/index.html'), 'corrupted');
    if (damage === 'private-bytes') fs.writeFileSync(path.join(artifacts, 'frontend-debug/_app/immutable/main.hash.js.map'), 'corrupted');
    if (damage === 'missing-file') fs.unlinkSync(path.join(artifacts, 'frontend-build/index.html'));
    if (damage === 'extra-file') fs.writeFileSync(path.join(artifacts, 'frontend-build/unlisted.txt'), 'not in manifest');
    if (damage === 'receipt') fs.writeFileSync(path.join(entry, 'cache-receipt.json'), '{}');
    if (damage === 'private-binding') {
      const file = path.join(artifacts, 'frontend-debug/source-map-manifest.json');
      const json = JSON.parse(fs.readFileSync(file)); json.production_manifest_sha256 = 'wrong'; fs.writeFileSync(file, JSON.stringify(json));
    }
    if (damage === 'self-consistent-artifact-swap') {
      fs.writeFileSync(path.join(artifacts, 'frontend-build/index.html'), 'another same-mode build');
      const publicFile = path.join(artifacts, 'frontend-build-manifest.json');
      const publicManifest = JSON.parse(fs.readFileSync(publicFile));
      publicManifest.files['index.html'] = sha('another same-mode build');
      fs.writeFileSync(publicFile, JSON.stringify(publicManifest));
      const privateFile = path.join(artifacts, 'frontend-debug/source-map-manifest.json');
      const privateManifest = JSON.parse(fs.readFileSync(privateFile));
      privateManifest.production_manifest_sha256 = sha(fs.readFileSync(publicFile));
      fs.writeFileSync(privateFile, JSON.stringify(privateManifest));
    }
    const output = path.join(value.root, 'not-created');
    assert.throws(() => restoreCachedBuild(value.cache, value.assessed, output, value.metadata), /frontend_cache_corrupt:.*preserve this entry.*omit --cache-dir/);
    assert.equal(fs.existsSync(output), false);
    assert.equal(fs.existsSync(entry), true);
  });
}

test('cache paths cannot overlap source/output and input symlinks cannot escape the install', t => {
  const value = fixture(t);
  assert.throws(() => assertCacheLocation(path.join(value.output, 'cache'), path.join(value.root, 'source'), value.output), /overlaps/);
  assert.throws(() => assertCacheLocation(value.root, path.join(value.root, 'source'), value.output), /overlaps/);
  const outside = path.join(value.root, 'outside'); fs.mkdirSync(outside);
  const node = path.join(value.root, 'node_modules'); fs.mkdirSync(node);
  fs.symlinkSync(outside, path.join(node, 'linked-package'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => snapshotTree(node, { dependencies: true }), /linked_dependency_outside_install/);
});

test('dotenv presence disables cache without opening private configuration or requiring a prepared install', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pl-cache-private-input-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, '.env'), 'NOT_A_BUILD_CACHE_INPUT=fixture');
  const result = collectBuildIdentity({ source: root });
  assert.equal(result.identity, null);
  assert.equal(result.reason, 'dotenv_present_not_read:.env');
});

test('cache cannot be placed in an overlay Docker context or the real junction-backed installation', t => {
  const value = fixture(t);
  for (const context of ['openwebui-profile-overlay', 'identity-bridge', 'main-profile-overlay']) {
    assert.throws(() => assertCacheLocation(path.resolve(import.meta.dirname, '..', context, 'unit-cache'),
      path.join(value.root, 'source'), value.output), /overlaps_protected_path/);
  }
  const installed = path.join(value.root, 'actual-install'); fs.mkdirSync(installed);
  fs.symlinkSync(installed, path.join(value.root, 'source/node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => assertCacheLocation(path.join(installed, 'cache'), path.join(value.root, 'source'), value.output), /overlaps_protected_path/);
  const policy = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, 'frontend-cache-input-policy.json')));
  assert.equal(policy.root_files.includes('.npmrc'), false);
});

for (const name of ['artifacts', 'artifacts/frontend-build', 'artifacts/frontend-debug', 'artifacts/frontend-build-manifest.json',
  'artifacts/frontend-debug/source-map-manifest.json', 'artifacts/.dockerignore', 'cache-receipt.json']) {
  test('required cache roots reject symlinks before following them: ' + name, t => {
    const value = fixture(t);
    publishCachedBuild(value.cache, value.assessed, value.output, value.metadata);
    const entry = path.join(value.cache, value.assessed.key);
    const target = path.join(entry, name), saved = path.join(value.root, 'saved-root');
    fs.renameSync(target, saved);
    const outside = path.join(value.root, 'outside-directory'); fs.mkdirSync(outside);
    fs.symlinkSync(outside, target, process.platform === 'win32' ? 'junction' : 'dir');
    const output = path.join(value.root, 'not-created');
    assert.throws(() => restoreCachedBuild(value.cache, value.assessed, output, value.metadata), /frontend_cache_corrupt:.*symlink/);
    assert.equal(fs.existsSync(output), false);
  });
}
