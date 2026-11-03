import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { privateDebugDockerIgnore } from './frontend-build-artifacts.mjs';

const directory = path.dirname(fileURLToPath(import.meta.url));
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const check = (value, reason) => { if (!value) throw new Error(reason); };
const inside = (parent, child) => {
  const normalize = value => process.platform === 'win32' ? value.toLowerCase() : value;
  return normalize(child) === normalize(parent) || normalize(child).startsWith(normalize(parent) + path.sep);
};
const sorted = object => Object.fromEntries(Object.entries(object).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0));
const privateName = name => /^\.env(?:\.|$)/i.test(name) || /\.(?:pem|key|p12|pfx|db|sqlite\d*|log)$/i.test(name);
const sourceDirectories = ['src', 'static', 'scripts', 'crypto/encryption'];
const rootInputs = ['package.json', 'package-lock.json', 'vite.config.ts', 'svelte.config.js', 'postcss.config.js', 'tailwind.config.js', 'tsconfig.json'];
class Uncacheable extends Error {}

/** 旧 SOURCE OUT [bootstrap] 原样保留；只有显式 --cache-dir 才启用新的输入和缓存约束。 */
export function parseBuildArguments(args) {
  const rest = args.slice(2);
  const mode = rest[0] === 'bootstrap' ? (rest.shift(), 'bootstrap') : 'performance';
  const valid = args.length >= 2 && (rest.length === 0 ||
    (rest.length === 2 && rest[0] === '--cache-dir' && rest[1].length > 0));
  check(valid, 'usage: node deploy/build-performance-frontend.mjs PREPARED_SOURCE NEW_OUTPUT_DIRECTORY [bootstrap] [--cache-dir DIRECTORY]');
  return { mode, cacheDirectory: rest.length ? path.resolve(rest[1]) : null };
}

/** 缓存必须在源码、发布输出之外，防止私有缓存或回执成为静态文件、Docker 上下文的一部分。 */
export function assertCacheLocation(cache, source, output) {
  const resolved = value => {
    let existing = value;
    while (!fs.existsSync(existing)) existing = path.dirname(existing);
    return path.resolve(fs.realpathSync(existing), path.relative(existing, value));
  };
  const realCache = resolved(cache);
  if (fs.existsSync(cache)) check(fs.statSync(cache).isDirectory(), 'frontend_cache_directory_not_directory');
  check(!inside(resolved(source), resolved(output)), 'frontend_cache_output_must_be_outside_prepared_source');
  const protectedRoots = [source, output, ...['openwebui-profile-overlay', 'identity-bridge', 'main-profile-overlay']
    .map(name => path.resolve(directory, '..', name))];
  const installed = path.join(source, 'node_modules');
  if (fs.existsSync(installed)) protectedRoots.push(fs.realpathSync(installed));
  for (const target of protectedRoots.map(resolved)) {
    check(!inside(target, realCache) && !inside(realCache, target), 'frontend_cache_directory_overlaps_protected_path');
  }
}

/** 读取明确允许的源码/依赖目录，按真实文件字节、路径及执行位生成摘要；不读取环境或密钥文件。 */
export function snapshotTree(root, { dependencies = false, excludedDirectories = [], onFile = () => {} } = {}) {
  if (!dependencies && fs.lstatSync(root).isSymbolicLink()) throw new Uncacheable('source_symlink:' + root);
  const realRoot = fs.realpathSync(root);
  const digest = crypto.createHash('sha256');
  const visited = new Set();
  let files = 0, bytes = 0;
  function visit(current, relative) {
    if (privateName(path.basename(current))) throw new Uncacheable('private_file_not_read:' + relative);
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) {
      if (!dependencies) throw new Uncacheable('source_symlink:' + relative);
      const resolved = fs.realpathSync(current);
      if (!inside(realRoot, resolved)) throw new Uncacheable('linked_dependency_outside_install:' + relative);
      digest.update(`link\0${relative}\0${path.relative(realRoot, resolved)}\n`);
      visit(resolved, relative);
      return;
    }
    if (stat.isDirectory()) {
      if (dependencies && ['.cache', '.vite', '.vite-temp'].includes(relative)) return;
      if (excludedDirectories.includes(relative)) return;
      const real = fs.realpathSync(current);
      if (visited.has(real)) return;
      visited.add(real);
      for (const entry of fs.readdirSync(current).sort()) visit(path.join(current, entry), relative ? `${relative}/${entry}` : entry);
      return;
    }
    if (!stat.isFile()) throw new Uncacheable('unsupported_input:' + relative);
    if (dependencies && path.basename(current) === '.npmrc') {
      throw new Uncacheable('private_file_not_read:' + relative);
    }
    const content = fs.readFileSync(current);
    digest.update(`${relative}\0${stat.mode & 0o111}\0${sha(content)}\n`);
    files++; bytes += content.length;
    onFile(relative, content);
  }
  visit(realRoot, '');
  return { sha256: digest.digest('hex'), files, bytes };
}

/** 只保存已审构建环境字段的摘要；注入式 Node/编译器配置或 dotenv 存在时明确关闭复用。 */
export function buildEnvironmentIdentity(env, heap, buildHash, version) {
  const injection = Object.keys(env).find(name => env[name] &&
    /^(?:NODE_OPTIONS$|NODE_PATH$|NODE_V8_COVERAGE$|ESBUILD_BINARY_PATH$|BROWSERSLIST_CONFIG$|SASS_PATH$|SASS_BINARY_|TS_NODE_|JITI_|SWC_|SVELTE_|VITE_NODE_)/i.test(name));
  if (injection) throw new Uncacheable('unverified_environment:' + injection);
  const names = new Set(['NODE_ENV', 'ENV', 'WEBUI_BACKEND_URL', 'TZ', 'LANG', 'LC_ALL', 'LC_CTYPE',
    'SOURCE_DATE_EPOCH', 'BROWSERSLIST', 'BROWSERSLIST_ENV', 'TAILWIND_MODE', 'CI', 'TEMP', 'TMP']);
  for (const name of Object.keys(env)) if (/^(?:VITE_|PUBLIC_)/.test(name)) names.add(name);
  const effective = { ...env, NODE_ENV: env.NODE_ENV || 'production', npm_package_version: version,
    APP_BUILD_HASH: buildHash, APP_BUILD_CHANNEL: 'unknown', FRONTEND_BUILD_HEAP_MB: String(heap) };
  for (const name of ['npm_package_version', 'APP_BUILD_HASH', 'APP_BUILD_CHANNEL', 'FRONTEND_BUILD_HEAP_MB']) names.add(name);
  return sorted(Object.fromEntries([...names].map(name => [name, name in effective ? sha(effective[name]) : null])));
}

/**
 * 为固定配方收集完整输入身份；未能证明的配置返回 disabled，不读取私密文件，也不影响无缓存构建。
 * identity 包含源码、实际安装依赖、Node 二进制/版本、命令、构建环境摘要和补丁模式，不接纳旧产物补认种子。
 */
export function collectBuildIdentity({ source, mode, metadata, env, heap, buildHash, version }) {
  try {
    for (const name of ['.env', '.env.local', '.env.production', '.env.production.local']) {
      if (fs.existsSync(path.join(source, name))) throw new Uncacheable('dotenv_present_not_read:' + name);
    }
    if (process.execArgv.length) throw new Uncacheable('unverified_node_arguments');
    const policyBytes = fs.readFileSync(path.join(directory, 'frontend-cache-input-policy.json'));
    const policy = JSON.parse(policyBytes);
    if (policy.format !== 1 || policy.upstream_commit !== metadata.upstream_commit) throw new Uncacheable('unreviewed_input_policy');
    if (JSON.stringify(policy.source_directories) !== JSON.stringify(sourceDirectories) ||
      JSON.stringify(policy.root_files) !== JSON.stringify(rootInputs) ||
      Object.keys(policy.audited_configs).some(relative => !rootInputs.includes(relative) &&
        !['scripts/build-info.js', 'scripts/encryption/artifact.mjs', 'src/routes/+layout.js'].includes(relative))) {
      throw new Uncacheable('input_policy_outside_approved_paths');
    }
    const readSourceInput = relative => {
      const target = path.join(source, relative);
      const expected = path.resolve(fs.realpathSync(source), relative);
      const actual = fs.realpathSync(target);
      if (fs.lstatSync(target).isSymbolicLink() || !inside(expected, actual) || !inside(actual, expected)) {
        throw new Uncacheable('source_symlink:' + relative);
      }
      return fs.readFileSync(target);
    };
    for (const [relative, expected] of Object.entries(policy.audited_configs)) {
      if (sha(readSourceInput(relative)) !== expected) throw new Uncacheable('unreviewed_build_config:' + relative);
    }
    const allowedConfigs = new Set(['vite.config.ts', 'svelte.config.js', 'postcss.config.js', 'tailwind.config.js', 'tsconfig.json']);
    for (const name of fs.readdirSync(source)) {
      if ((/^(?:vite|svelte|postcss|tailwind)\.config\./.test(name) || /^\.(?:postcssrc|babelrc|browserslistrc)/.test(name) ||
        ['babel.config.js', 'browserslist', 'jsconfig.json'].includes(name)) && !allowedConfigs.has(name)) {
        throw new Uncacheable('unreviewed_build_config:' + name);
      }
    }
    const environment = buildEnvironmentIdentity(env, heap, buildHash, version);
    const require = createRequire(path.join(source, 'package.json'));
    const dependenciesRoot = fs.realpathSync(path.join(source, 'node_modules'));
    for (const resolver of [require, createRequire(path.join(dependenciesRoot, 'vite/package.json'))]) {
      for (const candidate of resolver.resolve.paths('frontend-cache-input-probe') ?? []) {
        if (fs.existsSync(candidate) && !inside(dependenciesRoot, fs.realpathSync(candidate))) {
          throw new Uncacheable('dependency_search_root_outside_install');
        }
      }
    }
    const ts = require('typescript');
    const knownInput = candidate => !inside(path.join(source, 'crypto/encryption/target'), candidate) &&
      (policy.source_directories.some(relative => inside(path.join(source, relative), candidate)) ||
        policy.root_files.some(relative => path.join(source, relative) === candidate) || inside(path.join(source, 'node_modules'), candidate));
    const checkImport = (relative, specifier) => {
      if (/^(?:node:)?(?:fs|child_process|vm|os)(?:\/|$)/.test(specifier)) throw new Uncacheable('source_external_io:src/' + relative);
      if (!specifier.startsWith('.') && !specifier.startsWith('/')) return;
      const candidate = specifier.startsWith('/') ? path.resolve(source, '.' + specifier.split('?')[0]) :
        path.resolve(source, 'src', path.dirname(relative), specifier.split('?')[0]);
      if (!knownInput(candidate)) throw new Uncacheable('source_import_outside_inputs:src/' + relative);
    };
    const checkSourceEnvironment = (relative, content) => {
      if (/\.(?:css|scss|sass)$/.test(relative)) {
        for (const match of content.toString('utf8').matchAll(/@(?:import|use|forward)\s+(?:url\()?['"]([^'"]+)['"]/g)) checkImport(relative, match[1]);
      }
      if (/\/\+(?:page|layout|server)(?:\.server)?\.[cm]?[jt]s$/.test(relative) && relative !== 'routes/+layout.js') {
        throw new Uncacheable('unreviewed_route_build_options:src/' + relative);
      }
      if (!/\.(?:[cm]?js|ts|svelte)$/.test(relative) || relative.startsWith('lib/encryption/vendor/')) return;
      const scanner = ts.createScanner(ts.ScriptTarget.Latest, true, ts.LanguageVariant.Standard, content.toString('utf8'));
      const tokens = [];
      for (let kind = scanner.scan(); kind !== ts.SyntaxKind.EndOfFileToken; kind = scanner.scan()) {
        tokens.push({ kind, text: scanner.getTokenText(), value: scanner.getTokenValue() });
      }
      for (let i = 0; i < tokens.length; i++) {
        const text = tokens.slice(i, i + 5).map(token => token.text).join('');
        if (/^process(?:\?\.|\.|\[)["']?env/.test(text) || text.startsWith('import.meta.env') ||
          (tokens[i].kind === ts.SyntaxKind.StringLiteral && (/^\$env\//.test(tokens[i].value) || tokens[i].value === 'node:process'))) {
          throw new Uncacheable('unverified_source_environment:src/' + relative);
        }
        if (tokens[i].kind === ts.SyntaxKind.StringLiteral &&
          (tokens[i - 1]?.text === 'from' || tokens[i - 1]?.kind === ts.SyntaxKind.ImportKeyword ||
            (tokens[i - 1]?.text === '(' && ['import', 'require', 'URL'].includes(tokens[i - 2]?.text)))) {
          if (tokens[i].value === 'process') throw new Uncacheable('unverified_source_environment:src/' + relative);
          checkImport(relative, tokens[i].value);
        }
      }
    };
    const inputs = {};
    for (const relative of policy.source_directories) {
      inputs[relative] = snapshotTree(path.join(source, relative), {
        excludedDirectories: relative === 'crypto/encryption' ? ['target'] : [],
        onFile: relative === 'src' ? checkSourceEnvironment : undefined,
      });
    }
    for (const relative of policy.root_files) inputs[relative] = { sha256: sha(readSourceInput(relative)) };
    const dependencies = snapshotTree(path.join(source, 'node_modules'), { dependencies: true });
    const recipe = {};
    for (const name of ['build-performance-frontend.mjs', 'frontend-build-artifacts.mjs', 'frontend-build-cache.mjs']) recipe[name] = sha(fs.readFileSync(path.join(directory, name)));
    const identity = { format: 1, mode, metadata, source: fs.realpathSync(source), inputs, dependencies,
      policy_sha256: sha(policyBytes), recipe, environment,
      commands: ['scripts/encryption/artifact.mjs', `--max-old-space-size=${heap} node_modules/vite/bin/vite.js build --sourcemap hidden`],
      toolchain: { node: process.version, versions: sorted(process.versions), platform: process.platform, architecture: process.arch,
        executable_sha256: sha(fs.readFileSync(process.execPath)), dependencies_root: fs.realpathSync(path.join(source, 'node_modules')) } };
    return { identity, key: sha(JSON.stringify(identity)) };
  } catch (error) {
    if (error instanceof Uncacheable) return { identity: null, reason: error.message };
    throw error;
  }
}

function inventory(root) {
  const files = {};
  function walk(relative = '') {
    for (const entry of fs.readdirSync(path.join(root, relative), { withFileTypes: true }).sort((a, b) => a.name < b.name ? -1 : 1)) {
      const child = relative ? `${relative}/${entry.name}` : entry.name;
      check(!entry.isSymbolicLink(), 'artifact_symlink');
      if (entry.isDirectory()) walk(child);
      else { check(entry.isFile(), 'artifact_not_regular_file'); files[child] = sha(fs.readFileSync(path.join(root, child))); }
    }
  }
  walk(); return files;
}

/** 核对公开与私有完整目录、清单绑定和来源；不信任仅 index.html 存在或只列部分文件的缓存。 */
export function verifyPackagedBuild(root, metadata) {
  const rootStat = fs.lstatSync(root);
  check(rootStat.isDirectory() && !rootStat.isSymbolicLink(), 'artifact_root_symlink');
  for (const name of ['frontend-build', 'frontend-debug']) {
    const stat = fs.lstatSync(path.join(root, name));
    check(stat.isDirectory() && !stat.isSymbolicLink(), 'artifact_directory_symlink:' + name);
  }
  for (const name of ['frontend-build-manifest.json', 'frontend-debug/source-map-manifest.json', '.dockerignore']) {
    const stat = fs.lstatSync(path.join(root, name));
    check(stat.isFile() && !stat.isSymbolicLink(), 'artifact_manifest_symlink:' + name);
  }
  const manifestBytes = fs.readFileSync(path.join(root, 'frontend-build-manifest.json'));
  const manifest = JSON.parse(manifestBytes);
  const debug = JSON.parse(fs.readFileSync(path.join(root, 'frontend-debug/source-map-manifest.json')));
  const publicMetadata = Object.fromEntries(Object.entries(manifest).filter(([key]) => key !== 'files'));
  const privateMetadata = Object.fromEntries(Object.entries(debug).filter(([key]) => !['files', 'production_manifest_sha256'].includes(key)));
  check(JSON.stringify(sorted(publicMetadata)) === JSON.stringify(sorted(metadata)) &&
    JSON.stringify(sorted(privateMetadata)) === JSON.stringify(sorted(metadata)), 'artifact_source_mismatch');
  check(debug.production_manifest_sha256 === sha(manifestBytes), 'private_manifest_binding');
  check(manifest.files?.['index.html'] && Object.keys(manifest.files).every(name => !name.endsWith('.map') && !name.startsWith('pyodide/')), 'invalid_public_manifest');
  check(Object.keys(debug.files).every(name => name.endsWith('.map') && !name.startsWith('pyodide/')), 'invalid_private_manifest');
  check(JSON.stringify(sorted(inventory(path.join(root, 'frontend-build')))) === JSON.stringify(sorted(manifest.files)), 'public_inventory_mismatch');
  const debugInventory = inventory(path.join(root, 'frontend-debug'));
  delete debugInventory['source-map-manifest.json'];
  check(JSON.stringify(sorted(debugInventory)) === JSON.stringify(sorted(debug.files)), 'private_inventory_mismatch');
  check(fs.readFileSync(path.join(root, '.dockerignore'), 'utf8') === privateDebugDockerIgnore, 'private_debug_not_excluded');
  const sum = files => Object.keys(files).reduce((size, name) => size + fs.statSync(path.join(root, files === manifest.files ? 'frontend-build' : 'frontend-debug', name)).size, 0);
  return { manifest, debugFiles: debug.files, bytes: { public: sum(manifest.files), debug: sum(debug.files) } };
}

const artifactNames = ['frontend-build', 'frontend-debug', 'frontend-build-manifest.json', '.dockerignore'];
const artifactSignature = root => sha(JSON.stringify([
  sha(fs.readFileSync(path.join(root, 'frontend-build-manifest.json'))),
  sha(fs.readFileSync(path.join(root, 'frontend-debug/source-map-manifest.json'))),
  sha(fs.readFileSync(path.join(root, '.dockerignore'))),
]));
function copyArtifacts(from, to) {
  fs.mkdirSync(to, { recursive: false, mode: 0o700 });
  for (const name of artifactNames) fs.cpSync(path.join(from, name), path.join(to, name), { recursive: true, errorOnExist: true, force: false });
}
function corrupt(entry, error) {
  return new Error(`frontend_cache_corrupt:${entry}:${error.message}; preserve this entry and use another --cache-dir or omit --cache-dir for a fresh build; keep any partial output and choose a new output directory`);
}

/** 命中前完整校验再恢复到新目录；缺失返回 null，损坏明确终止，绝不自动降级为旧产物。 */
export function restoreCachedBuild(cache, assessed, output, metadata) {
  check(!fs.existsSync(output), 'output_exists_preserve_existing_artifact');
  check(/^[a-f0-9]{64}$/.test(assessed.key), 'invalid_frontend_cache_key');
  const entry = path.join(cache, assessed.key);
  if (!fs.existsSync(entry)) return null;
  let receipt;
  try {
    const entryStat = fs.lstatSync(entry);
    check(entryStat.isDirectory() && !entryStat.isSymbolicLink(), 'entry_symlink');
    const receiptStat = fs.lstatSync(path.join(entry, 'cache-receipt.json'));
    check(receiptStat.isFile() && !receiptStat.isSymbolicLink(), 'receipt_symlink');
    receipt = JSON.parse(fs.readFileSync(path.join(entry, 'cache-receipt.json')));
    check(receipt.format === 1 && receipt.key === assessed.key && JSON.stringify(receipt.identity) === JSON.stringify(assessed.identity), 'cache_identity_mismatch');
    check(JSON.stringify(receipt.checks) === JSON.stringify(['crypto-artifact', 'vite-build', 'complete-public-private-inventory']), 'cache_verification_receipt_missing');
    verifyPackagedBuild(path.join(entry, 'artifacts'), metadata);
    check(artifactSignature(path.join(entry, 'artifacts')) === receipt.artifact_signature, 'cache_input_artifact_binding_mismatch');
  } catch (error) { throw corrupt(entry, error); }
  copyArtifacts(path.join(entry, 'artifacts'), output);
  const packaged = verifyPackagedBuild(output, metadata);
  check(artifactSignature(output) === receipt.artifact_signature, 'frontend_cache_restore_changed_during_copy; keep output and choose a new output directory');
  return packaged;
}

/** 只接纳本次真实成功构建并完整核对的产物；用临时目录原子发布，不覆盖已有缓存或导入旧清单补认来源。 */
export function publishCachedBuild(cache, assessed, output, metadata) {
  check(/^[a-f0-9]{64}$/.test(assessed.key), 'invalid_frontend_cache_key');
  verifyPackagedBuild(output, metadata);
  fs.mkdirSync(cache, { recursive: true, mode: 0o700 });
  const entry = path.join(cache, assessed.key);
  check(!fs.existsSync(entry), 'frontend_cache_entry_exists_preserve_existing_artifact:' + entry);
  const temporary = fs.mkdtempSync(path.join(cache, '.partial-'));
  copyArtifacts(output, path.join(temporary, 'artifacts'));
  verifyPackagedBuild(path.join(temporary, 'artifacts'), metadata);
  fs.writeFileSync(path.join(temporary, 'cache-receipt.json'), JSON.stringify({ format: 1, key: assessed.key, identity: assessed.identity,
    artifact_signature: artifactSignature(path.join(temporary, 'artifacts')),
    checks: ['crypto-artifact', 'vite-build', 'complete-public-private-inventory'] }, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  fs.renameSync(temporary, entry);
}
