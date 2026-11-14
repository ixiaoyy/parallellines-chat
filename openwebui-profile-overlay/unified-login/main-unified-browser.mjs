// 仅交换固定主站的非敏感会话状态；凭据只经原生 OIDC 顶层跳转，不进入消息或地址。
export const MAIN_ORIGIN = 'https://pingxingxian.space';
export const CHAT_ORIGIN = 'https://chat.pingxingxian.space';
export const STATE_TYPE = 'pl:unified-session';
export const DRAFT_KEY = 'pl:unified-guest-draft.v1';
const MAX_DRAFT = 20000;
const EPOCH_KEY = 'pl:unified:epoch';
const ATTEMPT_KEY = 'pl:unified:oidc-attempt';
let knownEpoch = '';
let generation = 0;

/** 接受同源站内返回地址；拒绝协议、认证回环及跨站地址。 */
export function safeReturn(value) {
	if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\x00-\x20]|%2f|%5c|%0[ad]/i.test(value)) return '/';
	const target = new URL(value, CHAT_ORIGIN);
	return target.origin === CHAT_ORIGIN && !target.pathname.startsWith('/oauth/') && !target.pathname.startsWith('/auth')
		? target.pathname + target.search + target.hash : '/';
}

/** 只保留本标签页游客新写的纯文本，不读取旧账号 chat-input/附件或自动发送。 */
export function saveGuestDraft(text, storage = sessionStorage) {
	const value = typeof text === 'string' ? text.slice(0, MAX_DRAFT) : '';
	storage.setItem(DRAFT_KEY, JSON.stringify({ version: 1, text: value }));
}

/** 恢复独立游客草稿；坏数据保留原值，不冒充聊天记录或自动发消息。 */
export function readGuestDraft(storage = sessionStorage) {
	try {
		const value = JSON.parse(storage.getItem(DRAFT_KEY) || 'null');
		return value?.version === 1 && typeof value.text === 'string' ? value.text.slice(0, MAX_DRAFT) : '';
	} catch { return ''; }
}

/** 只在空的新对话中显式取回草稿，不提交消息，也不读取旧账号的输入缓存。 */
export function consumeGuestDraft(storage = sessionStorage) {
	const text = readGuestDraft(storage);
	try { storage.removeItem(DRAFT_KEY); } catch { /* 无持久化时由当前页面保留。 */ }
	return text;
}

/** 当前浏览器换号/退出只清理本地私有 UI 缓存；保留语言、主题和无关站点存储。 */
export function clearPrivateState(environment = globalThis) {
	// 存储访问失败交给调用方显示恢复状态；不能清不掉旧凭据还接续新账号。
	for (const key of Object.keys(environment.localStorage)) {
		if (key.startsWith('chat-input') || ['token', 'settings', 'selectedModels', 'selectedTerminalId', 'redirectPath'].includes(key)) environment.localStorage.removeItem(key);
	}
	// 原生草稿和待接续工具也在 sessionStorage，不能在 B 的新对话中恢复 A 的内容。
	for (const key of Object.keys(environment.sessionStorage)) {
		if (key.startsWith('chat-input') || [DRAFT_KEY, 'selectedModels', 'pl:model-authorization:auto-attempted', 'pendingOAuthToolId', 'oauthRedirectInProgressToolId'].includes(key)) environment.sessionStorage.removeItem(key);
	}
	environment.document.cookie = 'token=; Max-Age=0; path=/';
}

/** 读取自己的 JWT 声明仅用于决定必须清旧 UI，绝不据此前端内容授予登录或权限。 */
function localClaims(token, environment) {
	try {
		const encoded = token.split('.')[1].replaceAll('-', '+').replaceAll('_', '/');
		return JSON.parse(environment.atob(encoded));
	} catch { return {}; }
}

/** 接续已确认的主站身份；不可用不当退出，相同 epoch 不重复跳转，不以模型恢复切账号。 */
export async function syncUnifiedSession(state, environment = globalThis) {
	if (!state.available) return { available: false };
	// 原生回调仍负责消费 Cookie/MFA；状态 frame 不能在其完成前发起第二次 OIDC。
	if (environment.location.pathname === '/auth' && !environment.localStorage.getItem('token') &&
		(new URLSearchParams(environment.location.search).has('mfa') || /(?:^|; )token=/.test(environment.document.cookie))) return { callback: true };
	const current = ++generation;
	const token = environment.localStorage.getItem('token');
	const local = token ? localClaims(token, environment) : {};
	// 恢复标记仅用于请求后端再核白名单/直连；前端自称恢复不能授予任何权限。
	if (token && local.pl_unified_recovery === true) {
		const response = await environment.fetch('/api/v1/auths/unified/session', { credentials: 'same-origin', headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(4000) });
		if (current !== generation) return { cancelled: true };
		if (response.status >= 500) return { available: false };
		if (response.ok && (await response.json()).recovery === true) return { recovery: true };
	}
	const previous = knownEpoch || environment.localStorage.getItem(EPOCH_KEY);
	const changed = token && (!state.authenticated || local.pl_unified?.subject !== state.subject || (previous && previous !== state.auth_epoch));
	if (changed) {
		environment.dispatchEvent?.(new Event('pl:unified-private-reset'));
		// 清理请求不要求旧 Main 仍有效；即使网络故障也先撤旧私有 UI，不能串到新账号。
		try { await environment.fetch('/api/v1/auths/unified/local-signout', { method: 'POST', credentials: 'same-origin', headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(4000) }); } catch { /* Main 已明确身份变化；旧凭据服务端仍受绑定复核。 */ }
		if (current !== generation) return { cancelled: true };
		try { clearPrivateState(environment); } catch { return { storage_unavailable: true }; }
	}
	if (!state.authenticated) {
		knownEpoch = '';
		environment.localStorage.removeItem(EPOCH_KEY);
		environment.sessionStorage.removeItem(ATTEMPT_KEY);
		if (changed) environment.location.replace('/');
		return { authenticated: false };
	}
	if (token && !changed) {
		const response = await environment.fetch('/api/v1/auths/unified/session', { credentials: 'same-origin', headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(4000) });
		if (current !== generation) return { cancelled: true };
		if (response.status >= 500) return { available: false };
		if (response.ok) {
			const value = await response.json();
			if (value.recovery === true) return { recovery: true };
			if (value.subject === state.subject && value.unified === true) {
				knownEpoch = state.auth_epoch; environment.localStorage.setItem(EPOCH_KEY, knownEpoch);
				environment.sessionStorage.removeItem(ATTEMPT_KEY); return { authenticated: true };
			}
		}
		environment.dispatchEvent?.(new Event('pl:unified-private-reset'));
		try { clearPrivateState(environment); } catch { return { storage_unavailable: true }; }
	}
	if (environment.sessionStorage.getItem(ATTEMPT_KEY) === state.auth_epoch) return { needs_action: true };
	environment.sessionStorage.setItem(ATTEMPT_KEY, state.auth_epoch);
	knownEpoch = state.auth_epoch; environment.localStorage.setItem(EPOCH_KEY, knownEpoch);
	loginForFeature(readGuestDraft(environment.sessionStorage), environment);
	return { pending: true };
}

/** 固定 Main 单次退出票据直达，不套用原生 OP 的 post_logout_redirect 参数。 */
export function isMainLogout(value) {
	try { const url = new URL(value); return url.origin === MAIN_ORIGIN && !url.username && !url.password && url.pathname === '/auth/unified/logout' && !url.hash && url.searchParams.getAll('ticket').length === 1 && /^[A-Za-z0-9_-]{32,128}$/.test(url.searchParams.get('ticket') || '') && [...url.searchParams.keys()].every(key => key === 'ticket'); } catch { return false; }
}

/** 主动使用受限功能才进入原生 OIDC；返回地址保留在 Chat 自己的存储中。 */
export function loginForFeature(text = '', environment = globalThis) {
	// 不能保留草稿时先由界面提示复制，用户确认后可使用空草稿继续登录。
	saveGuestDraft(text, environment.sessionStorage);
	environment.localStorage.setItem('redirectPath', safeReturn(environment.location.pathname + environment.location.search + environment.location.hash));
	environment.location.assign('/oauth/oidc/login');
}

/** 有界主站状态 frame；同一 epoch 去重，不把不可用或超时当退出。 */
export function watchUnifiedSession(onState, environment = globalThis) {
	const requestId = environment.crypto.randomUUID().replaceAll('-', '');
	const frame = environment.document.createElement('iframe');
	frame.hidden = true;
	frame.title = '主站登录状态';
	frame.referrerPolicy = 'no-referrer';
	frame.src = `${MAIN_ORIGIN}/auth/unified/state?client=chat&request_id=${requestId}`;
	let pending = false;
	let destroyed = false;
	let timer;
	const notify = (state) => { if (!destroyed) onState(state); };
	const refresh = () => {
		if (destroyed || pending) return;
		pending = true;
		timer = environment.setTimeout(() => { pending = false; notify({ available: false }); }, 7000);
		frame.contentWindow?.postMessage({ type: 'pl:unified-session:refresh', version: 1, request_id: requestId }, MAIN_ORIGIN);
	};
	const message = (event) => {
		const state = event.data;
		if (event.origin !== MAIN_ORIGIN || event.source !== frame.contentWindow || state?.type !== STATE_TYPE ||
			state.version !== 1 || state.request_id !== requestId || typeof state.available !== 'boolean') return;
		environment.clearTimeout(timer); pending = false;
		if (!state.available) { notify({ available: false }); return; }
		if (typeof state.authenticated !== 'boolean' || (state.authenticated ?
			(typeof state.subject !== 'string' || !state.subject || state.subject.length > 128 || typeof state.auth_epoch !== 'string' || !state.auth_epoch || state.auth_epoch.length > 128) :
			(state.subject !== null || state.auth_epoch !== null))) return;
		// 同状态在 focus 后仍需复核本地后端，允许从 authority 暂不可用恢复；OIDC 尝试另按 epoch 去重。
		notify(state);
	};
	environment.window.addEventListener('message', message);
	environment.window.addEventListener('focus', refresh);
	frame.addEventListener('load', refresh);
	environment.document.body.appendChild(frame);
	refresh();
	return () => {
		destroyed = true; environment.clearTimeout(timer);
		environment.window.removeEventListener('message', message);
		environment.window.removeEventListener('focus', refresh); frame.remove();
	};
}
