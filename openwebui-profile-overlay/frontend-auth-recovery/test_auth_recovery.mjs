import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';
import {
	AUTHORIZATION_ERROR_CODE,
	AUTHORIZATION_TOAST_ID,
	AUTHORIZATION_MESSAGE,
	REAUTHORIZE_PATH,
	showMainOAuthAuthorizationRequired,
	dismissMainOAuthAuthorizationRequired
} from './main-oauth-auth-recovery.mjs';

const authorizationError = () => ({
	detail: {
		code: AUTHORIZATION_ERROR_CODE,
		message: '<script>never render upstream text</script>',
		reauthorize_url: 'https://untrusted.example/collect'
	}
});

function recorder() {
	const records = { shown: [], dismissed: [], navigations: [] };
	return {
		records,
		toast: {
			error: (message, options) => records.shown.push({ message, options }),
			dismiss: (id) => records.dismissed.push(id)
		},
		navigate: (url) => records.navigations.push(url)
	};
}

test('exact 403/code shows one stable toast identity and requires an explicit click', () => {
	const { records, toast, navigate } = recorder();
	const body = authorizationError();
	for (let i = 0; i < 2; i++) {
		assert.equal(showMainOAuthAuthorizationRequired(403, body, toast, navigate), true);
	}
	assert.equal(records.shown.length, 2);
	assert.equal(new Set(records.shown.map((item) => item.options.id)).size, 1);
	assert.equal(records.shown[0].options.id, AUTHORIZATION_TOAST_ID);
	assert.equal(records.shown[0].message, AUTHORIZATION_MESSAGE);
	assert.equal(records.shown[0].options.duration, Infinity);
	assert.deepEqual(records.navigations, []);
	records.shown[0].options.action.onClick();
	assert.deepEqual(records.navigations, [REAUTHORIZE_PATH]);
	assert.equal(REAUTHORIZE_PATH, '/oauth/oidc/login');
	assert.equal(body.detail.reauthorize_url, 'https://untrusted.example/collect');
});

test('unrelated status or body never prompts, redirects, or dismisses another error', () => {
	const { records, toast, navigate } = recorder();
	for (const [status, body] of [
		[401, authorizationError()], [500, authorizationError()], [200, authorizationError()],
		[403, null], [403, 'error'], [403, {}], [403, { detail: 'forbidden' }],
		[403, { detail: { code: 'other_authorization_error' } }]
	]) assert.equal(showMainOAuthAuthorizationRequired(status, body, toast, navigate), false);
	assert.deepEqual(records, { shown: [], dismissed: [], navigations: [] });
});

test('notification failures never replace model success or the original API error', () => {
	const toast = { error() { throw new Error('toast unavailable'); }, dismiss() { throw new Error('no toast'); } };
	assert.equal(showMainOAuthAuthorizationRequired(403, authorizationError(), toast, () => {}), false);
	assert.doesNotThrow(() => dismissMainOAuthAuthorizationRequired(toast));
});

// Exercise the actual patched upstream getModels body, without importing Svelte,
// making network calls, or maintaining a second copy of the request logic.
const patchedPath = process.env.OPENWEBUI_PATCHED_INDEX;
if (!patchedPath) throw new Error('Set OPENWEBUI_PATCHED_INDEX to the staged patched src/lib/apis/index.ts');
const patchedSource = readFileSync(patchedPath, 'utf8');
const methodSource = patchedSource.slice(
	patchedSource.indexOf('export const getModels = async ('),
	patchedSource.indexOf('export const unloadModel = async ')
).replace('export const getModels =', 'globalThis.getModels =');

function modelClient(response, thrown) {
	const { records, toast, navigate } = recorder();
	const requests = [];
	const context = {
		URLSearchParams, WEBUI_BASE_URL: '', toast,
		showMainOAuthAuthorizationRequired, dismissMainOAuthAuthorizationRequired,
		window: { location: { assign: navigate } },
		console: { error() {} },
		fetch: async (...args) => { requests.push(args); if (thrown) throw thrown; return response; },
		localStorage: { removeItem() { throw new Error('must not log out'); } }
	};
	vm.runInNewContext(stripTypeScriptTypes(methodSource), context);
	return { getModels: context.getModels, records, requests };
}

test('actual getModels rethrows the original authorization JSON and keeps JWT request intact', async () => {
	const body = authorizationError();
	const client = modelClient({ ok: false, status: 403, json: async () => body });
	await assert.rejects(client.getModels('synthetic-jwt', null, false, true), (error) => error === body);
	assert.equal(client.requests[0][0], '/api/models?refresh=true');
	assert.equal(client.requests[0][1].headers.authorization, 'Bearer synthetic-jwt');
	assert.equal(client.records.shown.length, 1);
	assert.deepEqual(client.records.navigations, []);
	client.records.shown[0].options.action.onClick();
	assert.deepEqual(client.records.navigations, ['/oauth/oidc/login']);
});

test('actual getModels preserves unrelated HTTP and network failures without recovery toast', async () => {
	const body = { detail: 'generic upstream failure' };
	const http = modelClient({ ok: false, status: 500, json: async () => body });
	await assert.rejects(http.getModels(), (error) => error === body);
	assert.equal(http.records.shown.length, 0);
	const originalError = new Error('synthetic network error');
	const network = modelClient(undefined, originalError);
	await assert.rejects(network.getModels(), (error) => error === originalError);
	assert.equal(network.records.shown.length, 0);
});

test('actual getModels returns success unchanged and dismisses only the recovery toast', async () => {
	const models = [{ id: 'synthetic-model', name: 'Synthetic' }];
	const client = modelClient({ ok: true, status: 200, json: async () => ({ data: models }) });
	assert.equal(await client.getModels('synthetic-jwt'), models);
	assert.equal(client.records.shown.length, 0);
	assert.deepEqual(client.records.dismissed, [AUTHORIZATION_TOAST_ID]);
	assert.deepEqual(client.records.navigations, []);
});
