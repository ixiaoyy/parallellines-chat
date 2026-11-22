// 统一 Chat 前端叠加构建入口（Open WebUI v0.12.5）。
// 后续新增前端功能只在本入口注册/追加 overlay，不再复制新的 build-*-frontend.mjs 或 Dockerfile.*。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { packageFrontendBuild, privateDebugDockerIgnore } from './frontend-build-artifacts.mjs';

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

const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex');

export function parseCliArgs(argv) {
	const positionals = [];
	let prepared = false;
	let dryRun = false;
	let features = null;
	let extend = [];
	let fromFeature = null;

	for (let i = 0; i < argv.length; i += 1) {
		const arg = argv[i];
		if (arg === '--prepared') {
			prepared = true;
		} else if (arg === '--dry-run') {
			dryRun = true;
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
		throw new Error('usage: node deploy/build-chat-frontend.mjs <SOURCE> <OUTPUT> [--prepared] [--from-feature <name>] [--features a,b | --extend x,y] [--dry-run]');
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

	return { source, output, prepared, dryRun, featureOrder, fromFeature };
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

export function runUnifiedFrontendBuild(argv, repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')) {
	const options = parseCliArgs(argv);
	const specs = loadOverlaySpecs(repositoryRoot, options.featureOrder, options.fromFeature);

	if (options.dryRun) {
		if (options.source) {
			verifyFiles(options.source, options.prepared ? specs.expected : specs.baseline, options.prepared ? 'prepared_source' : 'baseline_source');
		}
		return {
			dry_run: true,
			version: UPSTREAM_VERSION,
			upstream_commit: UPSTREAM_COMMIT,
			feature_order: options.featureOrder,
			apply_from: options.fromFeature || options.featureOrder[0],
			manifest_count: Object.keys(specs.hashes).length,
			tracked_files: Object.keys(specs.expected).length
		};
	}

	const { source, output, prepared, featureOrder } = options;
	const overlayRoot = path.join(repositoryRoot, 'openwebui-profile-overlay');

	if (!prepared) {
		verifyFiles(source, specs.baseline, 'baseline_source');
		for (const name of featureOrder.slice(specs.firstApplyIndex)) {
			const applyScript = path.join(overlayRoot, name, 'apply.py');
			if (!fs.existsSync(applyScript)) {
				throw new Error(`missing_apply_script:${name}`);
			}
			for (const args of [
				[applyScript, source, '--check'],
				[applyScript, source]
			]) {
				const result = spawnSync(process.env.PYTHON || 'python', args, {
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

	verifyFiles(source, specs.expected, 'prepared_source');

	const lockHash = sha256(fs.readFileSync(path.join(source, 'package-lock.json')));
	if (lockHash !== PACKAGE_LOCK_SHA256) {
		throw new Error('lockfile_changed');
	}
	const version = JSON.parse(fs.readFileSync(path.join(source, 'package.json'), 'utf8')).version;
	if (version !== UPSTREAM_VERSION) {
		throw new Error('version_changed');
	}

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

	const metadata = {
		upstream_commit: UPSTREAM_COMMIT,
		feature_order: featureOrder,
		feature_manifests: specs.hashes,
		preserved_runtime: ['pyodide/']
	};
	const result = packageFrontendBuild({
		built: path.join(source, 'build'),
		publicDirectory: path.join(output, 'frontend-build'),
		debugDirectory: path.join(output, 'frontend-debug'),
		manifestPath: path.join(output, 'frontend-build-manifest.json'),
		metadata
	});
	fs.writeFileSync(path.join(output, '.dockerignore'), privateDebugDockerIgnore, { flag: 'wx' });

	return {
		version,
		feature_order: featureOrder,
		file_count: Object.keys(result.manifest.files).length,
		public_bytes: result.bytes.public
	};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
	const summary = runUnifiedFrontendBuild(process.argv.slice(2));
	console.log(JSON.stringify(summary));
}
