// 仅在后台恢复当前用户模型授权；不离开主页面，不保存或重发聊天内容。
import { AUTHORIZATION_TOAST_ID, REAUTHORIZE_PATH } from './main-oauth-auth-recovery.mjs';

export const CHAT_ORIGIN = 'https://chat.pingxingxian.space';
export const AUTHORIZATION_ENDPOINT = 'https://pingxingxian.space/auth/oidc/auth';
export const RECOVERY_ENDPOINT = '/oauth/oidc/model-authorization';
export const RECOVERY_MESSAGE_TYPE = 'pl:model-authorization:result';
export const MODELS_RECOVERED_EVENT = 'pl:model-authorization:restored';
export const RECOVERY_ATTEMPT_KEY = 'pl:model-authorization:auto-attempted';
export const RECOVERY_TIMEOUT_MS = 20000;

/** 总时限同时覆盖准备响应和 frame；取消后不让迟到的响应恢复页面状态。 */
function abortable(promise, signal) {
	if (signal.aborted) return Promise.reject(signal.reason);
	let cancel;
	const aborted = new Promise((_resolve, reject) => {
		cancel = () => reject(signal.reason);
		signal.addEventListener('abort', cancel, { once: true });
	});
	return Promise.race([promise, aborted]).finally(() => signal.removeEventListener('abort', cancel));
}

/** 创建后台恢复协调器；每个失败阶段最多尝试一次，实际目录成功才开始下一阶段。 */
export function createModelAuthorizationRecovery(environment = globalThis, onResult = () => {}) {
	let attempted = false;
	let flight = null;
	let lastResult = 'failed';

	async function run(token, controller) {
		let frame = null;
		let onMessage = null;
		const timer = environment.setTimeout(() => controller.abort(), RECOVERY_TIMEOUT_MS);
		try {
			const response = await abortable(environment.fetch(RECOVERY_ENDPOINT, {
				method: 'POST', credentials: 'same-origin', signal: controller.signal,
				headers: { Accept: 'application/json', Authorization: `Bearer ${token}` }
			}), controller.signal);
			if (!response.ok) {
				const body = await abortable(response.json(), controller.signal);
				return body?.detail?.code === 'model_recovery_identity_mismatch' ? 'identity_mismatch' : 'failed';
			}
			const result = await abortable(response.json(), controller.signal);
			if (!/^[a-f0-9]{32}$/.test(result?.request_id ?? '')) return 'failed';
			const authorization = new URL(result.authorization_url);
			if (`${authorization.origin}${authorization.pathname}` !== AUTHORIZATION_ENDPOINT ||
				authorization.username || authorization.password || authorization.hash ||
				authorization.searchParams.get('state') !== `plmr_${result.request_id}`) return 'failed';
			frame = environment.document.createElement('iframe');
			frame.hidden = true;
			frame.title = '模型授权恢复';
			frame.referrerPolicy = 'no-referrer';
			// 原链需要脚本、同站 Cookie 和表单；不允许导航主页面或打开窗口。
			frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms');
			const completed = new Promise((resolve) => {
				onMessage = (event) => {
					if (event.origin !== CHAT_ORIGIN || event.source !== frame.contentWindow ||
						event.data?.type !== RECOVERY_MESSAGE_TYPE || event.data?.request_id !== result.request_id ||
						!['success', 'failed', 'identity_mismatch'].includes(event.data?.status)) return;
					resolve(event.data.status);
				};
				environment.window.addEventListener('message', onMessage);
				frame.src = authorization.href;
				environment.document.body.appendChild(frame);
			});
			return await abortable(completed, controller.signal);
		} catch {
			// 超时/拦截/网络失败不能证明主站已退出登录，只报告后台恢复未完成。
			return 'failed';
		} finally {
			environment.clearTimeout(timer);
			if (onMessage) environment.window.removeEventListener('message', onMessage);
			frame?.remove();
		}
	}

	return {
		start(token) {
			if (flight) return { pending: true };
			try {
				const stored = environment.sessionStorage.getItem(RECOVERY_ATTEMPT_KEY);
				if (stored === 'identity_mismatch') lastResult = 'identity_mismatch';
				if (attempted || !token || stored) {
					return { pending: false, status: lastResult };
				}
				environment.sessionStorage.setItem(RECOVERY_ATTEMPT_KEY, '1');
			} catch {
				attempted = true;
				return { pending: false, status: lastResult };
			}
			attempted = true;
			const current = { controller: new AbortController(), cancelled: false };
			flight = current;
			void run(token, current.controller).then((status) => {
				// 先释放 flight，实际目录成功才能清标记；单纯 frame 成功不能允许循环。
				if (flight === current) flight = null;
				lastResult = status;
				// 账号不一致的非敏感原因跨刷新保留，不能退回会切号的普通登录按钮。
				if (status === 'identity_mismatch') {
					try { environment.sessionStorage.setItem(RECOVERY_ATTEMPT_KEY, status); } catch { /* 保留本页原因。 */ }
				}
				if (!current.cancelled) {
					try { onResult(status); } catch { /* 通知失败不能变成未处理的后台拒绝。 */ }
				}
			});
			return { pending: true };
		},
		healthy() {
			if (flight) return;
			try { environment.sessionStorage.removeItem(RECOVERY_ATTEMPT_KEY); } catch { return; }
			attempted = false;
			lastResult = 'failed';
		},
		retry(token) {
			// 仅由用户点击重查，仍须经过服务端原账号 guard，不能改用普通登录切号。
			if (flight) return { pending: true };
			try { environment.sessionStorage.removeItem(RECOVERY_ATTEMPT_KEY); } catch {
				return { pending: false, status: lastResult };
			}
			attempted = false;
			lastResult = 'failed';
			return this.start(token);
		},
		cancel() {
			if (!flight) return;
			flight.cancelled = true;
			flight.controller.abort();
		}
	};
}

let coordinator;
let currentToast;
let currentToken;

/** 账号不一致不得给出会切号的普通登录动作；其他失败保留显式手动恢复。 */
export function showModelRecoveryFailure(status, toast, navigate, retry) {
	try {
		if (status === 'identity_mismatch') {
			toast.error('登录账号不一致，已停止自动恢复。请确认主站与当前 Chat 使用原来的同一账号，再重新检查授权。', {
				id: AUTHORIZATION_TOAST_ID, duration: Infinity,
				...(retry ? { action: { label: '重新检查授权', onClick: retry } } : {})
			});
			return;
		}
		toast.error('后台恢复模型授权未完成，请手动重新授权后重试。', {
			id: AUTHORIZATION_TOAST_ID, duration: Infinity,
			action: { label: '重新授权', onClick: () => navigate(REAUTHORIZE_PATH) }
		});
	} catch {
		// 原生通知组件不可用时，不覆盖触发恢复的原接口错误。
	}
}

/** 延迟创建浏览器协调器，SSR 或尚未发生授权错误时不接触页面。 */
function getCoordinator() {
	coordinator ??= createModelAuthorizationRecovery(globalThis, (status) => {
		if (status === 'success') {
			window.dispatchEvent(new Event(MODELS_RECOVERED_EVENT));
		} else {
			reportFailure(status);
		}
	});
	return coordinator;
}

/** 用固定原因展示操作；显式重查仍使用原用户绑定流程。 */
function reportFailure(status) {
	showModelRecoveryFailure(status, currentToast, (path) => window.location.assign(path), () => {
		const result = getCoordinator().retry(currentToken);
		if (!result.pending) reportFailure(result.status);
	});
}

/** 同步启动而不等待 frame，保留调用方原有的启动截止和接口错误。 */
export function startModelAuthorizationRecovery(token, toast) {
	currentToast = toast;
	currentToken = token;
	const result = getCoordinator().start(token);
	if (!result.pending) reportFailure(result.status);
}

/** 只有真实目录成功才允许未来的新失效再次恢复。 */
export function clearModelAuthorizationAttempt() { getCoordinator().healthy(); }

/** App 布局销毁时终止后台恢复并清理 frame，不发送迟到事件。 */
export function cancelModelAuthorizationRecovery() { coordinator?.cancel(); }
