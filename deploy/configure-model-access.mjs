import { isDeepStrictEqual } from 'node:util';
import { pathToFileURL } from 'node:url';

// OWUI v0.12.5. CLI input: JSON on stdin with adminBearer, optional baseURL/mode.
// Invoke only after the relay adapter is ready. No credentials in argv or env.
// This helper does not change OIDC scopes, user roles, balances or provider secrets.
export const CHAT_BASE = 'https://chat.pingxingxian.space';
export const RELAY_BASE = 'https://api.pingxingxian.space/oauth-relay/v1';
export const MODEL_IDS = Object.freeze(['gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol']);
const READ_GRANT = Object.freeze({ principal_type: 'user', principal_id: '*', permission: 'read' });
const IMAGE_FLAGS = ['ENABLE_IMAGE_GENERATION', 'ENABLE_IMAGE_EDIT', 'ENABLE_IMAGE_PROMPT_GENERATION'];
const CONFIGS = [
  { name: 'connections', get: '/api/v1/configs/connections', post: '/api/v1/configs/connections', required: ['ENABLE_DIRECT_CONNECTIONS', 'ENABLE_BASE_MODELS_CACHE'], patch: { ENABLE_BASE_MODELS_CACHE: false } },
  { name: 'defaults', get: '/api/v1/configs/models', post: '/api/v1/configs/models', required: ['DEFAULT_MODELS', 'DEFAULT_PINNED_MODELS', 'MODEL_ORDER_LIST', 'DEFAULT_MODEL_METADATA', 'DEFAULT_MODEL_PARAMS'], patch: { DEFAULT_MODELS: 'gpt-6-luna' } },
  { name: 'tasks', get: '/api/v1/tasks/config', post: '/api/v1/tasks/config/update', required: ['TASK_MODEL', 'TASK_MODEL_EXTERNAL', 'ENABLE_TITLE_GENERATION', 'TITLE_GENERATION_PROMPT_TEMPLATE', 'IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE', 'ENABLE_AUTOCOMPLETE_GENERATION', 'AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE', 'TAGS_GENERATION_PROMPT_TEMPLATE', 'FOLLOW_UP_GENERATION_PROMPT_TEMPLATE', 'ENABLE_FOLLOW_UP_GENERATION', 'ENABLE_TAGS_GENERATION', 'ENABLE_SEARCH_QUERY_GENERATION', 'ENABLE_RETRIEVAL_QUERY_GENERATION', 'QUERY_GENERATION_PROMPT_TEMPLATE', 'TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE', 'ENABLE_VOICE_MODE_PROMPT', 'VOICE_MODE_PROMPT_TEMPLATE'], patch: { TASK_MODEL_EXTERNAL: 'gpt-6-luna' } },
  { name: 'images', get: '/api/v1/images/config', post: '/api/v1/images/config/update', required: ['ENABLE_IMAGE_GENERATION', 'ENABLE_IMAGE_PROMPT_GENERATION', 'IMAGE_GENERATION_ENGINE', 'IMAGE_GENERATION_MODEL', 'IMAGE_SIZE', 'IMAGE_STEPS', 'IMAGES_OPENAI_API_BASE_URL', 'IMAGES_OPENAI_API_KEY', 'IMAGES_OPENAI_API_VERSION', 'IMAGES_OPENAI_API_PARAMS', 'AUTOMATIC1111_BASE_URL', 'AUTOMATIC1111_API_AUTH', 'AUTOMATIC1111_PARAMS', 'COMFYUI_BASE_URL', 'COMFYUI_API_KEY', 'COMFYUI_WORKFLOW', 'COMFYUI_WORKFLOW_NODES', 'IMAGES_GEMINI_API_BASE_URL', 'IMAGES_GEMINI_API_KEY', 'IMAGES_GEMINI_ENDPOINT_METHOD', 'ENABLE_IMAGE_EDIT', 'IMAGE_EDIT_ENGINE', 'IMAGE_EDIT_MODEL', 'IMAGE_EDIT_SIZE', 'IMAGES_EDIT_OPENAI_API_BASE_URL', 'IMAGES_EDIT_OPENAI_API_KEY', 'IMAGES_EDIT_OPENAI_API_VERSION', 'IMAGES_EDIT_GEMINI_API_BASE_URL', 'IMAGES_EDIT_GEMINI_API_KEY', 'IMAGES_EDIT_COMFYUI_BASE_URL', 'IMAGES_EDIT_COMFYUI_API_KEY', 'IMAGES_EDIT_COMFYUI_WORKFLOW', 'IMAGES_EDIT_COMFYUI_WORKFLOW_NODES'], patch: Object.fromEntries(IMAGE_FLAGS.map(key => [key, false])) },
];
const OPENAI = { name: 'openai', get: '/openai/config', post: '/openai/config/update' };
const MODEL_POST = '/api/v1/models/model/access/update';
const modelPath = id => `/api/v1/models/model?id=${encodeURIComponent(id)}`;
const ALLOWED = new Set([
  ...[...CONFIGS, OPENAI].flatMap(c => [`GET ${c.get}`, `POST ${c.post}`]),
  ...MODEL_IDS.map(id => `GET ${modelPath(id)}`),
  `POST ${MODEL_POST}`,
]);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const empty = value => value == null || value === '' || (typeof value === 'string' && value.trim() === '');
const publicRead = grant => grant?.principal_type === 'user' && grant.principal_id === '*' && grant.permission === 'read';
const unsafeGrant = grant => grant?.principal_type === 'anyone' || (grant?.principal_type === 'user' && grant.principal_id === '*' && grant.permission !== 'read');

class SafeFailure extends Error {
  constructor(step, completed, writeAttempted) {
    super(step);
    this.receipt = { ok: false, failedStep: step, completedSteps: [...completed], partialOrUncertain: writeAttempted, noModelCalls: true };
  }
}

function containsCredential(value) {
  if (!object(value) && !Array.isArray(value)) return false;
  return Object.entries(value).some(([key, item]) => {
    const credentialField = key !== 'forward_cookies' && (/(^|[_-])(key|token|secret|password|authorization|cookie)(s)?$/i.test(key) || /^(authorization|proxy-authorization|x-api-key)$/i.test(key));
    return (credentialField && !empty(item) && !(Array.isArray(item) && item.every(empty))) || containsCredential(item);
  });
}

/** 检查原生连接结构与批准目标；返回连接定位信息，失败由调用方 check 安全终止。 */
function inspectOpenAI(current, check) {
  check(object(current), 'openai.schema');
  const urls = current.OPENAI_API_BASE_URLS;
  const keys = current.OPENAI_API_KEYS;
  const configs = current.OPENAI_API_CONFIGS;
  check(typeof current.ENABLE_OPENAI_API === 'boolean' && Array.isArray(urls) && Array.isArray(keys) && object(configs), 'openai.schema');
  check(keys.every(key => typeof key === 'string' && empty(key)), 'openai.nonempty-global-key');
  check(urls.length <= 1 && urls.every(url => typeof url === 'string'), 'openai.unapproved-provider');
  check(!containsCredential(configs), 'openai.embedded-credential');
  const url = urls[0];
  const config = configs['0'] ?? configs[url] ?? {};
  check(object(config), 'openai.schema');
  check(Object.keys(configs).length <= 1 && Object.keys(configs).every(key => key === '0' || key === url), 'openai.unapproved-provider');
  const isTarget = url === RELAY_BASE;
  const emptyInitial = current.ENABLE_OPENAI_API === false && (urls.length === 0 || url === '' || url === 'https://api.openai.com/v1' || url === 'http://newapi:3000/v1') && Object.keys(config).length === 0;
  check(isTarget || emptyInitial, 'openai.unapproved-provider');
  if (isTarget) {
    check(config.auth_type === undefined || config.auth_type === 'system_oauth', 'openai.unapproved-auth');
    check(!config.prefix_id && !config.azure && config.provider !== 'azure', 'openai.unapproved-provider-options');
    check(config.headers === undefined || object(config.headers), 'openai.schema');
  }
  return { url, config, isTarget };
}

function desiredOpenAI(current, check) {
  const { config, isTarget } = inspectOpenAI(current, check);
  return {
    ...current,
    ENABLE_OPENAI_API: true,
    OPENAI_API_BASE_URLS: [RELAY_BASE],
    OPENAI_API_KEYS: [''],
    OPENAI_API_CONFIGS: {
      '0': { ...(isTarget ? config : {}), enable: true, auth_type: 'system_oauth', api_type: 'responses', connection_type: 'external', forward_cookies: false, headers: isTarget ? (config.headers ?? {}) : {}, model_ids: [] },
    },
  };
}

/**
 * 配置原生模型接入；input 通过 adminBearer 认证，mode=responses-only 仅修正已启用的批准连接。
 * 返回无凭据回执，不调用模型；调用方须事先确认适配器就绪，未指定 mode 保持原全量配置流程。
 */
export async function configureModelAccess(input, { fetchImpl = globalThis.fetch } = {}) {
  const completed = [];
  let writeAttempted = false;
  const check = (condition, step) => { if (!condition) throw new SafeFailure(step, completed, writeAttempted); };
  check(object(input), 'input');
  check(input.mode === undefined || input.mode === 'responses-only', 'input.mode');
  check(typeof input.adminBearer === 'string' && input.adminBearer.length > 0 && input.adminBearer.length <= 16384 && !/\s/.test(input.adminBearer), 'input');
  const base = input.baseURL === undefined ? CHAT_BASE : input.baseURL;
  check(typeof base === 'string' && (base === CHAT_BASE || base === `${CHAT_BASE}/`), 'input.base-url');
  check(typeof fetchImpl === 'function', 'input.fetch');
  const steps = [];
  const allowed = input.mode === 'responses-only' ? new Set([`GET ${OPENAI.get}`, `POST ${OPENAI.post}`]) : ALLOWED;

  async function request(method, path, step, body, allow404 = false) {
    check(allowed.has(`${method} ${path}`), 'endpoint');
    try {
      const response = await fetchImpl(`${CHAT_BASE}${path}`, {
        method,
        headers: { Authorization: `Bearer ${input.adminBearer}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(30000),
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (allow404 && response.status === 404) { await response.body?.cancel(); return null; }
      check(response.ok, step);
      if (method === 'POST') { await response.body?.cancel(); return null; }
      return await response.json();
    } catch {
      throw new SafeFailure(step, completed, writeAttempted);
    }
  }

  // 窄模式只处理已存在的连接，完成后立即返回，不读取或修改其他模型设置。
  const currentOpenAI = await request('GET', OPENAI.get, 'openai.read');
  if (input.mode === 'responses-only') {
    const { url, config, isTarget } = inspectOpenAI(currentOpenAI, check);
    check(isTarget && currentOpenAI.ENABLE_OPENAI_API === true && config.enable === true, 'openai.connection-not-enabled');
    check(config.auth_type === 'system_oauth', 'openai.unapproved-auth');
    check(config.forward_cookies === false, 'openai.cookie-forwarding');
    const configKey = Object.hasOwn(currentOpenAI.OPENAI_API_CONFIGS, '0') ? '0' : url;
    // 三个批准模型共用此连接；仅切换协议，保留连接索引、身份、权限及所有其他参数。
    const nextOpenAI = { ...currentOpenAI, OPENAI_API_CONFIGS: { ...currentOpenAI.OPENAI_API_CONFIGS, [configKey]: { ...config, api_type: 'responses' } } };
    const changed = !isDeepStrictEqual(currentOpenAI, nextOpenAI);
    if (changed) {
      writeAttempted = true;
      await request('POST', OPENAI.post, 'openai.write', nextOpenAI);
      const readback = await request('GET', OPENAI.get, 'openai.readback');
      check(isDeepStrictEqual(readback, nextOpenAI), 'openai.verify');
      completed.push('openai');
    }
    return { ok: true, mode: 'responses-only', changed, apiType: 'responses', models: [...MODEL_IDS], systemOAuth: true, sharedKeyConfigured: false, noModelCalls: true, noFeesExpected: true };
  }
  const nextOpenAI = desiredOpenAI(currentOpenAI, check);
  const configPlans = [];
  for (const spec of CONFIGS) {
    const current = await request('GET', spec.get, `${spec.name}.read`);
    check(object(current) && spec.required.every(key => Object.hasOwn(current, key)), `${spec.name}.schema`);
    if (spec.name === 'images' && IMAGE_FLAGS.every(key => current[key] === false)) {
      configPlans.push({ ...spec, current, next: current });
      continue;
    }
    if (spec.name === 'images') check(current.IMAGE_GENERATION_ENGINE === 'openai', 'images.unsupported-engine');
    configPlans.push({ ...spec, current, next: { ...current, ...spec.patch } });
  }
  const modelPlans = [];
  for (const id of MODEL_IDS) {
    const current = await request('GET', modelPath(id), `model.${id}.read`, undefined, true);
    if (current !== null) {
      check(object(current) && current.id === id && current.base_model_id === null && current.is_active === true && Array.isArray(current.access_grants), `model.${id}.schema`);
      check(current.access_grants.every(grant => object(grant) && ['user', 'group'].includes(grant.principal_type) && typeof grant.principal_id === 'string' && ['read', 'write'].includes(grant.permission)), `model.${id}.grant-schema`);
      check(!current.access_grants.some(unsafeGrant), `model.${id}.unsafe-grant`);
    }
    modelPlans.push({ id, current, grants: current?.access_grants ?? [] });
  }

  for (const plan of configPlans) {
    if (isDeepStrictEqual(plan.current, plan.next)) { steps.push({ step: plan.name, changed: false }); continue; }
    writeAttempted = true;
    await request('POST', plan.post, `${plan.name}.write`, plan.next);
    const readback = await request('GET', plan.get, `${plan.name}.readback`);
    check(object(readback) && Object.entries(plan.patch).every(([key, value]) => isDeepStrictEqual(readback[key], value)), `${plan.name}.verify`);
    completed.push(plan.name);
    steps.push({ step: plan.name, changed: true });
  }
  for (const { id, current, grants } of modelPlans) {
    if (grants.some(publicRead)) { steps.push({ step: `model.${id}`, changed: false }); continue; }
    const nextGrants = [...grants, { ...READ_GRANT }];
    writeAttempted = true;
    await request('POST', MODEL_POST, `model.${id}.write`, { id, ...(current ? {} : { name: id }), access_grants: nextGrants });
    const readback = await request('GET', modelPath(id), `model.${id}.readback`);
    check(object(readback) && readback.id === id && readback.base_model_id === null && readback.is_active === true && Array.isArray(readback.access_grants) && readback.access_grants.some(publicRead) && !readback.access_grants.some(unsafeGrant), `model.${id}.verify`);
    check(grants.every(before => readback.access_grants.some(after => before.principal_type === after.principal_type && before.principal_id === after.principal_id && before.permission === after.permission)), `model.${id}.preserve-grants`);
    if (current) check(['name', 'params', 'meta', 'user_id', 'is_active', 'base_model_id'].every(key => isDeepStrictEqual(current[key], readback[key])), `model.${id}.preserve-fields`);
    completed.push(`model.${id}`);
    steps.push({ step: `model.${id}`, changed: true });
  }
  // Enable the sole upstream connection last, after ordinary-user model access is ready.
  if (!isDeepStrictEqual(currentOpenAI, nextOpenAI)) {
    writeAttempted = true;
    await request('POST', OPENAI.post, 'openai.write', nextOpenAI);
    const readback = await request('GET', OPENAI.get, 'openai.readback');
    check(object(readback) && ['ENABLE_OPENAI_API', 'OPENAI_API_BASE_URLS', 'OPENAI_API_KEYS', 'OPENAI_API_CONFIGS'].every(key => isDeepStrictEqual(readback[key], nextOpenAI[key])), 'openai.verify');
    completed.push('openai');
    steps.push({ step: 'openai', changed: true });
  } else steps.push({ step: 'openai', changed: false });
  return { ok: true, steps, models: [...MODEL_IDS], systemOAuth: true, sharedKeyConfigured: false, globalModelDirectoryCache: false, dedicatedImagesDisabled: true, noModelCalls: true, noFeesExpected: true };
}

async function main() {
  let input;
  try {
    let raw = '';
    for await (const chunk of process.stdin) {
      raw += chunk;
      if (raw.length > 32768) throw new Error('input');
    }
    input = JSON.parse(raw);
  } catch {
    process.stderr.write(`${JSON.stringify({ ok: false, failedStep: 'input', partialOrUncertain: false, noModelCalls: true })}\n`);
    process.exitCode = 1;
    return;
  }
  try {
    process.stdout.write(`${JSON.stringify(await configureModelAccess(input))}\n`);
  } catch (error) {
    process.stderr.write(`${JSON.stringify(error instanceof SafeFailure ? error.receipt : { ok: false, failedStep: 'internal', partialOrUncertain: true, noModelCalls: true })}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
