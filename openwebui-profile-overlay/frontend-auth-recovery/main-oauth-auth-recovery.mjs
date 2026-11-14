// 复用 Open WebUI 原生提示及登录入口；不接受服务端指定的跳转地址。
export const AUTHORIZATION_ERROR_CODE = 'main_oauth_authorization_required';
export const AUTHORIZATION_TOAST_ID = 'main-oauth-authorization-required';
export const REAUTHORIZE_PATH = '/oauth/oidc/login';
export const AUTHORIZATION_MESSAGE = '模型授权已失效，请点击重新授权后重试。';

/** 仅对固定授权错误显示操作提示；导航必须由用户点击，不清除 Chat 登录。 */
export function showMainOAuthAuthorizationRequired(status, error, toast, navigate) {
	if (status !== 403 || error?.detail?.code !== AUTHORIZATION_ERROR_CODE) return false;

	try {
		toast.error(AUTHORIZATION_MESSAGE, {
			id: AUTHORIZATION_TOAST_ID,
			duration: Infinity,
			action: {
				label: '重新授权',
				onClick: () => navigate(REAUTHORIZE_PATH)
			}
		});
		return true;
	} catch {
		// 提示组件失败不能覆盖原接口错误。
		return false;
	}
}

/** 目录发现成功后只清除此恢复提示，不干扰其他通知。 */
export function dismissMainOAuthAuthorizationRequired(toast) {
	try {
		toast.dismiss(AUTHORIZATION_TOAST_ID);
	} catch {
		// 提示组件失败不能把模型请求成功改成失败。
	}
}
