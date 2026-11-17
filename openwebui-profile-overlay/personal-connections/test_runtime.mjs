import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isPersonalPrefix, isPersonalModel, decoratePersonalModel, boundedPersonalRequest,
  readPersonalModels, resolvePersonalConnection, cancelPersonalRequests,
  revalidatePersonalRequests, handlePersonalCompletion
} from './personal-connections-runtime.mjs';

const ID = `pl-personal-${'a'.repeat(32)}`;
const OTHER_ID = `pl-personal-${'b'.repeat(32)}`;
const OWNER = 'synthetic-owner';
const URL = 'https://api.example.invalid/v1';
const KEY = 'sk-synthetic-private-key-not-for-output';
const answer = () => ({ choices: [{ message: { content: 'synthetic answer' } }] });
const turn = () => new Promise((resolve) => setImmediate(resolve));

function fixture() {
  const config = { prefix_id: ID, name: '我的服务商', enable: true, model_ids: [] };
  const connections = { OPENAI_API_BASE_URLS: [URL], OPENAI_API_KEYS: [KEY], OPENAI_API_CONFIGS: { 0: config } };
  const model = decoratePersonalModel({ id: `${ID}.example-model`, name: 'example-model', openai: { id: 'example-model' } }, config, URL, OWNER);
  return { connections, model, body: { model: model.id, messages: [{ role: 'user', content: 'synthetic' }], stream: false } };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function jsonResponse(value = answer(), json) {
  return { ok: true, status: 200, headers: new Headers({ 'Content-Type': 'application/json' }), json: json ?? (async () => value) };
}

function streamed(chunks, error) {
  const bytes = chunks.map((chunk) => typeof chunk === 'string' ? new TextEncoder().encode(chunk) : chunk);
  let index = 0;
  return { ok: true, status: 200, headers: new Headers({ 'Content-Type': 'text/event-stream' }),
    body: new ReadableStream({ pull(controller) {
      if (index < bytes.length) controller.enqueue(bytes[index++]);
      else if (error) controller.error(error);
      else controller.close();
    } }) };
}

function harness(f, chatCompletion) {
  const acks = [], emitted = [], calls = [];
  let owner = OWNER;
  const run = () => handlePersonalCompletion({ data: { model: f.model, form_data: f.body, channel: 'synthetic-channel' },
    connections: f.connections, ownerId: OWNER, getOwnerId: () => owner,
    socket: { emit(channel, value) { assert.equal(channel, 'synthetic-channel'); emitted.push(value); } },
    cb(value) { acks.push(value); },
    chatCompletion(...args) { calls.push(args); return chatCompletion(...args); }
  });
  return { run, acks, emitted, calls, changeOwner(value) { owner = value; } };
}

// 复现原生 generate_direct_chat_completion 对字符串/对象的 SSE 包装，检查真实下游能解析的数据。
function downstreamFrames(emitted) {
  const wire = emitted.filter((value) => typeof value === 'string').map((value) =>
    (value.includes('data:') ? value : `data: ${value}`) + '\n\n').join('');
  return wire.split(/\r?\n\r?\n/).map((frame) => frame.split(/\r?\n/)
    .filter((line) => line.startsWith('data:')).map((line) => line.slice(5).replace(/^ /, '')).join('\n')).filter(Boolean);
}

function errorOutputs(h) {
  return [...h.acks, ...h.emitted].filter((value) => value && typeof value === 'object' && value.error);
}

function assertTerminated(h) {
  assert.equal(h.acks.length, 1, 'socket callback must acknowledge exactly once');
  assert.equal(h.emitted.filter((value) => value?.done === true).length, 1, 'the original backend queue needs one terminal signal');
  assert.equal(JSON.stringify([...h.acks, ...h.emitted]).includes(KEY), false, 'errors must not return the configured Key');
}

async function withTimers(run) {
  const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
  const timers = new Map();
  let id = 0;
  globalThis.setTimeout = (fn, milliseconds) => { const handle = ++id; timers.set(handle, { fn, milliseconds }); return handle; };
  globalThis.clearTimeout = (handle) => timers.delete(handle);
  try { await run(timers); }
  finally { globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear; cancelPersonalRequests(); }
}

test('namespace matches backend reservation exactly, with a nonempty model suffix', () => {
  assert.equal(isPersonalPrefix(ID), true);
  assert.equal(isPersonalModel(`${ID}.model.with.dots`), true);
  for (const value of [null, '', ID, `${ID}.`, `pl-personal-${'A'.repeat(32)}.model`, `pl-personal-${'a'.repeat(31)}.model`, `${ID}x.model`]) {
    assert.equal(isPersonalModel(value), false, String(value));
  }
  assert.equal(isPersonalPrefix(`${ID}.model`), false);
});

test('model decoration records only nonsecret provenance, leaving ordinary models unchanged', () => {
  const f = fixture();
  assert.deepEqual(f.model.personal_connection, { id: ID, name: '我的服务商', origin: URL, owner_id: OWNER });
  assert.equal(f.model.openai.id, 'example-model');
  assert.equal(JSON.stringify(f.model).includes(KEY), false);
  const ordinary = { id: 'platform-model' };
  assert.equal(decoratePersonalModel(ordinary, { prefix_id: 'ordinary' }, URL, OWNER), ordinary);
});

test('connection resolution uses stable ID after array reorder and uses the current Key', () => {
  const f = fixture();
  f.connections = { OPENAI_API_BASE_URLS: ['https://other.example.invalid/v1', URL],
    OPENAI_API_KEYS: ['other-key', 'new-key'], OPENAI_API_CONFIGS: { 0: { prefix_id: OTHER_ID }, 1: f.connections.OPENAI_API_CONFIGS[0] } };
  assert.deepEqual(resolvePersonalConnection(f.model, f.body, f.connections, OWNER), { url: URL, key: 'new-key', model: 'example-model' });
});

test('deleted, disabled, changed-origin, different-owner, removed-model, and mismatched-body connections fail closed', () => {
  const f = fixture();
  const cases = [
    () => resolvePersonalConnection(f.model, f.body, { ...f.connections, OPENAI_API_CONFIGS: {} }, OWNER),
    () => resolvePersonalConnection(f.model, f.body, { ...f.connections, OPENAI_API_CONFIGS: { 0: { ...f.connections.OPENAI_API_CONFIGS[0], enable: false } } }, OWNER),
    () => resolvePersonalConnection(f.model, f.body, { ...f.connections, OPENAI_API_BASE_URLS: ['https://changed.example.invalid/v1'] }, OWNER),
    () => resolvePersonalConnection(f.model, f.body, f.connections, 'other-owner'),
    () => resolvePersonalConnection(f.model, f.body, { ...f.connections, OPENAI_API_CONFIGS: { 0: { ...f.connections.OPENAI_API_CONFIGS[0], model_ids: ['another-model'] } } }, OWNER),
    () => resolvePersonalConnection(f.model, { ...f.body, model: 'platform-model' }, f.connections, OWNER),
    () => resolvePersonalConnection(f.model, f.body, { ...f.connections, OPENAI_API_KEYS: [''] }, OWNER)
  ];
  for (const execute of cases) assert.throws(execute);
});

test('unsafe URL credentials, query, fragment and non-HTTPS origins are rejected', () => {
  for (const url of ['http://api.example.invalid/v1', 'https://u:p@api.example.invalid/v1', 'https://api.example.invalid/v1?key=x', 'https://api.example.invalid/v1#x']) {
    const f = fixture();
    f.model.personal_connection.origin = url;
    f.connections.OPENAI_API_BASE_URLS[0] = url;
    assert.throws(() => resolvePersonalConnection(f.model, f.body, f.connections, OWNER));
  }
});

test('model discovery forwards cancellation, closes timers, and never returns upstream Key errors', async () => {
  const f = fixture();
  const result = [{ id: 'example-model' }];
  let received;
  assert.equal(await readPersonalModels(async (url, key, signal) => { received = { url, key, signal }; return result; }, URL, KEY), result);
  assert.equal(received.url, URL); assert.equal(received.key, KEY); assert.equal(received.signal.aborted, false);
  await assert.rejects(readPersonalModels(async () => { throw new Error(`upstream rejected ${KEY}`); }, URL, KEY), (error) => !error.message.includes(KEY));
  const parent = new AbortController();
  const reason = new Error('caller cancelled'); parent.abort(reason);
  const request = boundedPersonalRequest(parent.signal, 10);
  assert.equal(request.signal.reason, reason); request.close();
});

test('model discovery timeout is bounded and aborts the native list request', async () => {
  await withTimers(async (timers) => {
    let signal;
    const pending = readPersonalModels((url, key, value) => { signal = value; return new Promise((resolve, reject) => value.addEventListener('abort', () => reject(value.reason), { once: true })); }, URL, KEY);
    const timer = [...timers.values()].find((value) => value.milliseconds <= 15000);
    assert.ok(timer); timer.fn();
    await assert.rejects(pending);
    assert.equal(signal.aborted, true);
    assert.equal(timers.size, 0);
  });
});

test('nonstream completion uses only the selected personal origin and stripped model ID', async () => {
  const f = fixture();
  const h = harness(f, async () => [jsonResponse()]);
  await h.run();
  assertTerminated(h);
  assert.deepEqual(h.acks[0], answer());
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], KEY); assert.equal(h.calls[0][1].model, 'example-model'); assert.equal(h.calls[0][2], URL);
  assert.ok(h.calls[0][3] instanceof AbortSignal);
});

test('invalid personal connection never invokes transport or falls back to a platform request', async () => {
  const f = fixture(); f.connections.OPENAI_API_CONFIGS = {};
  const h = harness(f, async () => { throw new Error('must not send'); });
  await h.run();
  assertTerminated(h); assert.equal(h.calls.length, 0); assert.equal(errorOutputs(h).length, 1);
});

test('HTTP failures and transport errors are sanitized and acknowledged once without fallback', async () => {
  for (const status of [401, 402, 403, 429, 500]) {
    const f = fixture();
    const h = harness(f, async () => [{ ok: false, status, json: async () => ({ error: { message: KEY } }) }]);
    await h.run(); assertTerminated(h); assert.equal(errorOutputs(h).length, 1); assert.equal(h.calls.length, 1);
  }
  const h = harness(fixture(), async () => { throw new Error(`network included ${KEY}`); });
  await h.run(); assertTerminated(h); assert.equal(errorOutputs(h).length, 1); assert.equal(h.calls.length, 1);
});

test('HTTP-200 error envelopes and invalid JSON are not returned as successful provider replies', async () => {
  for (const response of [jsonResponse({ error: { message: KEY } }), jsonResponse(undefined, async () => { throw new Error(`parse included ${KEY}`); })]) {
    const h = harness(fixture(), async () => [response]);
    await h.run(); assertTerminated(h); assert.equal(errorOutputs(h).length, 1); assert.equal(h.acks[0].choices, undefined);
  }
});

test('fragmented UTF-8, CRLF and multiline SSE data survive the actual native downstream framing', async () => {
  const f = fixture(); f.body.stream = true;
  const first = { choices: [{ delta: { content: '中文' } }] };
  const second = { choices: [{ delta: { content: ' next' }, finish_reason: 'stop' }] };
  const wire = `: comment\r\nevent: message\r\ndata: ${JSON.stringify(first)}\r\n\r\ndata: {"choices":\n` +
    `data: ${JSON.stringify(second.choices)}}\n\ndata: [DONE]\n\n`;
  const bytes = new TextEncoder().encode(wire);
  const chunks = [];
  for (let index = 0; index < bytes.length; index += 3) chunks.push(bytes.slice(index, index + 3));
  const h = harness(f, async () => [streamed(chunks)]);
  await h.run(); assertTerminated(h); assert.deepEqual(h.acks[0], { status: true });
  assert.deepEqual(downstreamFrames(h.emitted).filter((data) => data !== '[DONE]').map((data) => JSON.parse(data)), [first, second]);
  assert.equal(errorOutputs(h).length, 0);
});

test('truncated SSE frame, unfinished stream, and protocol-invalid data fail after one stream acknowledgement', async () => {
  for (const wire of ['data: {"choices":[', 'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n', 'data: definitely-not-json\n\ndata: [DONE]\n\n']) {
    const f = fixture(); f.body.stream = true;
    const h = harness(f, async () => [streamed([wire])]);
    await h.run(); assertTerminated(h); assert.deepEqual(h.acks[0], { status: true });
    assert.equal(errorOutputs(h).length, 1);
  }
});

test('SSE error envelopes and a midstream network error are sanitized and terminate the old queue', async () => {
  for (const response of [streamed([`data: {"error":{"message":"${KEY}"}}\n\n`]),
    streamed(['data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'], new Error(`reader included ${KEY}`))]) {
    const f = fixture(); f.body.stream = true;
    const h = harness(f, async () => [response]);
    await h.run(); assertTerminated(h); assert.deepEqual(h.acks[0], { status: true }); assert.equal(errorOutputs(h).length, 1);
  }
});

test('request revalidation survives array reorder but aborts current Key changes', async () => {
  const f = fixture(); const ready = deferred(); let signal;
  const h = harness(f, async (key, body, url, current) => { signal = current; return ready.promise; });
  const pending = h.run(); await turn();
  const reordered = { OPENAI_API_BASE_URLS: ['https://other.example.invalid/v1', URL], OPENAI_API_KEYS: ['other', KEY],
    OPENAI_API_CONFIGS: { 0: { prefix_id: OTHER_ID }, 1: f.connections.OPENAI_API_CONFIGS[0] } };
  revalidatePersonalRequests(reordered, OWNER); assert.equal(signal.aborted, false);
  revalidatePersonalRequests({ ...reordered, OPENAI_API_KEYS: ['other', 'new-key'] }, OWNER);
  assert.equal(signal.aborted, true);
  ready.resolve([jsonResponse()]); await pending;
  assertTerminated(h); assert.equal(h.acks[0].choices, undefined); assert.equal(errorOutputs(h).length, 1);
});

test('late nonstream JSON cannot acknowledge success after owner change, deletion, disablement, or origin change', async () => {
  for (const change of ['owner', 'delete', 'disable', 'origin']) {
    const f = fixture(); const parsed = deferred(); let signal;
    const h = harness(f, async (key, body, url, current) => { signal = current; return [jsonResponse(undefined, () => parsed.promise)]; });
    const pending = h.run(); await turn();
    if (change === 'owner') { h.changeOwner('new-owner'); cancelPersonalRequests(); }
    else if (change === 'delete') revalidatePersonalRequests({ ...f.connections, OPENAI_API_CONFIGS: {} }, OWNER);
    else if (change === 'disable') revalidatePersonalRequests({ ...f.connections, OPENAI_API_CONFIGS: { 0: { ...f.connections.OPENAI_API_CONFIGS[0], enable: false } } }, OWNER);
    else revalidatePersonalRequests({ ...f.connections, OPENAI_API_BASE_URLS: ['https://changed.example.invalid/v1'] }, OWNER);
    assert.equal(signal.aborted, true);
    parsed.resolve(answer()); await pending;
    assertTerminated(h); assert.equal(h.acks[0].choices, undefined); assert.equal(errorOutputs(h).length, 1);
  }
});

test('cancelling an acknowledged stream after owner change sends no more content but still terminates its queue', async () => {
  const f = fixture(); f.body.stream = true; let streamController;
  const h = harness(f, async (key, body, url, signal) => [{ ok: true, status: 200, headers: new Headers({ 'Content-Type': 'text/event-stream' }),
    body: new ReadableStream({ start(controller) { streamController = controller; signal.addEventListener('abort', () => controller.error(signal.reason), { once: true }); } }) }]);
  const pending = h.run(); await turn();
  assert.deepEqual(h.acks, [{ status: true }]);
  streamController.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"before"}}]}\n\n'));
  await turn(); const before = h.emitted.filter((value) => typeof value === 'string').length;
  h.changeOwner('new-owner'); cancelPersonalRequests(); await pending;
  assertTerminated(h);
  assert.equal(h.emitted.filter((value) => typeof value === 'string').length, before);
});

test('connection wait and total response timeouts abort rather than retry or use platform balance', async () => {
  for (const stage of ['connection', 'body']) await withTimers(async (timers) => {
    let signal;
    const f = fixture();
    const h = harness(f, async (key, body, url, current) => {
      signal = current;
      const wait = () => new Promise((resolve, reject) => current.addEventListener('abort', () => reject(new Error(`abort ${KEY}`)), { once: true }));
      return stage === 'connection' ? wait() : [jsonResponse(undefined, wait)];
    });
    const pending = h.run(); await turn();
    const scheduled = [...timers.values()]; assert.ok(scheduled.length > 0);
    const timer = stage === 'connection' ? scheduled.find((value) => value.milliseconds <= 30000) : scheduled.find((value) => value.milliseconds <= 600000);
    assert.ok(timer); timer.fn(); await pending;
    assert.equal(signal.aborted, true); assertTerminated(h); assert.equal(errorOutputs(h).length, 1); assert.equal(h.calls.length, 1);
    assert.equal(timers.size, 0);
  });
});
