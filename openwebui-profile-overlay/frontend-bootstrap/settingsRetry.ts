import type { SettingsModalRequest } from '$lib/stores';

const KEY = 'parallellines.settings-retry.v1';
const MAX_AGE_MS = 5 * 60 * 1000;
type SettingsRequest = boolean | string | SettingsModalRequest;
const record = (value: unknown): value is Record<string, unknown> =>
	typeof value === 'object' && value !== null && !Array.isArray(value);
/** 只识别实际调用方使用的 tab 和模型 id 定位，不接受设置草稿或任意状态对象。 */
function navigationRequest(value: unknown): SettingsRequest | null {
	if (value === true || (typeof value === 'string' && value.length > 0)) return value;
	if (!record(value) || typeof value.tab !== 'string' ||
		Object.keys(value).some(key => key !== 'tab' && key !== 'state')) return null;
	const state = value.state;
	if (state === undefined) return { tab: value.tab };
	if (state === null) return { tab: value.tab, state: null };
	if (!record(state) || Object.keys(state).some(key => key !== 'id') ||
		(state.id !== undefined && state.id !== null && typeof state.id !== 'string')) return null;
	return { tab: value.tab, state: state.id === undefined ? {} : { id: state.id } };
}

/** 用户明确重试模块加载后，只保存原设置定位；Storage 写入失败向上抛出，不丢失请求后假装重试。 */
export function saveSettingsRetry(storage: Storage, userId: string, show: SettingsRequest): void {
	const navigation = navigationRequest(show);
	if (!userId || navigation === null) throw new Error('Unsupported settings retry request');
	storage.setItem(KEY, JSON.stringify({ version: 1, userId, createdAt: Date.now(), show: navigation }));
}

/** 已验证账号的页面重载后一次性取回设置定位；账号不匹配或记录结构不合法时不打开设置。 */
export function takeSettingsRetry(storage: Storage, userId: string): SettingsRequest | null {
	const saved = storage.getItem(KEY);
	if (!saved) return null;
	storage.removeItem(KEY);
	let value: unknown;
	// 这是本工具的一次性导航记录；坏记录不授权任何操作，消费后忽略且不输出原文。
	try { value = JSON.parse(saved); } catch { return null; }
	if (!record(value) || value.version !== 1 || value.userId !== userId ||
		typeof value.createdAt !== 'number' || Date.now() < value.createdAt || Date.now() - value.createdAt > MAX_AGE_MS) return null;
	return navigationRequest(value.show);
}
