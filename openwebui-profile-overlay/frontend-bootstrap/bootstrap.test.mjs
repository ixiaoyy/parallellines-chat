import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import test from 'node:test';

const repository = path.resolve(import.meta.dirname, '../..');
const source = path.resolve(process.env.OPENWEBUI_BOOTSTRAP_SOURCE_ROOT ?? path.join(repository, '.build/frontend-bootstrap-20261103/source'));
const require = createRequire(path.join(source, 'package.json'));
const ts = require('typescript');
function load(code, scope = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports, console: { error() {} }, AbortController, DOMException, Error, TypeError, URLSearchParams, setTimeout, clearTimeout, ...scope });
  return exports;
}
function client(relative, name, scope) {
  const text = fs.readFileSync(path.join(source, relative), 'utf8');
  const start = text.indexOf('export const ' + name + ' =');
  assert.notEqual(start, -1);
  return load(text.slice(start, text.indexOf('\n};', start) + 3), {
    WEBUI_BASE_URL: '', WEBUI_API_BASE_URL: '/api/v1', ...scope,
  })[name];
}
const bootstrapCode = fs.readFileSync(path.join(import.meta.dirname, 'bootstrap.ts'), 'utf8');

test('bootstrap deadline is 12 seconds and aborts the actual request, even if a client swallows cancellation', async () => {
  let timeout, duration, cleared = false, signal;
  const { createBootstrapRequests } = load(bootstrapCode, {
    setTimeout(fn, ms) { timeout = fn; duration = ms; return 1; }, clearTimeout() { cleared = true; },
  });
  const requests = createBootstrapRequests();
  const result = requests.run(value => { signal = value; return new Promise(() => {}); });
  assert.equal(duration, 12000);
  timeout();
  await assert.rejects(result, error => error.name === 'BootstrapTimeoutError');
  assert.equal(signal.aborted, true);
  assert.equal(cleared, true);
});

test('page cancellation stops pending work and refuses new work without clearing shared state', async () => {
  const { createBootstrapRequests } = load(bootstrapCode);
  const requests = createBootstrapRequests();
  let signal;
  const result = requests.run(value => { signal = value; return new Promise(() => {}); });
  requests.cancel();
  await assert.rejects(result, error => error.name === 'AbortError');
  assert.equal(signal.aborted, true);
  await assert.rejects(requests.run(async () => 'late'), error => error.name === 'AbortError');
});

test('configuration reads and the existing forward-auth probe share the optional signal', async () => {
  const calls = [];
  const signal = new AbortController().signal;
  const getConfig = client('src/lib/apis/index.ts', 'getBackendConfig', {
    fetch: async (_url, options) => {
      calls.push(options);
      if (calls.length === 1) throw new TypeError('network');
      return { type: 'opaqueredirect', status: 0 };
    },
  });
  await assert.rejects(getConfig(signal), error => error.authRedirect === true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].signal, signal);
  assert.equal(calls[1].signal, signal);
  assert.equal(calls[1].redirect, 'manual');
});

test('old client calls omit signal and preserve model authorization errors', async () => {
  const authorization = { detail: { code: 'main_oauth_authorization_required', message: 'Authorize with Main' } };
  let options;
  const getModels = client('src/lib/apis/index.ts', 'getModels', {
    fetch: async (_url, value) => { options = value; return { ok: false, json: async () => authorization }; },
  });
  await assert.rejects(getModels('existing-token', null, false, true), error => error === authorization);
  assert.equal('signal' in options, false);
  assert.equal(options.headers.authorization, 'Bearer existing-token');
});

test('user settings retain raw/default query meaning and pass cancellation only when requested', async () => {
  const calls = [];
  const getSettings = client('src/lib/apis/users/index.ts', 'getUserSettings', {
    fetch: async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({ ui: {} }) }; },
  });
  await getSettings('token');
  const signal = new AbortController().signal;
  await getSettings('token', true, signal);
  assert.equal(calls[0][0], '/api/v1/users/user/settings');
  assert.equal('signal' in calls[0][1], false);
  assert.equal(calls[1][0], '/api/v1/users/user/settings?raw=true');
  assert.equal(calls[1][1].signal, signal);
});

test('direct model discovery uses the same signal and cannot turn cancellation into an empty success', async () => {
  const controller = new AbortController();
  const cancelled = new DOMException('cancelled', 'AbortError');
  const getDirect = client('src/lib/apis/openai/index.ts', 'getOpenAIModelsDirect', {
    fetch: async (_url, options) => {
      assert.equal(options.signal, controller.signal);
      controller.abort(cancelled);
      throw cancelled;
    },
  });
  await assert.rejects(getDirect('http://fixture', '', controller.signal), error => error === cancelled);
});

test('session recovery preserves HTTP status, legacy null behavior, and parent cancellation', async () => {
  const text = fs.readFileSync(path.join(source, 'src/lib/apis/auths/index.ts'), 'utf8');
  const start = text.indexOf('const requestSessionUser =');
  const end = text.indexOf('\nexport const ldapUserSignIn', start);
  const code = text.slice(start, end);
  let mode = '401';
  const auth = load(code, { WEBUI_API_BASE_URL: '/api/v1', fetch: async (_url, options) => {
    if (mode === 'network') throw new TypeError('network');
    if (mode === 'cancel') return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true }));
    return { ok: false, status: 401, json: async () => ({ detail: 'Expired' }) };
  } });
  await assert.rejects(auth.getSessionUserForBootstrap('token'), error => error.status === 401);
  mode = 'network';
  assert.equal(await auth.getSessionUser('token'), null);
  await assert.rejects(auth.getSessionUserForBootstrap('token'), error => error instanceof TypeError);
  mode = 'cancel';
  const controller = new AbortController();
  const result = auth.getSessionUserForBootstrap('token', controller.signal);
  controller.abort();
  await assert.rejects(result, error => error.name === 'AbortError');
});

test('settings retry restores only known navigation once for the same account and expires after five minutes', () => {
  const code = fs.readFileSync(path.join(import.meta.dirname, 'settingsRetry.ts'), 'utf8').replace(/^import type.*\n/m, '');
  let now = 1000000;
  const retry = load(code, { Date: { now: () => now } });
  const items = new Map();
  const storage = { getItem: key => items.get(key) ?? null, setItem: (key, value) => items.set(key, value), removeItem: key => items.delete(key) };
  const show = { tab: 'admin:models', state: { id: 'fixture-model' } };
  retry.saveSettingsRetry(storage, 'current-user', show);
  assert.deepEqual(JSON.parse(JSON.stringify(retry.takeSettingsRetry(storage, 'current-user'))), show);
  assert.equal(retry.takeSettingsRetry(storage, 'current-user'), null);
  retry.saveSettingsRetry(storage, 'current-user', show);
  assert.equal(retry.takeSettingsRetry(storage, 'another-user'), null);
  assert.equal(items.size, 0);
  retry.saveSettingsRetry(storage, 'current-user', true);
  now += 300001;
  assert.equal(retry.takeSettingsRetry(storage, 'current-user'), null);
  assert.equal(items.size, 0);
  assert.throws(() => retry.saveSettingsRetry(storage, 'current-user', { tab: 'connections', state: { apiKey: 'not-persisted' } }), /Unsupported/);
  assert.equal(items.size, 0);
  assert.throws(() => retry.saveSettingsRetry({ ...storage, setItem() { throw new Error('Storage blocked'); } }, 'current-user', 'general'), /Storage blocked/);
});
