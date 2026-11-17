// 发布审查回归：执行候选原生全局停止/启动函数，所有网络和身份为合成夹具。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { handlePersonalCompletion, cancelPersonalChatRequests, decoratePersonalModel, isPersonalPrefix,
  readPersonalModels, deferPersonalModelDiscovery, mergePersonalModelDiscovery } from './personal-connections-runtime.mjs';

const root = path.resolve(process.env.PERSONAL_CONNECTIONS_SOURCE ?? '.build/personal-connections-20261117/source');
const require = createRequire(path.join(root, 'package.json'));
const ts = require('typescript');
const prefix = 'pl-personal-' + 'a'.repeat(32);
const connections = { OPENAI_API_BASE_URLS: ['https://fixture.invalid/v1'], OPENAI_API_KEYS: ['fake-key'],
  OPENAI_API_CONFIGS: { 0: { prefix_id: prefix, name: 'Personal', enable: true, model_ids: [] } } };
const model = decoratePersonalModel({ id: `${prefix}.m`, openai: { id: 'm' }, name: 'm', urlIdx: '0' }, connections.OPENAI_API_CONFIGS[0], connections.OPENAI_API_BASE_URLS[0], 'owner');
const turn = () => new Promise(resolve => setTimeout(resolve, 0));
function compile(relative, from, to, globals) {
  const text = fs.readFileSync(path.join(root, relative), 'utf8');
  const start = text.indexOf(from); const end = text.indexOf(to, start);
  assert.ok(start >= 0 && end > start);
  const snippet = text.slice(start, end).replace('export const', 'const');
  const name = /const (\w+)/.exec(snippet)[1];
  const js = ts.transpileModule(snippet + `\nthis.result = ${name};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext({ URLSearchParams, AbortController, console: { error() {} }, ...globals });
  vm.runInContext(js, context);
  return context.result;
}

test('real global cancellation event aborts only its chat/message even outside the active temporary chat', async () => {
  const signals = []; const emitted = [];
  const create = (chatId, messageId) => handlePersonalCompletion({ data: { model, form_data: { model: model.id, stream: true }, channel: chatId + ':' + messageId },
    chatId, messageId, connections, ownerId: 'owner', getOwnerId: () => 'owner', cb() {}, socket: { emit: (...args) => emitted.push(args) },
    chatCompletion: async (_, body, url, signal) => {
      signals.push(signal);
      const bodyStream = new ReadableStream({ start(controller) { signal.addEventListener('abort', () => controller.error(new Error('aborted')), { once: true }); } });
      return [new Response(bodyStream, { status: 200 })];
    } });
  const first = create('chat-A', 'message-1'); const second = create('chat-A', 'message-2'); const third = create('chat-B', 'message-1');
  await turn();
  const handler = compile('src/routes/+layout.svelte', '\tconst chatEventHandler = ', '\n\tconst channelEventHandler = ', {
    cancelPersonalChatRequests, isTemporaryChatId: () => true, $chatId: 'other-current-chat', $page: { url: { pathname: '/' } },
  });
  await handler({ shared: false, chat_id: 'chat-A', message_id: 'message-1', data: { type: 'chat:tasks:cancel' } });
  await first;
  assert.equal(signals[0].aborted, true); assert.equal(signals[1].aborted, false); assert.equal(signals[2].aborted, false);
  cancelPersonalChatRequests('chat-A', 'message-2'); cancelPersonalChatRequests('chat-B', 'message-1');
  await Promise.all([second, third]);
  assert.equal(emitted.filter(([, data]) => data?.done === true).length, 3);
  const source = fs.readFileSync(path.join(root, 'src/routes/+layout.svelte'), 'utf8');
  assert.ok(source.includes('data, chatId: event.chat_id, messageId: event.message_id, connections:'));
});

function startup(load) {
  const notices = []; let values = [];
  const state = { ownerId: 'owner', connections: structuredClone(connections) };
  const getModels = compile('src/lib/apis/index.ts', 'export const getModels = ', 'export const unloadModel = ', {
    WEBUI_BASE_URL: '', fetch: async () => ({ ok: true, json: async () => ({ data: [{ id: 'platform', name: 'Platform' }] }) }),
    user: { kind: 'user' }, settings: { kind: 'settings' }, get: store => store.kind === 'user' ? { id: state.ownerId } : { directConnections: state.connections },
    normalizeTags: value => value ?? [], getOpenAIModelsDirect: load, decoratePersonalModel, isPersonalPrefix, readPersonalModels, deferPersonalModelDiscovery,
    toast: { error: text => notices.push(text) }, dismissMainOAuthAuthorizationRequired() {}, clearModelAuthorizationAttempt() {},
  });
  const setModels = compile('src/routes/(app)/+layout.svelte', '\tconst setModels = ', '\n\t// 后台授权', {
    getModels, localStorage: { token: 'fixture' }, $config: { features: { enable_direct_connections: true } }, $settings: { directConnections: state.connections },
    $user: { id: 'owner' }, destroyed: false, personalModelReadVersion: 0, mergePersonalModelDiscovery,
    models: { set: next => { values = next; }, update: fn => { values = fn(values); } }, toast: { error: text => notices.push(text) },
  });
  return { setModels, state, notices, models: () => values };
}

test('candidate startup returns platform models while personal discovery remains pending, then merges sources', async () => {
  let resolve; let calls = 0;
  const fixture = startup(() => { calls += 1; return new Promise(done => { resolve = done; }); });
  await fixture.setModels(new AbortController().signal);
  assert.equal(fixture.models()[0].id, 'platform'); assert.equal(calls, 0);
  await turn(); assert.equal(calls, 1);
  resolve({ data: [{ id: 'm' }] }); await turn();
  assert.equal(fixture.models().length, 2); assert.equal(fixture.models()[1].id, model.id);
  assert.equal(fixture.models()[1].direct, true, 'deferred models must retain native personal dispatch');
});

test('personal discovery failure is visible but never rejects candidate bootstrap or drops platform models', async () => {
  const fixture = startup(async () => { throw new Error('provider down fake-key'); });
  await fixture.setModels(new AbortController().signal); await turn(); await turn();
  assert.equal(fixture.models().length, 1); assert.equal(fixture.models()[0].id, 'platform');
  assert.equal(fixture.notices.length, 1); assert.ok(!fixture.notices[0].includes('fake-key'));
});

test('delayed personal discovery cannot reuse a previous account or changed Key', async () => {
  let resolve;
  const fixture = startup(() => new Promise(done => { resolve = done; }));
  await fixture.setModels(new AbortController().signal); await turn();
  fixture.state.connections.OPENAI_API_KEYS[0] = 'new-key';
  resolve({ data: [{ id: 'm' }] }); await turn();
  assert.equal(fixture.models().length, 1);
});
