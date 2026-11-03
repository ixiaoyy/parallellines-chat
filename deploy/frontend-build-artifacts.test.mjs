import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { packageFrontendBuild, privateDebugDockerIgnore } from './frontend-build-artifacts.mjs';

const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pl-chat-artifacts-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const built = path.join(root, 'build');
  for (const [relative, content] of Object.entries({
    'index.html': '<html>real build fixture</html>',
    '_app/immutable/app.hash.js': 'export const unchanged = true;',
    '_app/immutable/app.hash.js.map': '{"version":3,"sources":["app.svelte"]}',
    '_app/immutable/app.hash.css': 'body{color:black}',
    '_app/immutable/app.hash.css.map': '{"version":3,"sources":["app.css"]}',
    'pyodide/pyodide-lock.json': 'official runtime is preserved, not packaged',
    'pyodide/pyodide.js.map': 'official runtime maps are also left untouched',
  })) {
    fs.mkdirSync(path.dirname(path.join(built, relative)), { recursive: true });
    fs.writeFileSync(path.join(built, relative), content);
  }
  return { built, publicDirectory: path.join(root, 'release/frontend-build'),
    debugDirectory: path.join(root, 'release/frontend-debug'),
    manifestPath: path.join(root, 'release/frontend-build-manifest.json'),
    metadata: { upstream_commit: 'fixed-upstream', performance_patch_sha256: 'fixed-patch', preserved_runtime: ['pyodide/'] } };
}

test('source maps are private, normal assets and source bytes are unchanged, Pyodide is excluded', t => {
  const options = fixture(t);
  const result = packageFrontendBuild(options);
  assert.deepEqual(Object.keys(result.manifest.files).sort(), [
    '_app/immutable/app.hash.css', '_app/immutable/app.hash.js', 'index.html',
  ]);
  assert.deepEqual(Object.keys(result.debugFiles).sort(), [
    '_app/immutable/app.hash.css.map', '_app/immutable/app.hash.js.map',
  ]);
  let publicBytes = 0;
  let debugBytes = 0;
  for (const [directory, files] of [[options.publicDirectory, result.manifest.files], [options.debugDirectory, result.debugFiles]]) {
    for (const [relative, expected] of Object.entries(files)) {
      const original = fs.readFileSync(path.join(options.built, relative));
      const copied = fs.readFileSync(path.join(directory, relative));
      assert.deepEqual(copied, original);
      assert.equal(sha(copied), expected);
      if (directory === options.publicDirectory) publicBytes += copied.length;
      else debugBytes += copied.length;
    }
  }
  assert.deepEqual(result.bytes, { public: publicBytes, debug: debugBytes });
  assert.equal(fs.existsSync(path.join(options.publicDirectory, 'pyodide')), false);
  assert.equal(fs.existsSync(path.join(options.publicDirectory, '_app/immutable/app.hash.js.map')), false);
  const debugManifest = JSON.parse(fs.readFileSync(path.join(options.debugDirectory, 'source-map-manifest.json')));
  assert.equal(debugManifest.production_manifest_sha256, sha(fs.readFileSync(options.manifestPath)));
  assert.equal(debugManifest.upstream_commit, result.manifest.upstream_commit);
  assert.deepEqual(debugManifest.files, result.debugFiles);
});

test('both output layouts refuse an existing artifact without overwriting it', t => {
  for (const name of ['publicDirectory', 'debugDirectory', 'manifestPath']) {
    const options = fixture(t);
    fs.mkdirSync(path.dirname(options[name]), { recursive: true });
    fs.writeFileSync(options[name], 'previous-release');
    assert.throws(() => packageFrontendBuild(options), /output_exists_preserve_existing_artifact/);
    assert.equal(fs.readFileSync(options[name], 'utf8'), 'previous-release');
  }
});

test('release entry points use hidden maps and Docker contexts exclude private debug files', () => {
  for (const file of ['build-performance-frontend.mjs', 'build-recovery-frontend.mjs']) {
    const source = fs.readFileSync(path.join(repository, 'deploy', file), 'utf8');
    assert.match(source, /'build', '--sourcemap', 'hidden'/);
    assert.match(source, /packageFrontendBuild\(/);
  }
  assert.match(privateDebugDockerIgnore, /^frontend-debug\/$/m);
  assert.match(fs.readFileSync(path.join(repository, 'openwebui-profile-overlay/.dockerignore'), 'utf8'), /^frontend-debug\/$/m);
  for (const file of ['Dockerfile.performance', 'Dockerfile.recovery']) {
    const source = fs.readFileSync(path.join(repository, 'openwebui-profile-overlay', file), 'utf8');
    assert.match(source, /COPY frontend-build\/ \/app\/build\//);
    assert.match(source, /find \/app\/build\/_app -type f -name '\*\.map' -delete/);
    assert.doesNotMatch(source, /COPY frontend-debug/);
  }
});

test('identity dependencies retain a separate locked-install layer with a persistent download cache', () => {
  const source = fs.readFileSync(path.join(repository, 'identity-bridge/Dockerfile'), 'utf8');
  assert.match(source, /COPY package\.json package-lock\.json \.\/\n/);
  assert.match(source, /--mount=type=cache,id=pl-chat-identity-npm-node24,target=\/root\/\.npm,sharing=locked/);
  assert.match(source, /npm ci --omit=dev --ignore-scripts --prefer-offline --no-audit --no-fund/);
  assert.doesNotMatch(source, /npm cache clean/);
  assert.ok(source.indexOf('npm ci') < source.indexOf('COPY --chown=node:node'));
  assert.match(fs.readFileSync(path.join(repository, 'deploy/deploy-openrouter-free.py'), 'utf8'), /'--no-cache'/);
});
