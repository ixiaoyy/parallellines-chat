// 统一 Chat 前端叠加构建入口（Open WebUI v0.12.5）。
// 后续新增前端功能只在本入口注册/追加 overlay，不再复制新的 build-*-frontend.mjs 或 Dockerfile.*。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { packageFrontendBuild, privateDebugDockerIgnore, verifyPublicFrontend } from './frontend-build-artifacts.mjs';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const UPSTREAM_VERSION = '0.12.5';
export const UPSTREAM_COMMIT = 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca';
export const PACKAGE_LOCK_SHA256 = 'a16007f1de06fe3ca6cbac2a0025055495501a26c9bf067562320dc2e5260c9d';

export const DEFAULT_FEATURE_ORDER = Object.freeze([
	'frontend-performance',
	'frontend-bootstrap',
	'frontend-auth-recovery',
	'frontend-silent-recovery',
	'unified-login',
	'personal-connections',
	'frontend-i18n',
	'frontend-chat-rooms'
]);
export const RELEASE_FEATURE_ORDER = Object.freeze([
	...DEFAULT_FEATURE_ORDER, 'frontend-chat-error-guidance', 'frontend-model-capabilities'
]);

const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex');
const SINGLE_APPLY = new Set(['frontend-performance', 'frontend-bootstrap', 'frontend-silent-recovery']);
const MIXED_OVERLAYS = new Set(['unified-login', 'personal-connections']);
const isBackend = (relative) => relative.startsWith('backend/');
const publicMetadata = (featureOrder, hashes, trafficLoaderSha = null) => ({
	upstream_commit: UPSTREAM_COMMIT,
	feature_order: featureOrder,
	feature_manifests: hashes,
	preserved_runtime: ['pyodide/'],
	...(trafficLoaderSha
		? { traffic_analytics_enabled: true, traffic_loader_sha256: trafficLoaderSha }
		: {})
});

export function readTrafficLoaderBytes(repositoryRoot = REPO_ROOT) {
	const loaderPath = path.join(repositoryRoot, 'openwebui-profile-overlay', 'traffic-analytics', 'loader.js');
	if (!fs.existsSync(loaderPath)) {
		throw new Error('traffic_loader_source_missing');
	}
	const bytes = fs.readFileSync(loaderPath);
	if (bytes.length === 0) {
		throw new Error('traffic_loader_source_empty');
	}
	return bytes;
}

/** 仅在显式开启 --traffic-analytics 时填充原生空 loader 槽，不覆盖已有定制。 */
export function applyTrafficAnalyticsLoader(builtDirectory, repositoryRoot = REPO_ROOT, enabled = false) {
	if (!enabled) return null;
	const indexPath = path.join(builtDirectory, 'index.html');
	const targetPath = path.join(builtDirectory, 'static', 'loader.js');
	if (!fs.existsSync(indexPath) || !fs.statSync(indexPath).isFile()) {
		throw new Error('native_deferred_loader_entry_missing_or_changed');
	}
	const indexText = fs.readFileSync(indexPath, 'utf8');
	const matches = indexText.match(/\/static\/loader\.js/g) || [];
	if (matches.length !== 1) {
		throw new Error('native_deferred_loader_entry_missing_or_changed');
	}
	if (!fs.existsSync(targetPath) || !fs.statSync(targetPath).isFile()) {
		throw new Error('native_loader_slot_missing');
	}
	const loaderBytes = readTrafficLoaderBytes(repositoryRoot);
	const previous = fs.readFileSync(targetPath);
	if (previous.length > 0 && !previous.equals(loaderBytes)) {
		throw new Error('native_loader_slot_not_empty_preserve_existing_customization');
	}
	if (!previous.equals(loaderBytes)) {
		fs.writeFileSync(targetPath, loaderBytes);
	}
	return sha256(loaderBytes);
}

export function parseCliArgs(argv) {
	const positionals = [];
	let prepared = false;
	let dryRun = false;
	let frontendOnly = false;
	let trafficAnalytics = false;
	let features = null;
	let extend = [];
	let fromFeature = null;

	for (let i = 0; i < argv.length; i += 1) {
		const arg = argv[i];
		if (arg === '--prepared') {
			prepared = true;
		} else if (arg === '--dry-run') {
			dryRun = true;
		} else if (arg === '--frontend-only') {
			frontendOnly = true;
		} else if (arg === '--traffic-analytics') {
			trafficAnalytics = true;
		} else if (arg === '--features') {
			const next = argv[++i];
			if (!next) throw new Error('missing_features_value');
			features = next
				.split(',')
				.map((s) => s.trim())
				.filter(Boolean);
		} else if (arg === '--extend') {
			const next = argv[++i];
			if (!next) throw new Error('missing_extend_value');
			extend.push(
				...next
					.split(',')
					.map((s) => s.trim())
					.filter(Boolean)
			);
		} else if (arg === '--from-feature') {
			const next = argv[++i];
			if (!next) throw new Error('missing_from_feature_value');
			fromFeature = next.trim();
		} else if (arg.startsWith('--')) {
			throw new Error(`unknown_flag:${arg}`);
		} else {
			positionals.push(arg);
		}
	}

	if (features && extend.length > 0) {
		throw new Error('cannot_combine_features_and_extend');
	}

	const featureOrder = resolveFeatureChain({ features, extend });
	if (fromFeature && !featureOrder.includes(fromFeature)) {
		throw new Error(`unknown_from_feature:${fromFeature}`);
	}

	if (!dryRun && positionals.length !== 2) {
		throw new Error('usage: node deploy/build-chat-frontend.mjs <SOURCE> <OUTPUT> [--frontend-only] [--prepared] [--traffic-analytics] [--from-feature <name>] [--features a,b | --extend x,y] [--dry-run]');
	}
	if (dryRun && positionals.length > 2) {
		throw new Error('too_many_positional_args');
	}

	const source = positionals[0] ? path.resolve(positionals[0]) : null;
	const output = positionals[1] ? path.resolve(positionals[1]) : null;
	if (source && output && source === output) {
		throw new Error('source_equals_output');
	}
	if (!dryRun && output && fs.existsSync(output)) {
		throw new Error('output_exists_preserve_artifact');
	}

	return { source, output, prepared, dryRun, frontendOnly, trafficAnalytics, featureOrder, fromFeature };
}

export function resolveFeatureChain({ features = null, extend = [] } = {}) {
	const raw = features ? [...features] : [...DEFAULT_FEATURE_ORDER, ...extend];
	if (raw.length === 0) throw new Error('empty_feature_chain');
	const seen = new Set();
	for (const name of raw) {
		if (!/^[a-z0-9-]+$/.test(name)) throw new Error(`invalid_feature_name:${name}`);
		if (seen.has(name)) throw new Error(`duplicate_feature:${name}`);
		seen.add(name);
	}
	return raw;
}

export function loadOverlaySpecs(repositoryRoot, featureOrder, fromFeature = null) {
	const overlayRoot = path.join(repositoryRoot, 'openwebui-profile-overlay');
	const firstApplyIndex = fromFeature ? featureOrder.indexOf(fromFeature) : 0;
	const expected = {};
	const baseline = {};
	const hashes = {};
	const manifests = {};

	for (const [index, name] of featureOrder.entries()) {
		const manifestPath = path.join(overlayRoot, name, 'manifest.json');
		if (!fs.existsSync(manifestPath)) {
			throw new Error(`missing_manifest:${name}`);
		}
		const bytes = fs.readFileSync(manifestPath);
		const manifest = JSON.parse(bytes.toString('utf8'));
		if (manifest.upstream_commit !== UPSTREAM_COMMIT) {
			throw new Error(`upstream_commit_mismatch:${name}`);
		}
		if (Array.isArray(manifest.feature_order)) {
			const expectedPrefix = featureOrder.slice(0, index + 1);
			if (JSON.stringify(manifest.feature_order) !== JSON.stringify(expectedPrefix)) {
				throw new Error(`feature_order_mismatch:${name}`);
			}
		}

		hashes[name] = sha256(bytes);
		manifests[name] = manifest;

		for (const [relative, value] of Object.entries(manifest.files || {})) {
			expected[relative] = value.after_sha256;
			if (index < firstApplyIndex) {
				baseline[relative] = value.after_sha256;
			}
		}
		for (const [relative, value] of Object.entries(manifest.backend || {})) {
			const backendPath = 'backend/open_webui/' + relative;
			expected[backendPath] = value.after_sha256;
			if (index < firstApplyIndex) {
				baseline[backendPath] = value.after_sha256;
			}
		}
	}

	return { expected, baseline, hashes, manifests, firstApplyIndex };
}

function verifyFiles(source, fileHashes, errorPrefix) {
	for (const [relative, expectedHash] of Object.entries(fileHashes)) {
		const fullPath = path.join(source, relative);
		if (!fs.existsSync(fullPath)) {
			throw new Error(`${errorPrefix}_missing:${relative}`);
		}
		if (sha256(fs.readFileSync(fullPath)) !== expectedHash) {
			throw new Error(`${errorPrefix}_changed:${relative}`);
		}
	}
}

/** 校验官方依赖锁和版本。制品工作流在安装依赖之前调用，构建在应用后再次核对。 */
export function verifyUpstreamPackage(source) {
	if (sha256(fs.readFileSync(path.join(source, 'package-lock.json'))) !== PACKAGE_LOCK_SHA256) {
		throw new Error('lockfile_changed');
	}
	const version = JSON.parse(fs.readFileSync(path.join(source, 'package.json'), 'utf8')).version;
	if (version !== UPSTREAM_VERSION) throw new Error('version_changed');
	return version;
}

/** 在隔离源准备已锁定的叠加层；纯前端模式只读写浏览器源码，后端要求留在 reviewed manifests 中。 */
export function prepareUnifiedFrontend(options, repositoryRoot, specs) {
	const { source, prepared, frontendOnly, featureOrder } = options;
	const overlayRoot = path.join(repositoryRoot, 'openwebui-profile-overlay');
	const relevant = (files) => frontendOnly
		? Object.fromEntries(Object.entries(files).filter(([relative]) => !isBackend(relative)))
		: files;

	if (!prepared) {
		verifyFiles(source, relevant(specs.baseline), 'baseline_source');
		for (const name of featureOrder.slice(specs.firstApplyIndex)) {
			const applyScript = path.join(overlayRoot, name, 'apply.py');
			if (!fs.existsSync(applyScript)) {
				throw new Error(`missing_apply_script:${name}`);
			}
			const args = [applyScript, source];
			if (frontendOnly && MIXED_OVERLAYS.has(name)) args.push('--frontend-only');
			// 旧三个 CLI 的单次调用已核前像，不能把 --check 当作无写检查再次调用。
			for (const invocation of SINGLE_APPLY.has(name) ? [args] : [[...args, '--check'], args]) {
				const result = spawnSync(process.env.PYTHON || 'python', invocation, {
					stdio: 'inherit',
					windowsHide: true,
					timeout: 30_000
				});
				if (result.status !== 0) {
					throw new Error(`overlay_apply_failed:${name}`);
				}
			}
		}
	}
	verifyFiles(source, relevant(specs.expected), 'prepared_source');
}

/** 在上传前或下载后用同一十层来源校验两项公开输入，不处理同目录的其他构建输出。 */
export function verifyReleaseFrontend(output, repositoryRoot = REPO_ROOT, { trafficAnalytics = false } = {}) {
	const { hashes } = loadOverlaySpecs(repositoryRoot, RELEASE_FEATURE_ORDER);
	const trafficLoaderSha = trafficAnalytics ? sha256(readTrafficLoaderBytes(repositoryRoot)) : null;
	return verifyPublicFrontend({
		publicDirectory: path.join(output, 'frontend-build'),
		manifestPath: path.join(output, 'frontend-build-manifest.json'),
		metadata: publicMetadata(RELEASE_FEATURE_ORDER, hashes, trafficLoaderSha)
	});
}

export function runUnifiedFrontendBuild(argv, repositoryRoot = REPO_ROOT) {
	const options = parseCliArgs(argv);
	const specs = loadOverlaySpecs(repositoryRoot, options.featureOrder, options.fromFeature);
	const trafficLoaderSha = options.trafficAnalytics ? sha256(readTrafficLoaderBytes(repositoryRoot)) : null;

	if (options.dryRun) {
		if (options.source) {
			const files = options.prepared ? specs.expected : specs.baseline;
			verifyFiles(options.source, options.frontendOnly
				? Object.fromEntries(Object.entries(files).filter(([relative]) => !isBackend(relative))) : files,
			options.prepared ? 'prepared_source' : 'baseline_source');
		}
		return {
			dry_run: true,
			version: UPSTREAM_VERSION,
			upstream_commit: UPSTREAM_COMMIT,
			feature_order: options.featureOrder,
			apply_from: options.fromFeature || options.featureOrder[0],
			manifest_count: Object.keys(specs.hashes).length,
			tracked_files: Object.keys(specs.expected).length,
			traffic_analytics: options.trafficAnalytics,
			...(trafficLoaderSha ? { traffic_loader_sha256: trafficLoaderSha } : {})
		};
	}

	const { source, output, featureOrder } = options;
	verifyUpstreamPackage(source);
	prepareUnifiedFrontend(options, repositoryRoot, specs);
	const version = verifyUpstreamPackage(source);

	const heap = Number(process.env.FRONTEND_BUILD_HEAP_MB || 8192);
	if (!Number.isInteger(heap) || heap < 1024 || heap > 12288) {
		throw new Error('invalid_build_heap_limit');
	}

	const env = {
		...process.env,
		npm_package_version: version,
		APP_BUILD_HASH: sha256(JSON.stringify(specs.hashes)),
		APP_BUILD_CHANNEL: 'unknown'
	};

	for (const args of [
		['scripts/encryption/artifact.mjs'],
		[`--max-old-space-size=${heap}`, 'node_modules/vite/bin/vite.js', 'build', '--sourcemap', 'hidden']
	]) {
		const result = spawnSync(process.execPath, args, {
			cwd: source,
			env,
			stdio: 'inherit',
			windowsHide: true,
			timeout: 1_200_000
		});
		if (result.status !== 0) {
			throw new Error('chat_frontend_build_failed');
		}
	}

	const builtDir = path.join(source, 'build');
	const appliedTrafficSha = applyTrafficAnalyticsLoader(builtDir, repositoryRoot, options.trafficAnalytics);
	const result = packageFrontendBuild({
		built: builtDir,
		publicDirectory: path.join(output, 'frontend-build'),
		debugDirectory: path.join(output, 'frontend-debug'),
		manifestPath: path.join(output, 'frontend-build-manifest.json'),
		metadata: publicMetadata(featureOrder, specs.hashes, appliedTrafficSha)
	});
	fs.writeFileSync(path.join(output, '.dockerignore'), privateDebugDockerIgnore, { flag: 'wx' });

	return {
		version,
		feature_order: featureOrder,
		file_count: Object.keys(result.manifest.files).length,
		public_bytes: result.bytes.public,
		traffic_analytics: options.trafficAnalytics
	};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
	const args = process.argv.slice(2);
	if (args[0] === '--verify-public') {
		const rest = args.slice(1);
		const trafficAnalytics = rest.includes('--traffic-analytics');
		const targets = rest.filter((arg) => arg !== '--traffic-analytics');
		if (targets.length !== 1 || targets[0].startsWith('--')) {
			throw new Error('usage: node deploy/build-chat-frontend.mjs --verify-public <OUTPUT> [--traffic-analytics]');
		}
		console.log(JSON.stringify(verifyReleaseFrontend(targets[0], REPO_ROOT, { trafficAnalytics })));
	} else {
		console.log(JSON.stringify(runUnifiedFrontendBuild(args)));
	}
}
