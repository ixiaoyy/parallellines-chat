/** 个人连接表单共用逻辑；不保存 Key，不调用模型生成接口。 */
export const PERSONAL_CONNECTION_PREFIX = 'pl-personal-';

/** 仅完整保留命名空间走新合同；旧自定义前缀即使相似也不静默改名。 */
export function isPersonalConnectionNamespace(prefix) {
	return typeof prefix === 'string' && /^pl-personal-[a-f0-9]{32}$/.test(prefix);
}

/** 为新连接生成稳定命名空间；编辑已有连接时由调用方保留旧值。 */
export function createPersonalConnectionPrefix(cryptoProvider = globalThis.crypto) {
	const bytes = new Uint8Array(16);
	cryptoProvider.getRandomValues(bytes);
	return PERSONAL_CONNECTION_PREFIX + Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
}

/** 新连接只接受 HTTPS Base URL；旧非保留前缀继续保留原生地址合同。 */
export function normalizePersonalConnectionUrl(value, legacy = false) {
	if (legacy) return String(value ?? '').replace(/\/$/, '');
	const baseUrl = String(value ?? '').trim().replace(/\/+$/, '');
	let parsed;
	try {
		parsed = new URL(baseUrl);
	} catch {
		throw new Error('personal_connection_invalid_url');
	}
	if (parsed.protocol !== 'https:' || baseUrl.includes('?') || baseUrl.includes('#') || parsed.username || parsed.password) {
		throw new Error('personal_connection_invalid_url');
	}
	return baseUrl;
}

/** 预览和真实原生直连使用相同的路径拼接规则；无效输入不生成误导地址。 */
export function personalConnectionEndpoints(value, legacy = false) {
	try {
		const baseUrl = normalizePersonalConnectionUrl(value, legacy);
		if (!baseUrl) return null;
		return { models: `${baseUrl}/models`, chat: `${baseUrl}/chat/completions` };
	} catch {
		return null;
	}
}

/** 旧连接没有名称时复用原前缀或服务商域名，避免要求用户先迁移才能编辑。 */
export function personalConnectionName(config, url = '') {
	if (typeof config?.name === 'string' && config.name.trim()) return config.name.trim();
	if (typeof config?.prefix_id === 'string' && config.prefix_id && !isPersonalConnectionNamespace(config.prefix_id)) {
		return config.prefix_id;
	}
	try {
		return new URL(url).hostname;
	} catch {
		return '';
	}
}

/** 旧连接由编辑场景显式标记，空前缀/原认证也保留；仅新用法补命名空间和 Bearer。 */
export function personalConnectionConfig(config, name, legacy = false, cryptoProvider = globalThis.crypto) {
	const normalizedName = String(name ?? '').trim();
	if (!legacy && !normalizedName) throw new Error('personal_connection_name_required');
	return {
		...config,
		name: normalizedName,
		prefix_id: legacy ? (config?.prefix_id ?? '') : (config?.prefix_id || createPersonalConnectionPrefix(cryptoProvider)),
		auth_type: legacy ? (config?.auth_type ?? 'bearer') : 'bearer'
	};
}

/** 读取标准 /models 结果；空列表是有效结果，异常响应不能伪装成检查成功。 */
export function personalConnectionModelIds(response) {
	const models = Array.isArray(response) ? response : response?.data;
	if (!Array.isArray(models)) throw new Error('personal_connection_invalid_models');
	return [...new Set(models.map((model) => model?.id).filter((id) => typeof id === 'string' && id.trim()))];
}

/**
 * 管理一次个人模型目录读取：禁止并发、15 秒上限、关闭/改输入后取消。
 * 返回 null 只表示已被调用方取消的旧结果；网络和协议失败均返回显式 error。
 */
export function createPersonalModelListReader({ timeoutMs = 15000 } = {}) {
	let controller = null;
	let version = 0;
	return {
		cancel() {
			version += 1;
			controller?.abort();
			controller = null;
		},
		async read(load) {
			if (controller) return { kind: 'busy' };
			const current = new AbortController();
			controller = current;
			const run = ++version;
			let rejectAbort;
			const aborted = new Promise((_, reject) => { rejectAbort = reject; });
			const onAbort = () => rejectAbort(current.signal.reason);
			current.signal.addEventListener('abort', onAbort, { once: true });
			const timer = setTimeout(() => current.abort(new DOMException('Model list timeout', 'TimeoutError')), timeoutMs);
			try {
				const response = await Promise.race([Promise.resolve().then(() => load(current.signal)), aborted]);
				if (run !== version) return null;
				return { kind: 'success', modelIds: personalConnectionModelIds(response) };
			} catch (error) {
				if (run !== version) return null;
				return { kind: 'error', error };
			} finally {
				clearTimeout(timer);
				current.signal.removeEventListener('abort', onAbort);
				if (run === version) controller = null;
			}
		}
	};
}
