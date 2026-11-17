// 执行候选原生函数，核对目录/保存接线；所有 API 均为内存夹具，无真实服务调用。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { decoratePersonalModel, isPersonalPrefix, readPersonalModels } from './personal-connections-runtime.mjs';

const root = path.resolve(process.env.PERSONAL_CONNECTIONS_SOURCE ?? '.build/personal-connections-20261117/source');
const require = createRequire(path.join(root, 'package.json'));
const ts = require('typescript');
const prefix = 'pl-personal-' + 'a'.repeat(32);
const platform = { id: 'same-model', name: 'Platform model' };
function loadFunction(relative, start, end, globals) {
  const source = fs.readFileSync(path.join(root, relative), 'utf8');
  const from = source.indexOf(start); const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from);
  const snippet = source.slice(from, to).replace('export const', 'const');
  const name = /const (\w+)/.exec(snippet)[1];
  const js = ts.transpileModule(snippet + `\nthis.result = ${name};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext({ URLSearchParams, AbortController, console: { error() {} }, ...globals });
  vm.runInContext(js, context);
  return context.result;
}
function directory({ status = 200, error = {}, missingFirst = false } = {}) {
  const notices = [];
  const connections = { OPENAI_API_BASE_URLS: missingFirst ? ['https://unused.invalid/v1', 'https://provider.invalid/v1'] : ['https://provider.invalid/v1'],
    OPENAI_API_KEYS: missingFirst ? ['unused-key', 'fixture-key'] : ['fixture-key'],
    OPENAI_API_CONFIGS: { [missingFirst ? 1 : 0]: { prefix_id: prefix, name: 'My provider', model_ids: ['same-model'], enable: true } } };
  const getModels = loadFunction('src/lib/apis/index.ts', 'export const getModels = ', 'export const unloadModel = ', {
    WEBUI_BASE_URL: '', fetch: async () => ({ ok: status === 200, status, json: async () => status === 200 ? { data: [platform] } : error }),
    get: () => ({ id: 'owner-A' }), user: {}, normalizeTags: value => value ?? [], decoratePersonalModel, isPersonalPrefix, readPersonalModels,
    getOpenAIModelsDirect: async () => { throw new Error('Manual IDs must not query provider'); },
    toast: { error: value => notices.push(value) }, startModelAuthorizationRecovery() {}, showMainOAuthAuthorizationRequired() {},
    dismissMainOAuthAuthorizationRequired() {}, clearModelAuthorizationAttempt() {},
  });
  return { getModels, connections, notices };
}

test('candidate model directory preserves platform model and stable personal source/index', async () => {
  const fixture = directory({ missingFirst: true });
  const models = await fixture.getModels('fixture-token', fixture.connections);
  assert.equal(models.length, 2);
  assert.equal(models[0].id, platform.id);
  assert.equal(models[1].id, `${prefix}.same-model`);
  assert.equal(models[1].urlIdx, '1');
  assert.equal(models[1].name, 'My provider / same-model');
  assert.equal(models[1].personal_connection.owner_id, 'owner-A');
  assert.ok(!JSON.stringify(models).includes('fixture-key'));
});

test('platform OAuth model failure stays visible without blocking personal models; 401 remains failure', async () => {
  const fixture = directory({ status: 403, error: { detail: { code: 'main_oauth_authorization_required' } } });
  const models = await fixture.getModels('fixture-token', fixture.connections);
  assert.equal(models.length, 1);
  assert.ok(models[0].direct);
  assert.equal(fixture.notices.length, 1);
  const unauthorized = directory({ status: 401, error: { detail: 'Invalid login' } });
  await assert.rejects(unauthorized.getModels('expired', unauthorized.connections));
  const ordinary = directory({ status: 503, error: { detail: 'Unavailable' } });
  await assert.rejects(ordinary.getModels('fixture-token', ordinary.connections));
});

test('personal model metadata never forwards arbitrary provider response fields', () => {
  const model = decoratePersonalModel({ id: `${prefix}.m`, name: 'm', urlIdx: '0', openai: { id: 'm', key: 'secret' }, api_key: 'secret', headers: { Authorization: 'secret' } },
    { prefix_id: prefix, name: 'Personal' }, 'https://provider.invalid/v1', 'owner-A');
  assert.ok(!JSON.stringify(model).includes('secret'));
  assert.equal(model.openai.id, 'm');
});

test('candidate save distinguishes committed connection from failed directory refresh', async () => {
  let persisted = false; const notices = [];
  const save = loadFunction('src/lib/components/chat/SettingsModal.svelte', '\tconst saveSettings = ', '\n\tconst getModels = ', {
    localStorage: { token: 'fixture-token' }, updateUserSettings: async (_, value) => { persisted = true; return value; },
    settings: { set: async () => {} }, models: { set: async () => {} }, config: {}, $config: {},
    mergeUiSettings: (_, value) => value, getModels: async () => { throw new Error('directory unavailable'); },
    toast: { error: value => notices.push(value) }, personalUiSettings: {},
  });
  await save({ directConnections: {} });
  assert.ok(persisted);
  assert.equal(notices.length, 1);
  await assert.rejects(save({ theme: 'dark' }));
});
