import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';
import { pathToFileURL } from 'node:url';

const patchedPath = process.env.OPENWEBUI_SILENT_INDEX;
if (!patchedPath) throw new Error('Set OPENWEBUI_SILENT_INDEX to prepared source/src/lib/apis/index.ts');
const {
	createModelAuthorizationRecovery, CHAT_ORIGIN, AUTHORIZATION_ENDPOINT,
	RECOVERY_ATTEMPT_KEY, RECOVERY_MESSAGE_TYPE, RECOVERY_TIMEOUT_MS, showModelRecoveryFailure
} = await import(new URL('./main-model-silent-recovery.mjs', pathToFileURL(patchedPath)));
import {
	showMainOAuthAuthorizationRequired, dismissMainOAuthAuthorizationRequired
} from '../frontend-auth-recovery/main-oauth-auth-recovery.mjs';

const requestId = 'a'.repeat(32);
const tick = () => new Promise(setImmediate);


function browser(options = {}) {
	const values = new Map();
	const listeners = new Set();
	const timers = new Map();
	const frames = [];
	const requests = [];
	let timerId = 0;
	const initialResponse = { ok: true, json: async () => ({ request_id: requestId,
		authorization_url: `${AUTHORIZATION_ENDPOINT}?state=plmr_${requestId}` }) };
	const environment = {
		sessionStorage: {
			getItem: (key) => values.get(key) ?? null,
			setItem: (key, value) => values.set(key, value),
			removeItem: (key) => values.delete(key)
		},
		fetch: async (...args) => {
			requests.push(args);
			return options.fetch ? options.fetch(...args) : initialResponse;
		},
		setTimeout: (callback, milliseconds) => { timers.set(++timerId, { callback, milliseconds }); return timerId; },
		clearTimeout: (id) => timers.delete(id),
		window: {
			addEventListener: (name, handler) => { assert.equal(name, 'message'); listeners.add(handler); },
			removeEventListener: (name, handler) => { assert.equal(name, 'message'); listeners.delete(handler); },
			location: { assign() { throw new Error('automatic top-level navigation forbidden'); } }
		},
		document: {
			createElement: (name) => {
				assert.equal(name, 'iframe');
				return { attributes: {}, contentWindow: {}, removed: false,
					setAttribute(name, value) { this.attributes[name] = value; },
					remove() { this.removed = true; } };
			},
			body: { appendChild: (frame) => frames.push(frame) }
		}
	};
	return {
		environment, values, listeners, timers, frames, requests, initialResponse,
		emit(data = {}, options = {}) {
			const event = { origin: CHAT_ORIGIN, source: frames.at(-1)?.contentWindow,
				data: { type: RECOVERY_MESSAGE_TYPE, request_id: requestId, status: 'success', ...data }, ...options };
			for (const handler of listeners) handler(event);
		},
		expire() { for (const { callback } of [...timers.values()]) callback(); }
	};
}

test('concurrent starts share one frame and forged messages cannot complete it', async () => {
 const b = browser(); const results = [];
 const recovery = createModelAuthorizationRecovery(b.environment, (status) => results.push(status));
 assert.equal(recovery.start('synthetic-jwt').pending, true);
 assert.equal(recovery.start('synthetic-jwt').pending, true);
 await tick();
 assert.equal(b.requests.length, 1);
 assert.equal(b.requests[0][0], '/oauth/oidc/model-authorization');
 assert.equal(b.requests[0][1].headers.Authorization, 'Bearer synthetic-jwt');
 assert.deepEqual([...b.values], [[RECOVERY_ATTEMPT_KEY, '1']]);
 assert.equal(b.frames[0].attributes.sandbox, 'allow-scripts allow-same-origin allow-forms');
 assert.equal(b.frames[0].referrerPolicy, 'no-referrer');
 assert.equal([...b.timers.values()][0].milliseconds, RECOVERY_TIMEOUT_MS);
 b.emit({}, { origin: 'https://untrusted.invalid' }); b.emit({}, { source: {} });
 b.emit({ request_id: 'b'.repeat(32) }); b.emit({ type: 'other-event' }); b.emit({ status: 'unknown' });
 await tick(); assert.deepEqual(results, []);
 b.emit(); await tick();
 assert.deepEqual(results, ['success']);
 assert.equal(b.listeners.size, 0); assert.equal(b.timers.size, 0); assert.equal(b.frames[0].removed, true);
 // frame 成功尚不代表实际目录可读，再次 403 不能创建第二个 frame。
 assert.equal(b.values.get(RECOVERY_ATTEMPT_KEY), '1');
 assert.equal(recovery.start('synthetic-jwt').pending, false); assert.equal(b.requests.length, 1);
 recovery.healthy(); assert.equal(b.values.size, 0);
 assert.equal(recovery.start('synthetic-jwt').pending, true); await tick();
 assert.equal(b.requests.length, 2); recovery.cancel(); await tick();
});

test('only actual healthy models reset a failure phase and never reset an active flight', async () => {
 const b = browser(); const results = [];
 const recovery = createModelAuthorizationRecovery(b.environment, (status) => results.push(status));
 recovery.start('synthetic-jwt'); recovery.healthy();
 assert.equal(b.values.get(RECOVERY_ATTEMPT_KEY), '1');
 await tick(); b.emit({ status: 'failed' }); await tick();
 assert.deepEqual(results, ['failed']);
 assert.equal(recovery.start('synthetic-jwt').pending, false);
 assert.equal(createModelAuthorizationRecovery(b.environment).start('synthetic-jwt').pending, false);
 recovery.healthy(); assert.equal(recovery.start('synthetic-jwt').pending, true);
 await tick(); recovery.cancel(); await tick();
});

test('identity mismatch persists across reload and never offers account-switching login action', async () => {
 const b = browser(); const results = [];
 const recovery = createModelAuthorizationRecovery(b.environment, (status) => results.push(status));
 recovery.start('synthetic-jwt'); await tick(); b.emit({ status: 'identity_mismatch' }); await tick();
 assert.deepEqual(results, ['identity_mismatch']);
 assert.equal(b.values.get(RECOVERY_ATTEMPT_KEY), 'identity_mismatch');
 assert.equal(createModelAuthorizationRecovery(b.environment).start('synthetic-jwt').status, 'identity_mismatch');
 const notices = []; const navigations = [];
 showModelRecoveryFailure('identity_mismatch', { error: (...args) => notices.push(args) }, (url) => navigations.push(url));
 assert.match(notices[0][0], /账号不一致/); assert.match(notices[0][0], /原来的同一账号/);
 assert.equal(notices[0][1].action, undefined); assert.deepEqual(navigations, []);
 showModelRecoveryFailure('failed', { error: (...args) => notices.push(args) }, (url) => navigations.push(url));
 assert.doesNotMatch(notices[1][0], /过期|退出登录/); assert.deepEqual(navigations, []);
 notices[1][1].action.onClick(); assert.deepEqual(navigations, ['/oauth/oidc/login']);
 assert.doesNotThrow(() => showModelRecoveryFailure('failed', { error() { throw new Error('unavailable'); } }, () => {}));
 let explicit = 0;
 showModelRecoveryFailure('identity_mismatch', { error: (...args) => notices.push(args) },
  () => { throw new Error('ordinary login forbidden'); }, () => { explicit++; recovery.retry('synthetic-jwt'); });
 assert.equal(explicit, 0); notices[2][1].action.onClick(); await tick();
 assert.equal(explicit, 1); assert.equal(b.requests.length, 2); recovery.cancel(); await tick();
});

test('one deadline covers stalled prepare and clears all resources', async () => {
 const b = browser({ fetch: () => new Promise(() => {}) }); const results = [];
 createModelAuthorizationRecovery(b.environment, (status) => results.push(status)).start('synthetic-jwt');
 b.expire(); await tick();
 assert.deepEqual(results, ['failed']); assert.equal(b.requests[0][1].signal.aborted, true);
 assert.equal(b.frames.length, 0); assert.equal(b.timers.size, 0); assert.equal(b.listeners.size, 0);
});

test('frame timeout does not reset deadline or infer Main login state', async () => {
 const b = browser(); const results = [];
 createModelAuthorizationRecovery(b.environment, (status) => results.push(status)).start('synthetic-jwt');
 const timer = [...b.timers.keys()][0]; await tick(); assert.deepEqual([...b.timers.keys()], [timer]);
 b.expire(); await tick();
 assert.deepEqual(results, ['failed']); assert.equal(b.frames[0].removed, true);
 assert.equal(b.listeners.size, 0); assert.equal(b.timers.size, 0);
});

test('layout cancellation aborts background work without a late result or navigation', async () => {
 const b = browser(); const results = [];
 const recovery = createModelAuthorizationRecovery(b.environment, (status) => results.push(status));
 recovery.start('synthetic-jwt'); await tick(); recovery.cancel(); await tick();
 assert.deepEqual(results, []); assert.equal(b.requests[0][1].signal.aborted, true);
 assert.equal(b.frames[0].removed, true); assert.equal(b.listeners.size, 0); assert.equal(b.timers.size, 0);
});

test('missing/blocked storage, empty token and SSR do not start an automatic flow', () => {
 for (const mode of ['missing', 'read-blocked', 'write-blocked']) {
  const b = browser();
  if (mode === 'missing') b.environment.sessionStorage = undefined;
  if (mode === 'read-blocked') b.environment.sessionStorage.getItem = () => { throw new Error('unavailable'); };
  if (mode === 'write-blocked') b.environment.sessionStorage.setItem = () => { throw new Error('unavailable'); };
  const recovery = createModelAuthorizationRecovery(b.environment);
  assert.equal(recovery.start('synthetic-jwt').pending, false);
  assert.equal(recovery.start('synthetic-jwt').pending, false); assert.equal(b.requests.length, 0);
 }
 assert.equal(createModelAuthorizationRecovery({}).start('synthetic-jwt').pending, false);
 const b = browser(); assert.equal(createModelAuthorizationRecovery(b.environment).start('').pending, false);
 assert.equal(b.requests.length, 0);
});

test('untrusted or mismatched authorization URL never enters an iframe', async () => {
 for (const url of [`https://untrusted.invalid/auth?state=plmr_${requestId}`,
  `${AUTHORIZATION_ENDPOINT}?state=plmr_${'b'.repeat(32)}`,
  `${AUTHORIZATION_ENDPOINT}?state=plmr_${requestId}#fragment`,
  `https://user@pingxingxian.space/auth/oidc/auth?state=plmr_${requestId}`]) {
  const b = browser({ fetch: async () => ({ ok: true, json: async () => ({ request_id: requestId, authorization_url: url }) }) });
  const results = []; createModelAuthorizationRecovery(b.environment, (status) => results.push(status)).start('synthetic-jwt');
  await tick(); assert.deepEqual(results, ['failed']); assert.equal(b.frames.length, 0); assert.equal(b.timers.size, 0);
 }
});

test('prepare identity mismatch is explicit and does not offer a frame', async () => {
 const b = browser({ fetch: async () => ({ ok: false, json: async () => ({ detail: { code: 'model_recovery_identity_mismatch' } }) }) });
 const results = []; createModelAuthorizationRecovery(b.environment, (status) => results.push(status)).start('synthetic-jwt');
 await tick(); assert.deepEqual(results, ['identity_mismatch']);
 assert.equal(b.frames.length, 0); assert.equal(b.listeners.size, 0); assert.equal(b.timers.size, 0);
});

// 直接执行已补丁的真实 getModels，避免维护第二份请求实现或加载整个 Svelte 应用。
const patchedSource = readFileSync(patchedPath, 'utf8');
const method = patchedSource.slice(patchedSource.indexOf('export const getModels = async ('),
	patchedSource.indexOf('export const unloadModel = async ')).replace('export const getModels =', 'globalThis.getModels =');
const authorizationError = () => ({ detail: { code: 'main_oauth_authorization_required' } });
const failedResponse = (body, status = 403) => ({ ok: false, status, json: async () => body });

function modelClient(responses, background = () => {}) {
 const calls = { requests: [], starts: [], shown: [], dismiss: [], navigations: [], cleared: 0 };
 const context = {
  URLSearchParams, WEBUI_BASE_URL: '', console: { error() {} },
  fetch: async (...args) => { calls.requests.push(args); const next = responses.shift(); if (next instanceof Error) throw next; return next; },
  startModelAuthorizationRecovery: (...args) => { calls.starts.push(args); return background(...args); },
  clearModelAuthorizationAttempt: () => { calls.cleared++; },
  showMainOAuthAuthorizationRequired, dismissMainOAuthAuthorizationRequired,
  toast: { error: (...args) => calls.shown.push(args), dismiss: (...args) => calls.dismiss.push(args) },
  window: { location: { assign: (path) => calls.navigations.push(path) } },
  localStorage: { removeItem() { throw new Error('must not clear Chat login'); } }
 };
 vm.runInNewContext(stripTypeScriptTypes(method), context);
 return { getModels: context.getModels, calls };
}

test('actual getModels starts background without awaiting, retrying, or replacing its original error', async () => {
 const original = authorizationError(); const controller = new AbortController();
 const client = modelClient([failedResponse(original)], () => new Promise(() => {}));
 await assert.rejects(client.getModels('synthetic-jwt', null, false, true, controller.signal), (error) => error === original);
 assert.equal(client.calls.starts.length, 1); assert.equal(client.calls.requests.length, 1);
 assert.equal(client.calls.requests[0][1].signal, controller.signal);
 assert.deepEqual(client.calls.navigations, []); assert.equal(client.calls.shown.length, 0);
});

test('actual unchanged 12-second bootstrap finishes while a 20-second recovery remains pending', async () => {
 const b = browser({ fetch: () => new Promise(() => {}) }); const recovery = createModelAuthorizationRecovery(b.environment);
 const client = modelClient([failedResponse(authorizationError())], () => recovery.start('synthetic-jwt'));
 const timers = new Map(); let timer = 0;
 const source = readFileSync(new URL('../../.build/chat-repair-20261111/source/src/lib/utils/bootstrap.ts', import.meta.url), 'utf8')
  .replace('export class BootstrapTimeoutError', 'class BootstrapTimeoutError')
  .replace('export function createBootstrapRequests', 'globalThis.createBootstrapRequests = function');
 const context = { AbortController, DOMException,
  setTimeout: (fn, ms) => { assert.equal(ms, 12000); timers.set(++timer, fn); return timer; }, clearTimeout: (id) => timers.delete(id) };
 vm.runInNewContext(stripTypeScriptTypes(source), context);
 const startup = context.createBootstrapRequests(); let loggedError;
 await startup.run((signal) => client.getModels('synthetic-jwt', null, false, false, signal).catch((error) => { loggedError = error; }));
 assert.equal(loggedError.detail.code, 'main_oauth_authorization_required');
 assert.equal(timers.size, 0); assert.equal(b.timers.size, 1); assert.equal(b.requests.length, 1);
 recovery.cancel(); await tick(); assert.equal(b.timers.size, 0);
});

test('actual layout handles a recovery event with only one bounded model read and cleans up', async () => {
 const path = patchedPath.replace(/src[\\/]lib[\\/]apis[\\/]index\.ts$/, 'src/routes/(app)/+layout.svelte');
 const source = readFileSync(path, 'utf8');
 const start = source.indexOf('\tonMount(() => {', source.indexOf('// 后台授权不阻塞启动'));
 const code = source.slice(start, source.indexOf('\tconst setToolServers =', start));
 const listeners = new Map(); let cleanup; let reads = 0; let cancelled = 0;
 const signal = new AbortController().signal;
 const context = { destroyed: false, MODELS_RECOVERED_EVENT: 'restored',
  onMount: (fn) => { cleanup = fn(); },
  window: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name) => listeners.delete(name) },
  startupRequests: { run: async (fn) => fn(signal) }, setModels: async (received) => { assert.equal(received, signal); reads++; },
  cancelModelAuthorizationRecovery: () => { cancelled++; }, console: { error() {} } };
 vm.runInNewContext(stripTypeScriptTypes(code), context);
 listeners.get('restored')(); await tick(); assert.equal(reads, 1);
 cleanup(); assert.equal(cancelled, 1); assert.equal(listeners.size, 0);
});

test('actual directory success clears phase marker; unrelated errors do not start recovery', async () => {
 const models = [{ id: 'synthetic-model' }];
 const client = modelClient([{ ok: true, json: async () => ({ data: models }) }]);
 assert.equal(await client.getModels('synthetic-jwt'), models); assert.equal(client.calls.cleared, 1);
 for (const [status, body, base] of [[403, { detail: { code: 'main_model_discovery_failed' } }, false],
  [429, authorizationError(), false], [500, authorizationError(), false], [403, authorizationError(), true]]) {
  const other = modelClient([failedResponse(body, status)]);
  await assert.rejects(other.getModels('synthetic-jwt', null, base), (error) => error === body);
  assert.equal(other.calls.starts.length, 0); assert.equal(other.calls.requests.length, 1);
 }
});

test('aborted actual model request retains caller reason and never starts background', async () => {
 const controller = new AbortController(); const reason = new Error('bootstrap cancelled'); controller.abort(reason);
 const client = modelClient([failedResponse(authorizationError())]);
 await assert.rejects(client.getModels('synthetic-jwt', null, false, false, controller.signal), (error) => error === reason);
 assert.equal(client.calls.starts.length, 0); assert.deepEqual(client.calls.navigations, []);
});
