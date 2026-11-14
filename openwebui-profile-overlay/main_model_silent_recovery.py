"""通过原生 OIDC state 恢复同一 Chat 用户的模型授权，不重新登录或切换账号。"""
import asyncio
from copy import copy
import json
import logging
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from starlette.responses import HTMLResponse, JSONResponse
from authlib.integrations.starlette_client.integration import StarletteIntegration

from open_webui.config import OAUTH_AUTHORIZE_PARAMS
from open_webui.env import OAUTH_MAX_SESSIONS_PER_USER, WEBUI_AUTH_COOKIE_SAME_SITE, WEBUI_AUTH_COOKIE_SECURE
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.utils.auth import get_verified_user_by_token
from open_webui.utils.oauth import _normalize_token_expiry, get_oauth_runtime_config

CHAT_ORIGIN = 'https://chat.pingxingxian.space'
ISSUER = 'https://pingxingxian.space/auth/oidc'
AUTHORIZATION_ENDPOINT = ISSUER + '/auth'
CALLBACK_URL = CHAT_ORIGIN + '/oauth/oidc/login/callback'
MESSAGE_TYPE = 'pl:model-authorization:result'
STATE_PREFIX = 'plmr_'
STATE_DATA_KEY = 'pl_model_recovery'
STATE_NAMESPACE = 'pl_model_oidc'
STATE_TTL = 120
PREPARE_TIMEOUT = 5
CALLBACK_TIMEOUT = 15
log = logging.getLogger(__name__)


def create_model_recovery_client(manager):
    """逐请求克隆原客户端，仅隔离 state；旧后端的普通 oidc 客户端天然无法消费恢复 state。"""
    ordinary = manager.get_client('oidc')
    if ordinary is None:
        return None
    client = copy(ordinary)
    client.framework = StarletteIntegration(STATE_NAMESPACE, cache=ordinary.framework.cache)
    return client


def is_model_recovery_callback(request, provider):
    """只有专用 state 前缀和固定提供方进入恢复分支；普通 OAuth/MCP 不改变。"""
    return provider == 'oidc' and request.query_params.get('state', '').startswith(STATE_PREFIX)


def recovery_result(request_id, result):
    """向原 Chat 父窗口只发送关联 ID 与固定结果，不包含令牌或用户资料。"""
    if result not in ('success', 'failed', 'identity_mismatch'):
        result = 'failed'
    request_id = request_id if re.fullmatch(r'[a-f0-9]{32}', request_id or '') else ''
    nonce = secrets.token_urlsafe(24)
    payload = json.dumps({'type': MESSAGE_TYPE, 'request_id': request_id, 'status': result})
    html = ('<!doctype html><html><head><meta charset="utf-8"><title>模型授权恢复</title></head>'
            '<body><script nonce="' + nonce + '">window.parent.postMessage(' + payload + ',' +
            json.dumps(CHAT_ORIGIN) + ');</script></body></html>')
    return HTMLResponse(html, headers={
        'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': "default-src 'none'; script-src 'nonce-" + nonce + "'; frame-ancestors 'self'; base-uri 'none'",
        'X-Content-Type-Options': 'nosniff',
    })


async def prepare_model_recovery(request, user, manager):
    """真人 Bearer 与当前 Cookie 必须为同一已验证用户；五秒内建立原生 state。"""
    try:
        async with asyncio.timeout(PREPARE_TIMEOUT):
            authorization = request.headers.get('Authorization', '').split()
            if (request.headers.get('Origin') != CHAT_ORIGIN or len(authorization) != 2 or
                    authorization[0].lower() != 'bearer' or request.state.auth_type != 'jwt' or
                    request.state.claims.get('typ', 'session') != 'session'):
                raise HTTPException(403, detail={'code': 'model_recovery_not_allowed'})
            cookie_user = await get_verified_user_by_token(
                request.cookies.get('token', ''), getattr(request.app.state, 'redis', None))
            if not cookie_user or cookie_user.id != user.id:
                raise HTTPException(403, detail={'code': 'model_recovery_identity_mismatch'})
            provider_oauth = user.oauth.get('oidc') if isinstance(user.oauth, dict) else None
            if not isinstance(provider_oauth, dict) or not provider_oauth.get('sub'):
                raise HTTPException(403, detail={'code': 'model_recovery_not_allowed'})
            config = await get_oauth_runtime_config()
            client = create_model_recovery_client(manager)
            if not config.ENABLE_OAUTH or client is None:
                raise HTTPException(503, detail={'code': 'model_recovery_unavailable'})
            metadata = await client.load_server_metadata()
            if metadata.get('issuer') != ISSUER or metadata.get('authorization_endpoint') != AUTHORIZATION_ENDPOINT:
                raise HTTPException(503, detail={'code': 'model_recovery_unavailable'})
            redirect_uri = str((client.server_metadata or {}).get('redirect_uri') or
                               request.url_for('oauth_login_callback', provider='oidc'))
            if redirect_uri != CALLBACK_URL:
                raise HTTPException(503, detail={'code': 'model_recovery_unavailable'})
            request_id = secrets.token_hex(16)
            state = STATE_PREFIX + request_id
            parameters = {'audience': config.OAUTH_AUDIENCE} if config.OAUTH_AUDIENCE else {}
            if OAUTH_AUTHORIZE_PARAMS:
                parameters.update(OAUTH_AUTHORIZE_PARAMS)
            # 沿用现有 scope、prompt、PKCE 和 nonce；只将一次恢复与已验证的 Chat 用户绑定。
            parameters['state'] = state
            auth_data = await client.create_authorization_url(redirect_uri, **parameters)
            location = auth_data.pop('url')
            parsed = urlsplit(location)
            query = parse_qs(parsed.query)
            if (parsed.scheme + '://' + parsed.netloc + parsed.path != AUTHORIZATION_ENDPOINT or
                    parsed.fragment or parsed.username or parsed.password or auth_data.get('state') != state or
                    query.get('state') != [state] or query.get('redirect_uri') != [CALLBACK_URL]):
                raise HTTPException(503, detail={'code': 'model_recovery_unavailable'})
            auth_data[STATE_DATA_KEY] = {'user_id': user.id, 'request_id': request_id, 'created_at': int(time.time())}
            await client.save_authorize_data(request, redirect_uri=redirect_uri, **auth_data)
            return JSONResponse({'authorization_url': location, 'request_id': request_id}, headers={'Cache-Control': 'no-store'})
    except HTTPException:
        raise
    except Exception:
        # 不记录授权 URL、Token 或异常原文；调用方只能看到明确的准备失败。
        log.warning('model_authorization_recovery_failed phase=prepare')
        raise HTTPException(503, detail={'code': 'model_recovery_unavailable'}) from None


async def load_model_recovery_context(request, client):
    """在 Authlib 消费 state 前读取其元数据；缺失、过期和错配不得降为普通登录。"""
    state = request.query_params.get('state', '')
    request_id = state[len(STATE_PREFIX):]
    context = {'request_id': request_id, 'valid': False}
    if not re.fullmatch(r'[a-f0-9]{32}', request_id) or client is None:
        return context
    data = await client.framework.get_state_data(request.session, state)
    recovery = data.get(STATE_DATA_KEY) if isinstance(data, dict) else None
    if not isinstance(recovery, dict):
        return context
    created = recovery.get('created_at')
    if (recovery.get('request_id') != request_id or not isinstance(recovery.get('user_id'), str) or
            not recovery['user_id'] or type(created) is not int or not 0 <= time.time() - created <= STATE_TTL):
        return context
    return {**context, 'valid': True, 'user_id': recovery['user_id']}


async def finish_model_recovery(request, context, sub, token, db=None):
    """可信 sub 与仍有效的原 Chat Cookie 同时匹配后，只保存该用户的 OAuth session。"""
    try:
        user = await get_verified_user_by_token(
            request.cookies.get('token', ''), getattr(request.app.state, 'redis', None))
        oauth = user.oauth.get('oidc') if user and isinstance(user.oauth, dict) else None
        if (not user or user.id != context['user_id'] or not isinstance(oauth, dict) or
                str(oauth.get('sub', '')) != sub):
            return recovery_result(context['request_id'], 'identity_mismatch')
        # 不执行用户资料/角色变更、开户、MFA 或 Chat JWT 签发；缺刷新凭据不能冒充长期恢复成功。
        if not isinstance(token, dict) or not token.get('access_token') or not token.get('refresh_token'):
            return recovery_result(context['request_id'], 'failed')
        _normalize_token_expiry(token)
        session = await OAuthSessions.create_session(user_id=user.id, provider='oidc', token=token, db=db)
        if not session or not session.id:
            return recovery_result(context['request_id'], 'failed')
        # 先保证新会话落盘，再沿原生上限清理同提供方旧会话，不预先破坏其他设备可用授权。
        sessions = await OAuthSessions.get_sessions_by_user_id(user.id, db=db)
        previous = sorted((item for item in sessions if item.provider == 'oidc' and item.id != session.id),
                          key=lambda item: item.created_at, reverse=True)
        for old in previous[max(0, OAUTH_MAX_SESSIONS_PER_USER - 1):]:
            if not await OAuthSessions.delete_session_by_id(old.id, db=db):
                return recovery_result(context['request_id'], 'failed')
        response = recovery_result(context['request_id'], 'success')
        response.set_cookie('oauth_session_id', session.id, httponly=True, secure=WEBUI_AUTH_COOKIE_SECURE,
                            samesite=WEBUI_AUTH_COOKIE_SAME_SITE)
        return response
    except Exception:
        log.warning('model_authorization_recovery_failed phase=persist')
        return recovery_result(context['request_id'], 'failed')


async def run_model_recovery_callback(request, provider, response, manager, db=None):
    """包括原生 token/UserInfo 与 state 清理的十五秒总限时；失败不签发登录信息。"""
    state = request.query_params.get('state', '')
    request_id = state[len(STATE_PREFIX):]
    try:
        async with asyncio.timeout(CALLBACK_TIMEOUT):
            client = create_model_recovery_client(manager)
            try:
                return await manager.handle_callback(request, provider, response, db=db, model_recovery_client=client)
            finally:
                if client is not None:
                    await client.framework.clear_state_data(request.session, state)
    except Exception:
        log.warning('model_authorization_recovery_failed phase=callback')
        return recovery_result(request_id, 'failed')
