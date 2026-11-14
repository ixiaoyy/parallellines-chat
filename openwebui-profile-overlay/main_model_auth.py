"""区分可信模型目录的授权失效与服务错误，避免失败伪装为空目录。"""
from fastapi import HTTPException


ERROR_CODE = 'main_oauth_authorization_required'
RELAY_MODELS_URL = 'https://api.pingxingxian.space/oauth-relay/v1/models'


class MainModelDiscoveryError(HTTPException):
    """保留可信网关的失败状态；不向浏览器暴露上游响应或凭据。"""

    def __init__(self, status_code=503):
        super().__init__(status_code=status_code, detail={
            'code': 'main_model_discovery_failed',
            'message': '模型列表加载失败，请稍后重试；若持续失败请联系管理员。',
        }, headers={'Cache-Control': 'no-store'})


class MainOAuthAuthorizationRequired(MainModelDiscoveryError):
    """仅授权缺失或无效时提供已有的主站重新授权入口。"""

    def __init__(self):
        HTTPException.__init__(self, status_code=403, detail={
            'code': ERROR_CODE,
            'message': '模型授权已失效或缺失，请用平行线账号重新授权。',
            'reauthorize_url': '/oauth/oidc/login',
        }, headers={'Cache-Control': 'no-store'})


def is_main_models_connection(url, config):
    """只匹配固定可信目录与逐用户 OAuth 连接，不改变其他提供方。"""
    return url == RELAY_MODELS_URL and isinstance(config, dict) and config.get('auth_type') == 'system_oauth'


def require_main_model_authorization(url, config, headers):
    """模型发现缺少当前用户凭据时立即拒绝，不发送匿名网关请求。"""
    if is_main_models_connection(url, config) and not headers.get('Authorization'):
        raise MainOAuthAuthorizationRequired()


def check_main_model_authorization_response(url, config, status):
    """401 要求重新授权；其余 HTTP 失败保留状态且不诱导重复登录。"""
    if not is_main_models_connection(url, config):
        return
    # 可信网关的 401 仅表示 OAuth 无效；绑定、额度和服务错误不能触发登录循环。
    if status == 401:
        raise MainOAuthAuthorizationRequired()
    if status >= 400:
        raise MainModelDiscoveryError(status)
