import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const BRIDGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const FORBIDDEN_COUPLED_REFS = [
	'openwebui-profile-overlay',
	'main-profile-overlay',
	'../deploy',
	'..\\deploy',
	'../compose',
	'..\\compose'
];

test('identity-bridge contains its own self-contained build and dependency files', () => {
	for (const required of ['package.json', 'package-lock.json', 'Dockerfile', '.dockerignore', 'server.mjs', 'config.mjs']) {
		assert.equal(fs.existsSync(path.join(BRIDGE_ROOT, required)), true, `missing ${required}`);
	}
});

test('identity-bridge source and Dockerfile have zero references to Chat overlays or parent directories', () => {
	const entries = fs.readdirSync(BRIDGE_ROOT, { withFileTypes: true });
	const filesToScan = entries
		.filter((e) => e.isFile() && (e.name.endsWith('.mjs') || e.name === 'Dockerfile'))
		.map((e) => path.join(BRIDGE_ROOT, e.name));

	assert.ok(filesToScan.length >= 6);

	for (const file of filesToScan) {
		const content = fs.readFileSync(file, 'utf8');
		for (const forbidden of FORBIDDEN_COUPLED_REFS) {
			assert.equal(
				content.includes(forbidden),
				false,
				`${path.basename(file)} must not reference ${forbidden}`
			);
		}
		if (file.endsWith('.mjs')) {
			assert.equal(
				/from\s+['"]\.\.\//.test(content),
				false,
				`${path.basename(file)} must not import from parent repository directory`
			);
		}
	}
});
