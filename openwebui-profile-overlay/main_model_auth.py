"""Keep a missing main-site model authorization distinct from an empty catalog."""
from fastapi import HTTPException


ERROR_CODE = 'main_oauth_authorization_required'
RELAY_MODELS_URL = 'https://api.pingxingxian.space/oauth-relay/v1/models'


class MainOAuthAuthorizationRequired(HTTPException):
    def __init__(self):
        super().__init__(status_code=403, detail={
            'code': ERROR_CODE,
            'message': '模型授权已失效或缺失，请用平行线账号重新授权。',
            'reauthorize_url': '/oauth/oidc/login',
        }, headers={'Cache-Control': 'no-store'})


def is_main_models_connection(url, config):
    return url == RELAY_MODELS_URL and isinstance(config, dict) and config.get('auth_type') == 'system_oauth'


def require_main_model_authorization(url, config, headers):
    if is_main_models_connection(url, config) and not headers.get('Authorization'):
        raise MainOAuthAuthorizationRequired()


def check_main_model_authorization_response(url, config, status):
    # The trusted relay uses 401 only for absent/invalid main-site OAuth tokens.
    # Its 403 binding/credential/quota errors must not trigger a login loop.
    if is_main_models_connection(url, config) and status == 401:
        raise MainOAuthAuthorizationRequired()
