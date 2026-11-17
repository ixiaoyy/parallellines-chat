// 个人连接只复用当前账号明确选择的凭据，不把失效连接回退到平台余额。
const PREFIX = /^pl-personal-[a-f0-9]{32}$/;
const active = new Set();
class PersonalConnectionError extends Error {}

export const isPersonalPrefix = (value) => typeof value === 'string' && PREFIX.test(value);
export const isPersonalModel = (value) => typeof value === 'string' && /^pl-personal-[a-f0-9]{32}\..+$/.test(value);

/** 为个人模型记录非秘密来源；真实 API ID 保持在原生 openai.id 中。 */
export function decoratePersonalModel(model, config, url, ownerId) {
  if (!isPersonalPrefix(config?.prefix_id)) return model;
  const name = config.name?.trim() || '个人连接';
  return {
    // 模型目录是供应商输入，只携带原生使用的非秘密字段，不透传任意响应对象。
    id: model.id,
    name: `${name} / ${model.name ?? model.openai?.id ?? model.id}`,
    owned_by: 'openai',
    openai: { id: model.openai?.id ?? model.id },
    urlIdx: model.urlIdx,
    ...(model.tags ? { tags: model.tags } : {}),
    personal_connection: { id: config.prefix_id, name, origin: url, owner_id: ownerId }
  };
}

/** 模型发现设置总截止；调用方取消时沿用原取消原因。 */
export function boundedPersonalRequest(parent, milliseconds = 15000) {
  const controller = new AbortController();
  const cancel = () => controller.abort(parent.reason);
  if (parent?.aborted) cancel();
  else parent?.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(() => controller.abort(new PersonalConnectionError('个人连接请求超时，请检查地址和网络。')), milliseconds);
  return { signal: controller.signal, close() { clearTimeout(timer); parent?.removeEventListener('abort', cancel); } };
}

/** 使用原生模型列表调用，限制等待并避免上游报错回显完整 Key。 */
export async function readPersonalModels(load, url, key, signal) {
  const request = boundedPersonalRequest(signal);
  try { return await load(url, key, request.signal); }
  catch (error) {
    if (signal?.aborted) throw signal.reason;
    if (request.signal.aborted) throw request.signal.reason;
    throw new PersonalConnectionError('个人连接模型列表读取失败，可检查地址、Key 和跨域配置，或手动填写模型 ID。');
  } finally { request.close(); }
}

/** 启动只等待平台目录；第三方自动发现随后独立完成，失败不能锁住设置入口。 */
export function deferPersonalModelDiscovery({ connections, ownerId, getState, signal, load, normalizeTags, onResult }) {
  const targets = Object.entries(connections?.OPENAI_API_CONFIGS ?? {}).filter(([, value]) =>
    isPersonalPrefix(value?.prefix_id) && value.enable !== false && !value.model_ids?.length
  );
  if (!targets.length) return false;
  const cancel = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); };
  const timer = setTimeout(async () => {
    try {
      const results = await Promise.all(targets.map(async ([index, config]) => {
        const url = connections.OPENAI_API_BASE_URLS[index];
        const key = connections.OPENAI_API_KEYS[index];
        try {
          const response = await readPersonalModels(load, url, key, signal);
          const rows = Array.isArray(response) ? response : response?.data;
          if (!Array.isArray(rows)) throw new PersonalConnectionError('个人模型目录格式无效。');
          return { index, prefix: config.prefix_id, url, key, rows };
        } catch { return { index, prefix: config.prefix_id, url, key, rows: [], failed: true }; }
      }));
      const state = getState();
      if (signal?.aborted || state.ownerId !== ownerId) return;
      const result = { models: [], prefixes: [], errors: 0 };
      for (const entry of results) {
        const current = Object.entries(state.connections?.OPENAI_API_CONFIGS ?? {}).find(([, config]) => config?.prefix_id === entry.prefix);
        if (!current) continue;
        const [index, config] = current;
        // 发现期间的换号、换 Key、改地址、停用或手动模型选择均使旧结果失效。
        if (config.enable === false || config.model_ids?.length || state.connections.OPENAI_API_BASE_URLS[index] !== entry.url || state.connections.OPENAI_API_KEYS[index] !== entry.key) continue;
        result.prefixes.push(entry.prefix);
        if (entry.failed) result.errors += 1;
        for (const row of entry.rows) {
          if (typeof row?.id !== 'string' || !row.id) continue;
          result.models.push({ ...decoratePersonalModel({ id: `${entry.prefix}.${row.id}`, name: typeof row.name === 'string' ? row.name : row.id,
            openai: { id: row.id }, urlIdx: index, tags: normalizeTags(config.tags) }, config, entry.url, ownerId), direct: true });
        }
      }
      onResult(result);
    } finally { signal?.removeEventListener('abort', cancel); }
  }, 0);
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
  return true;
}

/** 后台发现只替换对应个人来源，保留平台及其他连接的当前目录。 */
export function mergePersonalModelDiscovery(models, result) {
  const prefixes = new Set(result.prefixes);
  return models.filter((model) => !prefixes.has(model.personal_connection?.id)).concat(result.models);
}

/** 按稳定连接 ID 解析，拒绝删除、禁用、换账号或改地址后的旧模型快照。 */
export function resolvePersonalConnection(model, body, connections, ownerId) {
  const source = model?.personal_connection;
  if (!ownerId || !source || source.owner_id !== ownerId || !isPersonalPrefix(source.id) ||
      !model.id?.startsWith(`${source.id}.`) || body.model !== model.id) {
    throw new PersonalConnectionError('个人连接已失效，请重新选择模型。');
  }
  const entries = Object.entries(connections?.OPENAI_API_CONFIGS ?? {});
  const entry = entries.find(([, config]) => config?.prefix_id === source.id);
  if (!entry) throw new PersonalConnectionError('个人连接已删除，请重新选择模型。');
  const [index, config] = entry;
  const url = connections.OPENAI_API_BASE_URLS?.[index];
  const key = connections.OPENAI_API_KEYS?.[index];
  if (config.enable === false || url !== source.origin || typeof key !== 'string' || !key) {
    throw new PersonalConnectionError('个人连接已更改或停用，请重新选择模型。');
  }
  let parsed;
  try { parsed = new URL(url); }
  catch { throw new PersonalConnectionError('个人连接 API 地址无效。'); }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new PersonalConnectionError('个人连接需要不含账号密码、查询参数的 HTTPS API 地址。');
  }
  const apiModel = model.id.slice(source.id.length + 1);
  if (!apiModel || (config.model_ids?.length && !config.model_ids.includes(apiModel))) {
    throw new PersonalConnectionError('该模型已从个人连接中移除，请重新选择模型。');
  }
  return { url, key, model: apiModel };
}

export function cancelPersonalRequests() {
  for (const request of active) request.controller.abort(new Error('登录或连接信息已更新，请重新发送。'));
}

/** 原生停止事件按会话/消息取消，不误停其他聊天里的个人请求。 */
export function cancelPersonalChatRequests(chatId, messageId) {
  if (typeof chatId !== 'string' || !chatId) return;
  for (const request of active) {
    if (request.chatId === chatId && (!messageId || request.messageId === messageId)) {
      request.controller.abort(new PersonalConnectionError('已停止生成。'));
    }
  }
}

/** 设置变化只终止受影响的请求，主题等无关偏好不打断流式回答。 */
export function revalidatePersonalRequests(connections, ownerId) {
  for (const request of active) {
    try {
      const current = resolvePersonalConnection(request.model, request.body, connections, ownerId);
      if (current.key !== request.key) request.controller.abort(new Error('个人 Key 已更新，请重新发送。'));
    } catch { request.controller.abort(new Error('个人连接已更新，请重新发送。')); }
  }
}

/** 原生 socket 协议的个人分支；连接最多 30 秒、响应最多 10 分钟，不自动重试。 */
export async function handlePersonalCompletion({ data, chatId, messageId, connections, ownerId, getOwnerId, socket, cb, chatCompletion }) {
  const { model, form_data: body, channel } = data;
  const controller = new AbortController();
  let acknowledged = false;
  let timer;
  let request;
  const emit = (value) => { if (getOwnerId() === ownerId) socket.emit(channel, value); };
  const ensureActive = () => {
    if (controller.signal.aborted || getOwnerId() !== ownerId) throw new PersonalConnectionError('个人连接请求已取消。');
  };
  try {
    const connection = resolvePersonalConnection(model, body, connections, ownerId);
    request = { controller, model, body, key: connection.key, chatId, messageId };
    active.add(request);
    timer = setTimeout(() => controller.abort(new Error('个人连接响应超时。')), 30000);
    const [response] = await chatCompletion(connection.key, { ...body, model: connection.model }, connection.url, controller.signal);
    clearTimeout(timer);
    timer = setTimeout(() => controller.abort(new Error('个人连接已达到本次请求时间上限。')), 600000);
    if (!response?.ok) {
      const status = response?.status;
      const message = status === 401 ? '个人 Key 无效或已失效。' : status === 429 ? '个人连接请求过于频繁，请稍后再试。' :
        status === 402 || status === 403 ? '个人连接额度或权限不足，请到对应服务商检查。' : '个人连接请求失败，请检查服务商状态。';
      throw new PersonalConnectionError(message);
    }
    ensureActive();
    if (body.stream ?? false) {
      if (!response.body) throw new PersonalConnectionError('个人连接未返回响应流。');
      acknowledged = true; cb({ status: true });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = '';
      let completed = false;
      let streamDone = false;
      // 以 SSE 空行为帧边界，允许 UTF-8 和 JSON 跨网络块；中断不能伪装生成成功。
      const frame = (text) => {
        ensureActive();
        const payload = text.split(/\r?\n/).filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
        if (!payload) return;
        if (streamDone) return;
        if (payload === '[DONE]') { completed = true; streamDone = true; emit('data: [DONE]'); return; }
        const event = JSON.parse(payload);
        if (!event || typeof event !== 'object' || event.error) throw new PersonalConnectionError('个人连接返回了错误或无效的响应流。');
        if (event.choices?.some((choice) => choice.finish_reason != null)) completed = true;
        emit(`data: ${JSON.stringify(event)}`);
      };
      while (true) {
        const { done, value } = await reader.read();
        ensureActive();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        if (pending.length > 1048576) throw new PersonalConnectionError('个人连接返回的单条事件过大。');
        const frames = pending.split(/\r?\n\r?\n/); pending = frames.pop();
        for (const text of frames) frame(text);
        if (streamDone) { pending = ''; break; }
      }
      pending += decoder.decode();
      if (pending.trim()) frame(pending);
      if (!completed) throw new PersonalConnectionError('个人连接响应中断，请重试。');
    } else {
      const result = await response.json();
      ensureActive();
      if (!result || typeof result !== 'object' || Array.isArray(result) || result.error) throw new PersonalConnectionError('个人连接返回了错误或无效响应。');
      acknowledged = true; cb(result);
    }
  } catch (error) {
    // 不回传上游原始错误或请求对象，避免响应/日志回显 Key。
    const message = controller.signal.aborted ? '个人连接请求已取消或超时，请检查连接后重试。' :
      error instanceof PersonalConnectionError ? error.message : '个人连接响应中断或格式不正确，请重试。';
    if (acknowledged) emit({ error: { message } });
    else cb({ error: message });
  } finally {
    clearTimeout(timer);
    active.delete(request);
    controller.abort();
    // 终止信号不含正文，始终送回捕获的旧 Socket，解除已确认流的后端等待。
    socket.emit(channel, { done: true });
  }
}
