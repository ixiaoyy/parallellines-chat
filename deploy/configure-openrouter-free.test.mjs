import test from 'node:test';
import assert from 'node:assert/strict';
import { CHAT_BASE, RELAY_BASE, FREE_MODEL, configureOpenRouterFree } from './configure-openrouter-free.mjs';

const clone = value => structuredClone(value);
const paid = ['gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol'];
const readGrant = { principal_type: 'user', principal_id: '*', permission: 'read' };
function fixture(model = true, modelIds = paid) {
  return {
    connection: {
      ENABLE_OPENAI_API: true, OPENAI_API_BASE_URLS: [RELAY_BASE, 'https://unrelated.example.invalid/v1'],
      OPENAI_API_KEYS: ['', 'UNRELATED-KEY-MUST-NOT-LEAK'],
      OPENAI_API_CONFIGS: {
        '0': { enable: true, auth_type: 'system_oauth', api_type: 'responses', forward_cookies: false, headers: {}, model_ids: [...modelIds], keep: 'original' },
        '1': { enable: true, auth_type: 'bearer', api_type: 'chat', keep: 'unrelated' },
      },
    },
    model: model ? { id: FREE_MODEL, base_model_id: null, is_active: true, name: 'Existing free name', user_id: 'existing-owner', params: { temperature: 0.2 }, meta: { description: 'keep' }, access_grants: [{ id: 'existing', principal_type: 'group', principal_id: 'private-group', permission: 'read' }] } : null,
    calls: [], backups: [],
  };
}
function fakeFetch(state, failAt) {
  return async (url, options) => {
    assert.equal(new URL(url).origin, CHAT_BASE);
    assert.equal(options.headers.Authorization, 'Bearer synthetic-admin');
    assert.equal(options.redirect, 'error');
    assert.equal(options.credentials, 'omit');
    assert.ok(options.signal instanceof AbortSignal);
    const path = url.slice(CHAT_BASE.length);
    const body = options.body ? JSON.parse(options.body) : null;
    state.calls.push({ method: options.method, path, body });
    if (failAt === `${options.method} ${path}`) throw new Error('SECRET synthetic-admin full-config');
    if (path.startsWith('/api/v1/models/model?id=')) {
      assert.equal(new URL(url).searchParams.get('id'), FREE_MODEL);
      return state.model ? Response.json(clone(state.model)) : Response.json({}, { status: 404 });
    }
    if (options.method === 'GET' && path === '/openai/config') return Response.json(clone(state.connection));
    if (options.method === 'POST' && path === '/api/v1/models/model/access/update') {
      assert.equal(body.id, FREE_MODEL);
      const record = state.model ?? { id: FREE_MODEL, base_model_id: null, is_active: true, name: body.name, user_id: 'synthetic-admin', meta: {}, params: {} };
      state.model = { ...record, access_grants: body.access_grants.map((grant, index) => ({ ...grant, id: `stored-${index}` })) };
      return Response.json({});
    }
    if (options.method === 'POST' && path === '/openai/config/update') {
      // 与固定版本真实 update_config 相同：仅保留数字索引键，并归一化密钥数组长度。
      const urls = clone(body.OPENAI_API_BASE_URLS);
      const keys = clone(body.OPENAI_API_KEYS.slice(0, urls.length));
      while (keys.length < urls.length) keys.push('');
      const validKeys = new Set(urls.map((_, index) => String(index)));
      state.connection = {
        ENABLE_OPENAI_API: body.ENABLE_OPENAI_API,
        OPENAI_API_BASE_URLS: urls, OPENAI_API_KEYS: keys,
        OPENAI_API_CONFIGS: Object.fromEntries(Object.entries(clone(body.OPENAI_API_CONFIGS)).filter(([key]) => validKeys.has(key))),
      };
      return Response.json({});
    }
    assert.fail(`Unexpected endpoint ${options.method} ${path}`);
  };
}
const input = apply => ({ adminBearer: 'synthetic-admin', apply, backupFile: '/data/private/free-model-before.json' });
const dependencies = state => ({ fetchImpl: fakeFetch(state), backupWriter: async (path, data) => state.backups.push({ path, data: clone(data) }) });

test('plan is read-only, does not expose secrets or change defaults/tasks', async () => {
  const state = fixture();
  const before = clone(state);
  const result = await configureOpenRouterFree(input(false), dependencies(state));
  assert.deepEqual(state.connection, before.connection);
  assert.deepEqual(state.model, before.model);
  assert.equal(state.calls.some(call => call.method === 'POST'), false);
  assert.equal(state.backups.length, 0);
  assert.equal(result.connectionFilterChanged, true);
  assert.equal(JSON.stringify(result).includes('UNRELATED-KEY'), false);
});

test('applies only free grant and appends to existing filter, preserving paid and unrelated connection', async () => {
  const state = fixture();
  const original = clone(state);
  const result = await configureOpenRouterFree(input(true), dependencies(state));
  assert.equal(result.ok, true);
  const expectedConnection = clone(original.connection);
  expectedConnection.OPENAI_API_CONFIGS['0'].model_ids.push(FREE_MODEL);
  assert.deepEqual(state.connection, expectedConnection);
  for (const key of ['name', 'params', 'meta', 'user_id', 'base_model_id', 'is_active']) assert.deepEqual(state.model[key], original.model[key]);
  assert.ok(state.model.access_grants.some(grant => ['principal_type', 'principal_id', 'permission'].every(key => grant[key] === readGrant[key])));
  assert.ok(state.model.access_grants.some(grant => grant.principal_id === 'private-group'));
  assert.equal(state.backups.length, 1);
  assert.equal(JSON.stringify(state.backups).includes('UNRELATED-KEY'), false);
  assert.deepEqual(state.backups[0].data.beforeAccessGrants, original.model.access_grants);
  assert.deepEqual(state.calls.filter(call => call.method === 'POST').map(call => call.path), ['/api/v1/models/model/access/update', '/openai/config/update']);
  state.calls = []; state.backups = [];
  const again = await configureOpenRouterFree(input(true), dependencies(state));
  assert.equal(again.accessChanged, false);
  assert.equal(again.connectionFilterChanged, false);
  assert.equal(state.calls.some(call => call.method === 'POST'), false);
  assert.equal(state.backups.length, 0);
});

test('dynamic upstream model list remains dynamic, missing record is created only for free model', async () => {
  const state = fixture(false, []);
  const originalConnection = clone(state.connection);
  await configureOpenRouterFree(input(true), dependencies(state));
  assert.deepEqual(state.connection, originalConnection);
  assert.equal(state.model.id, FREE_MODEL);
  assert.equal(state.model.name, 'OpenRouter 免费');
  assert.equal(state.backups[0].data.modelExisted, false);
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 1);
});

test('rejects wrong protocol, shared key, alias or unsafe grant without writing', async () => {
  for (const modify of [
    state => { state.connection.OPENAI_API_CONFIGS['0'].api_type = 'chat'; },
    state => { state.connection.OPENAI_API_KEYS[0] = 'SECRET-KEY'; },
    state => { state.model.base_model_id = paid[0]; },
    state => { state.model.access_grants.push({ principal_type: 'user', principal_id: '*', permission: 'write' }); },
  ]) {
    const state = fixture(); modify(state);
    await assert.rejects(configureOpenRouterFree(input(true), dependencies(state)), error => !JSON.stringify(error.receipt).includes('SECRET'));
    assert.equal(state.calls.some(call => call.method === 'POST'), false);
    assert.equal(state.backups.length, 0);
  }
});

test('backup failure stops before any configuration write', async () => {
  const state = fixture();
  await assert.rejects(configureOpenRouterFree(input(true), { fetchImpl: fakeFetch(state), backupWriter: async () => { throw new Error('private path'); } }), error => error.receipt.failedStep === 'backup.write');
  assert.equal(state.calls.some(call => call.method === 'POST'), false);
});

test('rejects target/unrelated URL keys, invalid numeric indexes and mismatched keys before any write', async () => {
  for (const modify of [
    state => { state.connection.OPENAI_API_CONFIGS[RELAY_BASE] = state.connection.OPENAI_API_CONFIGS['0']; delete state.connection.OPENAI_API_CONFIGS['0']; },
    state => { state.connection.OPENAI_API_CONFIGS['https://unrelated.example.invalid/v1'] = state.connection.OPENAI_API_CONFIGS['1']; delete state.connection.OPENAI_API_CONFIGS['1']; },
    state => { state.connection.OPENAI_API_CONFIGS['9'] = { enable: true }; },
    state => { state.connection.OPENAI_API_CONFIGS['00'] = { enable: true }; },
    state => { state.connection.OPENAI_API_KEYS.pop(); },
  ]) {
    for (const apply of [false, true]) {
      const state = fixture(); modify(state);
      const before = clone(state);
      await assert.rejects(configureOpenRouterFree(input(apply), dependencies(state)), error => ['connection.indexed-config-required', 'connection.key-count'].includes(error.receipt.failedStep));
      assert.deepEqual(state.connection, before.connection);
      assert.deepEqual(state.model, before.model);
      assert.equal(state.calls.some(call => call.method === 'POST'), false);
      assert.equal(state.backups.length, 0);
    }
  }
});

test('concurrent connection change is retained and partial state is explicit', async () => {
  const state = fixture(); const fetchImpl = fakeFetch(state);
  const conflictFetch = async (url, options) => {
    const response = await fetchImpl(url, options);
    if (options.method === 'POST' && url.endsWith('/api/v1/models/model/access/update')) state.connection.OPENAI_API_CONFIGS['1'].keep = 'changed-by-other-admin';
    return response;
  };
  await assert.rejects(configureOpenRouterFree(input(true), { ...dependencies(state), fetchImpl: conflictFetch }), error => error.receipt.failedStep === 'connection.concurrent-change' && error.receipt.completedSteps.includes('model.access') && error.receipt.partialOrUncertain);
  assert.equal(state.connection.OPENAI_API_CONFIGS['1'].keep, 'changed-by-other-admin');
  assert.equal(state.calls.some(call => call.path === '/openai/config/update'), false);
});

test('network errors expose only stable step, never credentials or raw response', async () => {
  const state = fixture();
  await assert.rejects(configureOpenRouterFree(input(true), { ...dependencies(state), fetchImpl: fakeFetch(state, 'GET /openai/config') }), error => error.receipt.failedStep === 'connection.read' && !JSON.stringify(error.receipt).includes('synthetic-admin'));
});
