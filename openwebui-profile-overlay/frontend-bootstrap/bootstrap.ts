/** 启动页使用的请求截止错误；只用于启动恢复，不改变其他 API 调用方的错误合同。 */
export class BootstrapTimeoutError extends Error {
	constructor() {
		super('Initialization request timed out');
		this.name = 'BootstrapTimeoutError';
	}
}

/**
 * 创建一个页面启动作用域。run 将 AbortSignal 传给读请求，12 秒后同时取消请求与等待；
 * cancel 用于页面销毁或启动失败，阻止迟到结果进入界面，不清除令牌或共享缓存。
 */
export function createBootstrapRequests() {
	const lifetime = new AbortController();
	return {
		signal: lifetime.signal,
		cancel() { lifetime.abort(new DOMException('Initialization cancelled', 'AbortError')); },
		async run<T>(request: (signal: AbortSignal) => Promise<T>): Promise<T> {
			lifetime.signal.throwIfAborted();
			const controller = new AbortController();
			const cancel = () => controller.abort(lifetime.signal.reason);
			lifetime.signal.addEventListener('abort', cancel, { once: true });
			const timer = setTimeout(() => controller.abort(new BootstrapTimeoutError()), 12000);
			let rejectAbort: () => void;
			const aborted = new Promise<never>((_resolve, reject) => {
				rejectAbort = () => reject(controller.signal.reason);
				controller.signal.addEventListener('abort', rejectAbort, { once: true });
			});
			try {
				// 除取消原生 fetch 外也终止等待；调用方只在 run 成功返回后写入页面状态。
				const result = await Promise.race([request(controller.signal), aborted]);
				controller.signal.throwIfAborted();
				return result;
			} finally {
				clearTimeout(timer);
				controller.signal.removeEventListener('abort', rejectAbort!);
				lifetime.signal.removeEventListener('abort', cancel);
			}
		}
	};
}
