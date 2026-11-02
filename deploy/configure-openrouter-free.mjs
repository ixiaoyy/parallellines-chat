import { isDeepStrictEqual } from 'node:util';
import { pathToFileURL } from 'node:url';
import { writeFile } from 'node:fs/promises';

export const CHAT_BASE = 'https://chat.pingxingxian.space';
export const RELAY_BASE = 'https://api.pingxingxian.space/oauth-relay/v1';
export const FREE_MODEL = 'openrouter/free';
const MODEL_PATH = `/api/v1/models/model?id=${encodeURIComponent(FREE_MODEL)}`;
const ACCESS_PATH = '/api/v1/models/model/access/update';
const READ_GRANT = Object.freeze({ principal_type: 'user', principal_id: '*', permission: 'read' });
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const publicRead = grant => grant.principal_type === 'user' && grant.principal_id === '*' && grant.permission === 'read';
const sameGrant = (a, b) => ['principal_type', 'principal_id', 'permission'].every(key => a[key] === b[key]);

export class FreeModelConfigurationFailure extends Error {
  constructor(step, completed, uncertain) {
    super(step);
    this.receipt = { ok: false, failedStep: step, completedSteps: [...completed], partialOrUncertain: uncertain, noModelCalls: true };
  }
}


/** 将最小恢复记录排他写入服务器私有目录；不保存管理员令牌、上游密钥或完整连接配置。 */
async function writePrivateBackup(file, snapshot) {
  if (typeof file !== 'string' || !file.startsWith('/data/') || file.includes('/../') || file.includes('/./') || !file.endsWith('.json')) {
    throw new Error('backup-path');
  }
  await writeFile(file, JSON.stringify(snapshot, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
}


/**
 * 仅给已有可信 OAuth 连接开放一个免费模型；apply=false 只生成计划。
 * apply=true 要求调用方指定服务器私有备份路径；不修改默认模型、全局任务、角色、余额或其他模型。
 */
export async function configureOpenRouterFree(input, { fetchImpl = globalThis.fetch, backupWriter = writePrivateBackup } = {}) {
  const completed = [];
  let uncertain = false;
  const check = (condition, step) => { if (!condition) throw new FreeModelConfigurationFailure(step, completed, uncertain); };
  check(object(input) && typeof input.adminBearer === 'string' && input.adminBearer.length > 0 && input.adminBearer.length <= 16384 && !/\s/.test(input.adminBearer), 'input');
  check(input.apply === undefined || typeof input.apply === 'boolean', 'input.apply');
  const allowed = new Set([`GET /openai/config`, `POST /openai/config/update`, `GET ${MODEL_PATH}`, `POST ${ACCESS_PATH}`]);

  /** 只访问固定原生配置接口，总时限覆盖连接、响应和响应体；失败不回显敏感配置。 */
  async function request(method, path, step, body, allowMissing = false) {
    check(allowed.has(`${method} ${path}`), 'endpoint');
    let response;
    try {
      response = await fetchImpl(CHAT_BASE + path, {
        method, headers: { Authorization: `Bearer ${input.adminBearer}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(30000),
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (allowMissing && response.status === 404) { await response.body?.cancel(); return null; }
      check(response.ok, step);
      if (method === 'POST') { await response.body?.cancel(); return null; }
      return await response.json();
    } catch {
      throw new FreeModelConfigurationFailure(step, completed, uncertain);
    }
  }

  const currentConnection = await request('GET', '/openai/config', 'connection.read');
  check(object(currentConnection) && currentConnection.ENABLE_OPENAI_API === true && Array.isArray(currentConnection.OPENAI_API_BASE_URLS) && Array.isArray(currentConnection.OPENAI_API_KEYS) && object(currentConnection.OPENAI_API_CONFIGS), 'connection.schema');
  // 原生 update 会截断/补齐密钥数组并丢弃非数字索引配置；不在本次窄变更中迁移这些旧形状。
  check(currentConnection.OPENAI_API_KEYS.length === currentConnection.OPENAI_API_BASE_URLS.length, 'connection.key-count');
  const validConfigKeys = new Set(currentConnection.OPENAI_API_BASE_URLS.map((_, index) => String(index)));
  check(Object.keys(currentConnection.OPENAI_API_CONFIGS).every(key => validConfigKeys.has(key)), 'connection.indexed-config-required');
  const targetIndexes = currentConnection.OPENAI_API_BASE_URLS.flatMap((url, index) => url === RELAY_BASE ? [index] : []);
  check(targetIndexes.length === 1, 'connection.target');
  const index = targetIndexes[0];
  const configKey = String(index);
  const targetConfig = currentConnection.OPENAI_API_CONFIGS[configKey];
  check(object(targetConfig) && targetConfig.enable === true && targetConfig.auth_type === 'system_oauth' && targetConfig.api_type === 'responses', 'connection.protocol');
  check(!targetConfig.prefix_id && !targetConfig.azure && (!targetConfig.provider || targetConfig.provider === 'openai'), 'connection.options');
  check(currentConnection.OPENAI_API_KEYS[index] === '' && targetConfig.forward_cookies === false && object(targetConfig.headers) && Object.keys(targetConfig.headers).length === 0, 'connection.credentials');
  check(targetConfig.model_ids === undefined || (Array.isArray(targetConfig.model_ids) && targetConfig.model_ids.every(id => typeof id === 'string')), 'connection.models');
  // 空过滤列表表示从上游动态获取；只有已有显式白名单时才追加免费模型。
  const nextConfig = { ...targetConfig };
  if (targetConfig.model_ids?.length && !targetConfig.model_ids.includes(FREE_MODEL)) nextConfig.model_ids = [...targetConfig.model_ids, FREE_MODEL];
  const nextConnection = { ...currentConnection, OPENAI_API_CONFIGS: { ...currentConnection.OPENAI_API_CONFIGS, [configKey]: nextConfig } };
  const connectionChanged = !isDeepStrictEqual(currentConnection, nextConnection);
  const currentModel = await request('GET', MODEL_PATH, 'model.read', undefined, true);
  if (currentModel !== null) {
    check(object(currentModel) && currentModel.id === FREE_MODEL && currentModel.base_model_id === null && currentModel.is_active === true && Array.isArray(currentModel.access_grants), 'model.schema');
    check(currentModel.access_grants.every(grant => object(grant) && ['user', 'group'].includes(grant.principal_type) && typeof grant.principal_id === 'string' && ['read', 'write'].includes(grant.permission)), 'model.grants');
    check(!currentModel.access_grants.some(grant => grant.principal_type === 'user' && grant.principal_id === '*' && grant.permission !== 'read'), 'model.public-write');
  }
  const oldGrants = currentModel?.access_grants ?? [];
  const accessChanged = !oldGrants.some(publicRead);
  const nextGrants = accessChanged ? [...oldGrants, { ...READ_GRANT }] : oldGrants;
  const summary = { ok: true, model: FREE_MODEL, apply: input.apply === true, accessChanged, connectionFilterChanged: connectionChanged, noModelCalls: true, paidModelsAndTasksUnchanged: true };
  if (input.apply !== true || (!accessChanged && !connectionChanged)) return summary;

  // 先留最小恢复点，再做写入；记录中不含任何令牌或其他连接的配置。
  try {
    await backupWriter(input.backupFile, {
      version: 1, model: FREE_MODEL, modelExisted: currentModel !== null,
      beforeAccessGrants: oldGrants, expectedAfterAccessGrants: nextGrants,
      connectionIndex: index, modelFilterExisted: Object.hasOwn(targetConfig, 'model_ids'),
      beforeModelIds: targetConfig.model_ids ?? null, expectedAfterModelIds: nextConfig.model_ids ?? null,
    });
  } catch {
    throw new FreeModelConfigurationFailure('backup.write', completed, uncertain);
  }
  if (accessChanged) {
    const concurrentModel = await request('GET', MODEL_PATH, 'model.recheck', undefined, true);
    check(isDeepStrictEqual(concurrentModel, currentModel), 'model.concurrent-change');
    uncertain = true;
    await request('POST', ACCESS_PATH, 'model.write', { id: FREE_MODEL, ...(currentModel ? {} : { name: 'OpenRouter 免费' }), access_grants: nextGrants });
    const readback = await request('GET', MODEL_PATH, 'model.readback');
    check(object(readback) && readback.id === FREE_MODEL && readback.base_model_id === null && readback.is_active === true && Array.isArray(readback.access_grants), 'model.verify');
    check(nextGrants.every(before => readback.access_grants.some(after => sameGrant(before, after))) && readback.access_grants.every(after => nextGrants.some(before => sameGrant(before, after))), 'model.verify-grants');
    if (currentModel) check(['name', 'params', 'meta', 'user_id', 'is_active', 'base_model_id'].every(key => isDeepStrictEqual(currentModel[key], readback[key])), 'model.preserve');
    completed.push('model.access');
  }
  if (connectionChanged) {
    // 模型授权后才改过滤项；写前重读，避免覆盖其他管理员同时修改的连接。
    const concurrentConnection = await request('GET', '/openai/config', 'connection.recheck');
    check(isDeepStrictEqual(concurrentConnection, currentConnection), 'connection.concurrent-change');
    uncertain = true;
    await request('POST', '/openai/config/update', 'connection.write', nextConnection);
    const readback = await request('GET', '/openai/config', 'connection.readback');
    check(isDeepStrictEqual(readback, nextConnection), 'connection.verify');
    completed.push('connection.filter');
  }
  return { ...summary, completedSteps: completed };
}


/** CLI 仅从标准输入接收管理员凭据，不把输入或错误响应打印到日志。 */
async function main() {
  let input;
  try {
    let raw = '';
    for await (const chunk of process.stdin) { raw += chunk; if (raw.length > 32768) throw new Error('input'); }
    input = JSON.parse(raw);
  } catch {
    process.stderr.write(JSON.stringify({ ok: false, failedStep: 'input', partialOrUncertain: false, noModelCalls: true }) + '\n');
    process.exitCode = 1;
    return;
  }
  try {
    process.stdout.write(JSON.stringify(await configureOpenRouterFree(input)) + '\n');
  } catch (error) {
    process.stderr.write(JSON.stringify(error instanceof FreeModelConfigurationFailure ? error.receipt : { ok: false, failedStep: 'internal', partialOrUncertain: true, noModelCalls: true }) + '\n');
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
