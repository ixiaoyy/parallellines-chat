import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
	DEFAULT_FEATURE_ORDER,
	UPSTREAM_COMMIT,
	UPSTREAM_VERSION,
	parseCliArgs,
	resolveFeatureChain,
	loadOverlaySpecs,
	runUnifiedFrontendBuild
} from './build-chat-frontend.mjs';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('resolveFeatureChain returns canonical 8-layer order by default', () => {
	assert.deepEqual(resolveFeatureChain(), [...DEFAULT_FEATURE_ORDER]);
	assert.equal(DEFAULT_FEATURE_ORDER.length, 8);
});

test('resolveFeatureChain supports --extend and rejects invalid/duplicate names', () => {
	const extended = resolveFeatureChain({ extend: ['frontend-error-guidance'] });
	assert.equal(extended.at(-1), 'frontend-error-guidance');
	assert.equal(extended.length, DEFAULT_FEATURE_ORDER.length + 1);

	assert.throws(() => resolveFeatureChain({ features: ['frontend-i18n', 'frontend-i18n'] }), /duplicate_feature/);
	assert.throws(() => resolveFeatureChain({ features: ['../escape'] }), /invalid_feature_name/);
	assert.throws(() => resolveFeatureChain({ features: [] }), /empty_feature_chain/);
});

test('parseCliArgs enforces argument safety rules', () => {
	assert.throws(() => parseCliArgs([]), /usage:/);
	assert.throws(() => parseCliArgs(['src', 'src']), /source_equals_output/);
	assert.throws(() => parseCliArgs(['--features', 'a', '--extend', 'b', '--dry-run']), /cannot_combine_features_and_extend/);
	assert.throws(() => parseCliArgs(['--unknown', '--dry-run']), /unknown_flag/);
	assert.throws(() => parseCliArgs(['--from-feature', 'nonexistent', '--dry-run']), /unknown_from_feature/);

	const parsed = parseCliArgs(['--dry-run', '--from-feature', 'frontend-i18n']);
	assert.equal(parsed.dryRun, true);
	assert.equal(parsed.fromFeature, 'frontend-i18n');
	assert.deepEqual(parsed.featureOrder, [...DEFAULT_FEATURE_ORDER]);
});

test('loadOverlaySpecs validates all 8 real repo overlay manifests in order', () => {
	const specs = loadOverlaySpecs(REPO_ROOT, [...DEFAULT_FEATURE_ORDER], 'frontend-i18n');
	assert.equal(Object.keys(specs.hashes).length, 8);
	assert.equal(specs.firstApplyIndex, 6);
	assert.ok(Object.keys(specs.expected).length > 0);
	assert.ok(Object.keys(specs.baseline).length > 0);
	assert.equal(specs.manifests['frontend-chat-rooms'].upstream_commit, UPSTREAM_COMMIT);
});

test('dry-run succeeds against real repository manifests without invoking Vite', () => {
	const summary = runUnifiedFrontendBuild(['--dry-run'], REPO_ROOT);
	assert.equal(summary.dry_run, true);
	assert.equal(summary.version, UPSTREAM_VERSION);
	assert.equal(summary.upstream_commit, UPSTREAM_COMMIT);
	assert.equal(summary.manifest_count, 8);
	assert.ok(summary.tracked_files > 0);
});
