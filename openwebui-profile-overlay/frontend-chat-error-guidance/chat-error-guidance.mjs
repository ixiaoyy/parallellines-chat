// 对话发送阶段的报错分类与下一步引导，不泄露上游凭据、不自动跳出当前对话。
export const ERROR_TOAST_ID = 'pl-chat-error-guidance';
export const REAUTHORIZE_PATH = '/oauth/oidc/login';
export const PLATFORM_QUOTA_URL = 'https://api.pingxingxian.space/topup';

const AUTH_CODES = new Set([
	'main_oauth_authorization_required',
	'invalid_api_key',
	'authentication_error',
	'unauthorized'
]);

const QUOTA_PATTERN =
	/insufficient[_\s-]*(user[_\s-]*)?quota|quota[_\s-]*exceeded|insufficient[_\s-]*balance|billing|credit|余额不足|额度不足|配额不足/i;
const RATE_LIMIT_PATTERN =
	/rate[_\s-]*limit|too many requests|requests per min|限流|请求过于频繁|请求过多/i;
const AUTH_PATTERN =
	/main_oauth_authorization_required|invalid[_\s-]*api[_\s-]*key|unauthorized|authentication|token expired|oauth|授权已失效|重新授权|未授权/i;
const NETWORK_PATTERN =
	/failed to fetch|networkerror|network error|timeout|timed out|econn|bad gateway|service unavailable|server connection failed|gateway timeout|fetch failed|连接失败|网络|超时|服务暂时不可用/i;

function isEnglishLocale(locale) {
	return typeof locale === 'string' && locale.toLowerCase().startsWith('en');
}

function extractStatus(error) {
	if (!error || typeof error !== 'object') return null;
	const candidates = [
		error.status,
		error.status_code,
		error.statusCode,
		error.response?.status,
		error.detail?.status,
		error.error?.status
	];
	for (const value of candidates) {
		const num = Number(value);
		if (Number.isInteger(num) && num >= 100 && num <= 599) return num;
	}
	return null;
}

function extractCode(error) {
	if (!error || typeof error !== 'object') return '';
	const candidates = [
		error.detail?.code,
		error.error?.code,
		error.error?.type,
		error.code
	];
	for (const value of candidates) {
		if (typeof value === 'string' && value.trim()) return value.trim().toLowerCase();
	}
	return '';
}

function extractCombinedText(error, rawMessage) {
	const parts = [];
	if (typeof rawMessage === 'string' && rawMessage.trim()) parts.push(rawMessage.trim());
	if (typeof error === 'string' && error.trim()) parts.push(error.trim());
	if (error && typeof error === 'object') {
		for (const field of [
			error.message,
			error.detail?.message,
			typeof error.detail === 'string' ? error.detail : '',
			error.error?.message
		]) {
			if (typeof field === 'string' && field.trim()) parts.push(field.trim());
		}
	}
	return parts.join(' ');
}

export function classifyChatError(error, { rawMessage = '', isPersonal = false, locale = 'zh-CN' } = {}) {
	const en = isEnglishLocale(locale);
	const status = extractStatus(error);
	const code = extractCode(error);
	const combined = extractCombinedText(error, rawMessage);

	// 1. 余额 / 额度不足优先于 403/429 泛化判断，避免把余额不足误判为限流或登录失效。
	if (status === 402 || QUOTA_PATTERN.test(code) || QUOTA_PATTERN.test(combined)) {
		if (isPersonal) {
			return {
				kind: 'quota',
				source: 'personal',
				actionType: 'open_personal_connections',
				title: en
					? 'Your personal API connection appears out of quota or balance.'
					: '当前个人 API 连接额度或余额不足，请检查你的服务商账户或切换模型。',
				actionLabel: en ? 'Manage Personal Connections' : '检查个人连接',
				duration: 10_000
			};
		}
		return {
			kind: 'quota',
			source: 'platform',
			actionType: 'open_platform_quota',
			title: en
				? 'Your platform balance is insufficient. Check quota or switch to a personal API connection.'
				: '平台额度不足，请查看平台额度或切换到个人 API 连接继续对话。',
			actionLabel: en ? 'Check Platform Quota' : '查看平台额度',
			duration: 10_000
		};
	}

	// 2. 限流（429）：明确提示稍后重试，绝不诱导重新登录。
	if (status === 429 || RATE_LIMIT_PATTERN.test(code) || RATE_LIMIT_PATTERN.test(combined)) {
		return {
			kind: 'rate_limit',
			source: isPersonal ? 'personal' : 'platform',
			actionType: 'retry',
			title: en
				? 'Rate limited right now. Please wait a moment and retry.'
				: '当前请求过于频繁，已触发限流，请稍后再试。',
			actionLabel: en ? 'Retry' : '稍后重试',
			duration: 8_000
		};
	}

	// 3. 授权失效：平台模型走主站重新授权，个人模型走检查个人连接（绝不误导主站重登）。
	if (
		AUTH_CODES.has(code) ||
		status === 401 ||
		(status === 403 && AUTH_PATTERN.test(combined)) ||
		AUTH_PATTERN.test(combined)
	) {
		if (isPersonal) {
			return {
				kind: 'auth',
				source: 'personal',
				actionType: 'open_personal_connections',
				title: en
					? 'Personal API Key authentication failed. Please check your connection settings.'
					: '个人 API Key 鉴权失败，请检查个人连接中的密钥与地址配置。',
				actionLabel: en ? 'Check Personal Connections' : '检查个人连接',
				duration: 10_000
			};
		}
		return {
			kind: 'auth',
			source: 'platform',
			actionType: 'reauthorize',
			title: en
				? 'Model authorization expired. Reauthorize with your ParallelLines account and retry.'
				: '模型授权已失效，请用平行线账号重新授权后重试。',
			actionLabel: en ? 'Reauthorize' : '重新授权',
			duration: Infinity
		};
	}

	// 4. 网络 / 上游服务波动：保留草稿与对话，给出直接重试。
	if (
		(status !== null && status >= 500) ||
		NETWORK_PATTERN.test(code) ||
		NETWORK_PATTERN.test(combined)
	) {
		return {
			kind: 'network',
			source: isPersonal ? 'personal' : 'platform',
			actionType: 'retry',
			title: en
				? 'Network or model service is temporarily unavailable. Your conversation is intact; try again.'
				: '网络或模型服务暂时不可用，当前对话已保留，可直接重试。',
			actionLabel: en ? 'Retry' : '立即重试',
			duration: 8_000
		};
	}

	// 5. 兜底：保留原始可读信息，不臆造错误分类。
	const fallback =
		(typeof rawMessage === 'string' && rawMessage.trim()) ||
		(en ? 'Request failed. Please retry.' : '请求失败，请稍后重试。');
	return {
		kind: 'unknown',
		source: isPersonal ? 'personal' : 'platform',
		actionType: 'retry',
		title: fallback,
		actionLabel: en ? 'Retry' : '重试',
		duration: 6_000
	};
}

export function presentChatErrorGuidance({
	error,
	rawMessage = '',
	isPersonal = false,
	locale = 'zh-CN',
	toast = null,
	onReauthorize = null,
	onOpenPersonalConnections = null,
	onOpenPlatformQuota = null,
	onRetry = null
} = {}) {
	const guidance = classifyChatError(error, { rawMessage, isPersonal, locale });

	let actionHandler = null;
	if (guidance.actionType === 'reauthorize') actionHandler = onReauthorize;
	else if (guidance.actionType === 'open_personal_connections') actionHandler = onOpenPersonalConnections;
	else if (guidance.actionType === 'open_platform_quota') actionHandler = onOpenPlatformQuota;
	else if (guidance.actionType === 'retry') actionHandler = onRetry;

	if (toast && typeof toast.error === 'function') {
		try {
			const options = {
				id: ERROR_TOAST_ID,
				duration: guidance.duration
			};
			if (typeof actionHandler === 'function') {
				options.action = {
					label: guidance.actionLabel,
					onClick: () => actionHandler()
				};
			}
			toast.error(guidance.title, options);
		} catch {
			// 提示组件异常不能破坏消息错误状态落盘。
		}
	}

	const cleanRaw = typeof rawMessage === 'string' ? rawMessage.trim() : '';
	const messageContent =
		guidance.kind === 'unknown' || !cleanRaw || cleanRaw === guidance.title
			? guidance.title
			: `${guidance.title}\n(${cleanRaw})`;

	return {
		...guidance,
		messageContent
	};
}
