// Keep recovery inside the existing Open WebUI toast and OIDC login flow.
export const AUTHORIZATION_ERROR_CODE = 'main_oauth_authorization_required';
export const AUTHORIZATION_TOAST_ID = 'main-oauth-authorization-required';
export const REAUTHORIZE_PATH = '/oauth/oidc/login';
export const AUTHORIZATION_MESSAGE = '模型授权不可用，请点击“重新授权”后重试。';

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
		// A display failure must not replace the original API error.
		return false;
	}
}

export function dismissMainOAuthAuthorizationRequired(toast) {
	try {
		toast.dismiss(AUTHORIZATION_TOAST_ID);
	} catch {
		// A display failure must not turn a successful model response into an error.
	}
}
