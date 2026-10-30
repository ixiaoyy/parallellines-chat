import test from 'node:test';
import assert from 'node:assert/strict';
import { CHAT_BASE, MODEL_IDS, RELAY_BASE, configureModelAccess } from './configure-model-access.mjs';

const clone = value => structuredClone(value);
const grant = { principal_type: 'user', principal_id: '*', permission: 'read' };
function fixture() {
  const images = Object.fromEntries(['IMAGE_GENERATION_MODEL','IMAGE_SIZE','IMAGES_OPENAI_API_BASE_URL','IMAGES_OPENAI_API_KEY','IMAGES_OPENAI_API_VERSION','AUTOMATIC1111_BASE_URL','AUTOMATIC1111_API_AUTH','COMFYUI_BASE_URL','COMFYUI_API_KEY','COMFYUI_WORKFLOW','IMAGES_GEMINI_API_BASE_URL','IMAGES_GEMINI_API_KEY','IMAGES_GEMINI_ENDPOINT_METHOD','IMAGE_EDIT_ENGINE','IMAGE_EDIT_MODEL','IMAGE_EDIT_SIZE','IMAGES_EDIT_OPENAI_API_BASE_URL','IMAGES_EDIT_OPENAI_API_KEY','IMAGES_EDIT_OPENAI_API_VERSION','IMAGES_EDIT_GEMINI_API_BASE_URL','IMAGES_EDIT_GEMINI_API_KEY','IMAGES_EDIT_COMFYUI_BASE_URL','IMAGES_EDIT_COMFYUI_API_KEY','IMAGES_EDIT_COMFYUI_WORKFLOW'].map(key => [key, '']));
  Object.assign(images, { IMAGE_GENERATION_ENGINE: 'openai', IMAGE_SIZE: '512x512', IMAGE_STEPS: 50, ENABLE_IMAGE_GENERATION: false, ENABLE_IMAGE_EDIT: false, ENABLE_IMAGE_PROMPT_GENERATION: true, IMAGES_OPENAI_API_PARAMS: { quality: 'preserve' }, AUTOMATIC1111_PARAMS: {}, COMFYUI_WORKFLOW_NODES: [], IMAGES_EDIT_COMFYUI_WORKFLOW_NODES: [] });
  const tasks = Object.fromEntries(['TITLE_GENERATION_PROMPT_TEMPLATE','IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE','AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE','TAGS_GENERATION_PROMPT_TEMPLATE','FOLLOW_UP_GENERATION_PROMPT_TEMPLATE','QUERY_GENERATION_PROMPT_TEMPLATE','TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE','VOICE_MODE_PROMPT_TEMPLATE'].map(key => [key, `preserve-${key}`]));
  Object.assign(tasks, Object.fromEntries(['ENABLE_TITLE_GENERATION','ENABLE_AUTOCOMPLETE_GENERATION','ENABLE_FOLLOW_UP_GENERATION','ENABLE_TAGS_GENERATION','ENABLE_SEARCH_QUERY_GENERATION','ENABLE_RETRIEVAL_QUERY_GENERATION','ENABLE_VOICE_MODE_PROMPT'].map(key => [key, true])), { TASK_MODEL: 'preserve-local', TASK_MODEL_EXTERNAL: '', TASK_MODEL_PARAMS: { temperature: 0.2 }, AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH: 200 });
  return {
    configs: {
      '/openai/config': { ENABLE_OPENAI_API: false, OPENAI_API_BASE_URLS: ['https://api.openai.com/v1'], OPENAI_API_KEYS: [''], OPENAI_API_CONFIGS: {} },
      '/api/v1/configs/connections': { ENABLE_DIRECT_CONNECTIONS: false, ENABLE_DIRECT_INTEGRATIONS: true, ENABLE_BASE_MODELS_CACHE: true },
      '/api/v1/configs/models': { DEFAULT_MODELS: null, DEFAULT_PINNED_MODELS: 'preserve-pinned', MODEL_ORDER_LIST: ['preserve-order'], DEFAULT_MODEL_METADATA: { capabilities: { vision: true } }, DEFAULT_MODEL_PARAMS: { temperature: 0.3 } },
      '/api/v1/tasks/config': tasks,
      '/api/v1/images/config': images,
    },
    models: new Map(), calls: [],
  };
}

function fakeFetch(state, failAt) {
  const postToGet = { '/openai/config/update': '/openai/config', '/api/v1/configs/connections': '/api/v1/configs/connections', '/api/v1/configs/models': '/api/v1/configs/models', '/api/v1/tasks/config/update': '/api/v1/tasks/config', '/api/v1/images/config/update': '/api/v1/images/config' };
  return async (url, options) => {
    assert.equal(new URL(url).origin, CHAT_BASE);
    assert.equal(options.redirect, 'error');
    assert.equal(options.headers.Authorization, 'Bearer ephemeral-test-token');
    const path = url.slice(CHAT_BASE.length);
    state.calls.push({ method: options.method, path, body: options.body ? JSON.parse(options.body) : null });
    if (failAt === `${options.method} ${path}`) throw new Error('DO-NOT-LEAK ephemeral-test-token fullconfig');
    if (options.method === 'GET' && path.startsWith('/api/v1/models/model?id=')) {
      const id = new URL(url).searchParams.get('id');
      assert.ok(MODEL_IDS.includes(id));
      return state.models.has(id) ? Response.json(clone(state.models.get(id))) : Response.json({}, { status: 404 });
    }
    if (options.method === 'POST' && path === '/api/v1/models/model/access/update') {
      const body = JSON.parse(options.body);
      const record = state.models.get(body.id) ?? { id: body.id, base_model_id: null, is_active: true, name: body.name ?? body.id, params: {}, meta: {}, user_id: 'recovery-admin' };
      state.models.set(body.id, { ...record, access_grants: body.access_grants.map((g, index) => ({ ...g, id: `generated-${index}` })) });
      return Response.json({ ignored: 'full-response' });
    }
    if (options.method === 'GET' && Object.hasOwn(state.configs, path)) return Response.json(clone(state.configs[path]));
    if (options.method === 'POST' && Object.hasOwn(postToGet, path)) {
      state.configs[postToGet[path]] = JSON.parse(options.body);
      return Response.json({ ignored: 'full-config-response' });
    }
    assert.fail(`Unexpected endpoint ${options.method} ${path}`);
  };
}
const input = () => ({ adminBearer: 'ephemeral-test-token' });

test('preserves config and existing grants, enables only approved connection last, rerun writes nothing', async () => {
  const state = fixture();
  const privateGrant = { id: 'old-id', principal_type: 'group', principal_id: 'existing-group', permission: 'read' };
  state.models.set(MODEL_IDS[0], { id: MODEL_IDS[0], base_model_id: null, is_active: true, name: 'Preserved name', params: { temperature: 0.4 }, meta: { description: 'preserved' }, user_id: 'owner', access_grants: [privateGrant] });
  const before = clone(state.configs);
  const result = await configureModelAccess(input(), { fetchImpl: fakeFetch(state) });
  assert.equal(result.ok, true);
  assert.equal(JSON.stringify(result).includes('ephemeral-test-token'), false);
  assert.deepEqual(state.configs['/api/v1/configs/models'].DEFAULT_MODEL_PARAMS, before['/api/v1/configs/models'].DEFAULT_MODEL_PARAMS);
  assert.equal(state.configs['/api/v1/configs/connections'].ENABLE_DIRECT_INTEGRATIONS, true);
  assert.equal(state.configs['/api/v1/tasks/config'].TASK_MODEL, 'preserve-local');
  assert.equal(state.configs['/api/v1/tasks/config'].TASK_MODEL_EXTERNAL, 'gpt-6-luna');
  assert.deepEqual(state.configs['/api/v1/images/config'].IMAGES_OPENAI_API_PARAMS, { quality: 'preserve' });
  assert.equal(state.models.get(MODEL_IDS[0]).name, 'Preserved name');
  assert.equal(state.models.get(MODEL_IDS[0]).access_grants.length, 2);
  assert.deepEqual(state.configs['/openai/config'].OPENAI_API_BASE_URLS, [RELAY_BASE]);
  assert.equal(state.calls.filter(c => c.method === 'POST').at(-1).path, '/openai/config/update');
  state.calls.length = 0;
  const again = await configureModelAccess(input(), { fetchImpl: fakeFetch(state) });
  assert.ok(again.steps.every(step => !step.changed));
  assert.equal(state.calls.filter(c => c.method === 'POST').length, 0);
});

test('unsafe provider, global key and embedded Authorization are rejected before all writes', async () => {
  for (const patch of [
    { ENABLE_OPENAI_API: true },
    { OPENAI_API_BASE_URLS: ['https://unapproved.example/v1'] },
    { OPENAI_API_KEYS: ['never-output-this-key'] },
    { OPENAI_API_BASE_URLS: [RELAY_BASE], OPENAI_API_CONFIGS: { '0': { auth_type: 'system_oauth', headers: { Authorization: 'never-output-this-key' } } } },
  ]) {
    const state = fixture();
    Object.assign(state.configs['/openai/config'], patch);
    await assert.rejects(configureModelAccess(input(), { fetchImpl: fakeFetch(state) }), error => !JSON.stringify(error.receipt).includes('never-output') && error.receipt.partialOrUncertain === false);
    assert.equal(state.calls.filter(c => c.method === 'POST').length, 0);
  }
});

test('the observed disabled candidate URL is allowed only without credentials or connection config', async () => {
  const state = fixture();
  state.configs['/openai/config'].OPENAI_API_BASE_URLS = ['http://newapi:3000/v1'];
  assert.equal((await configureModelAccess(input(), { fetchImpl: fakeFetch(state) })).ok, true);
  for (const patch of [{ ENABLE_OPENAI_API: true }, { OPENAI_API_KEYS: ['private'] }, { OPENAI_API_CONFIGS: { '0': { enable: false } } }]) {
    const unsafe = fixture();
    Object.assign(unsafe.configs['/openai/config'], { OPENAI_API_BASE_URLS: ['http://newapi:3000/v1'] }, patch);
    await assert.rejects(configureModelAccess(input(), { fetchImpl: fakeFetch(unsafe) }));
    assert.equal(unsafe.calls.filter(c => c.method === 'POST').length, 0);
  }
});

test('unsupported image engine is skipped only when all three flags are disabled', async () => {
  const state = fixture();
  Object.assign(state.configs['/api/v1/images/config'], { IMAGE_GENERATION_ENGINE: 'automatic1111', ENABLE_IMAGE_PROMPT_GENERATION: false });
  await configureModelAccess(input(), { fetchImpl: fakeFetch(state) });
  assert.equal(state.calls.filter(c => c.method === 'POST' && c.path.includes('/images/')).length, 0);
  const unsafe = fixture();
  unsafe.configs['/api/v1/images/config'].IMAGE_GENERATION_ENGINE = 'automatic1111';
  await assert.rejects(configureModelAccess(input(), { fetchImpl: fakeFetch(unsafe) }), error => error.message === 'images.unsupported-engine');
  assert.equal(unsafe.calls.filter(c => c.method === 'POST').length, 0);
});

test('no provider discovery, verification, inference or arbitrary destination calls', async () => {
  const state = fixture();
  await configureModelAccess(input(), { fetchImpl: fakeFetch(state) });
  assert.ok(state.calls.every(c => !/verify|completions|responses|generations|\/api\/models(?:\?|$)|\/openai\/models/.test(c.path)));
  let called = false;
  await assert.rejects(configureModelAccess({ ...input(), baseURL: 'https://evil.example' }, { fetchImpl: async () => { called = true; } }));
  assert.equal(called, false);
});

test('partial failure reports named step without response or token and safe rerun completes', async () => {
  const state = fixture();
  await assert.rejects(configureModelAccess(input(), { fetchImpl: fakeFetch(state, 'POST /api/v1/tasks/config/update') }), error => {
    assert.equal(error.receipt.failedStep, 'tasks.write');
    assert.equal(error.receipt.partialOrUncertain, true);
    assert.equal(JSON.stringify(error.receipt).includes('ephemeral-test-token'), false);
    return true;
  });
  assert.equal(state.configs['/openai/config'].ENABLE_OPENAI_API, false);
  state.calls.length = 0;
  await configureModelAccess(input(), { fetchImpl: fakeFetch(state) });
  assert.equal(state.calls.filter(c => c.method === 'POST' && c.path === '/api/v1/configs/connections').length, 0);
});

test('all model guards complete before a write, and existing public grants skip mutations', async () => {
  const state = fixture();
  state.models.set(MODEL_IDS[2], { id: MODEL_IDS[2], base_model_id: null, is_active: true, access_grants: [{ principal_type: 'anyone', principal_id: '*', permission: 'read' }] });
  await assert.rejects(configureModelAccess(input(), { fetchImpl: fakeFetch(state) }));
  assert.equal(state.calls.filter(c => c.method === 'POST').length, 0);
  const safe = fixture();
  for (const id of MODEL_IDS) safe.models.set(id, { id, base_model_id: null, is_active: true, access_grants: [{ ...grant, id: 'server-generated' }] });
  await configureModelAccess(input(), { fetchImpl: fakeFetch(safe) });
  assert.equal(safe.calls.filter(c => c.method === 'POST' && c.path.includes('/model/access/')).length, 0);
});
