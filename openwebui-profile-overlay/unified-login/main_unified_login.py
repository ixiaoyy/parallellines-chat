"""主站统一浏览器登录：绑定单个 Chat Web JWT，不改变 API key、角色或其它设备。"""
import asyncio
import ipaddress
import json
import os
import re
import time
from urllib.parse import parse_qs, urlsplit

from aiohttp import BasicAuth, ClientSession, ClientTimeout
from fastapi import HTTPException
from starlette.responses import JSONResponse

from open_webui.config import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
from open_webui.models.config import Config
from open_webui.models.oauth_sessions import OAuthSessions

ISSUER = 'https://pingxingxian.space/auth/oidc'
CLIENT = 'parallellines-openwebui'
CHAT = 'https://chat.pingxingxian.space'
BINDING_KEY = '_pl_unified_browser'
JWT_KEY = 'pl_unified'
OPAQUE = re.compile(r'^[A-Za-z0-9_-]{32,128}$')


def enabled():
    """仅显式开启新 Web 登录合同；旧未启用部署保持原行为。"""
    return os.environ.get('MAIN_UNIFIED_LOGIN_ENABLED') == 'true'


def denied(code='main_unified_session_invalid', status=401):
    """稳定失败码区分真实会话失效与主站不可用，不泄露凭据或异常原文。"""
    return HTTPException(status, detail={'code': code}, headers={'Cache-Control': 'no-store'})


def binding_from_userinfo(info):
    """只接受标准 OIDC 回调已验证的固定发行方/本客户端/中央浏览器证明。"""
    if (not isinstance(info, dict) or info.get('iss') != ISSUER or info.get('aud') != CLIENT
            or info.get('pl_unified_browser') is not True or not isinstance(info.get('sub'), str)
            or not 1 <= len(info['sub']) <= 128 or not isinstance(info.get('pl_binding'), str)
            or not OPAQUE.fullmatch(info['pl_binding']) or type(info.get('pl_auth_exp')) is not int
            or info['pl_auth_exp'] <= time.time()):
        raise denied('main_unified_reconnect_required')
    return {'subject': info['sub'], 'binding_id': info['pl_binding'],
            'auth_expires_at': info['pl_auth_exp'], 'unified_browser': True}


async def attach_login_binding(provider, token, userinfo):
    """签发 Chat 凭据前保存可信 Main 上下文；不以旧 JWT 或前端字段升级旧绑定。"""
    if enabled() and provider == 'oidc':
        token[BINDING_KEY] = binding_from_userinfo(userinfo)


def preserve_binding(old_token, new_token):
    """原生刷新只更换 OAuth token 时保留服务端绑定，不能被刷新响应自行重写。"""
    if BINDING_KEY in old_token:
        new_token[BINDING_KEY] = old_token[BINDING_KEY]
    return new_token


async def session_claims(user, session_id):
    """新 Web JWT 签入具体 OAuthSession 与 Main 绑定；无需新增表或改现有用户 ID。"""
    session = await binding_session(session_id)
    value = session.token.get(BINDING_KEY) if session and session.provider == 'oidc' and session.user_id == user.id else None
    oauth = user.oauth.get('oidc') if isinstance(user.oauth, dict) else None
    if (not isinstance(value, dict) or value.get('unified_browser') is not True
            or not isinstance(oauth, dict) or oauth.get('sub') != value.get('subject')
            or type(value.get('auth_expires_at')) is not int or value['auth_expires_at'] <= time.time()
            or not isinstance(value.get('binding_id'), str) or not OPAQUE.fullmatch(value['binding_id'])):
        raise denied('main_unified_reconnect_required')
    return {JWT_KEY: {**value, 'oauth_session_id': session_id}}


def recovery_transport_allowed(request):
    """应急通道只接受显式开启的真实 loopback 请求；部署须关闭代理头改写并在公网代理拒绝入口。"""
    if os.environ.get('MAIN_UNIFIED_RECOVERY_ENABLED') != 'true' or request is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError, TypeError):
        return False


def recovery_allowed(user, request):
    """稳定本地管理员 ID 明确入列才可恢复；是否曾绑定 OIDC 不参与授权。"""
    if not recovery_transport_allowed(request) or user is None or user.role != 'admin':
        return False
    try:
        raw = os.environ.get('MAIN_UNIFIED_RECOVERY_ADMIN_IDS', '[]')
        if len(raw) > 4096:
            return False
        allowed = json.loads(raw)
        return (isinstance(allowed, list) and 1 <= len(allowed) <= 32
                and all(isinstance(value, str) and 1 <= len(value) <= 128 for value in allowed)
                and user.id in allowed)
    except (ValueError, TypeError):
        return False


async def binding_session(session_id):
    """严格区分合法剪枝后的缺行与数据库/解密失败，后者不可伪装为可继续的缺行。"""
    try:
        return await OAuthSessions.get_session_by_id_strict(session_id)
    except Exception:
        raise denied('main_unified_authority_unavailable', 503) from None


async def rpc(operation, binding):
    """仅用本 RP 的原生独立凭据请求固定 issuer，两秒连接/四秒总限时、无重试。"""
    if operation not in ('main-session-state', 'unified-logout-ticket'):
        raise denied(status=503)
    values = await Config.get_many('oauth.client_id', 'oauth.client_secret')
    client = values.get('oauth.client_id', OAUTH_CLIENT_ID)
    secret = values.get('oauth.client_secret', OAUTH_CLIENT_SECRET)
    if client != CLIENT or not isinstance(secret, str) or len(secret) < 32:
        raise denied('main_unified_authority_unavailable', 503)
    try:
        async with asyncio.timeout(4), ClientSession(timeout=ClientTimeout(total=4, connect=2), trust_env=True) as session:
            async with session.post(ISSUER + '/' + operation, allow_redirects=False,
                                    auth=BasicAuth(client, secret), json={'binding_id': binding['binding_id'], 'subject': binding['subject']}) as response:
                if response.status != 200:
                    raise denied('main_unified_authority_unavailable', 503)
                # 读到响应结束再解析，分块传输也受同一总超时与 16 KiB 总上限约束。
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(4096):
                    size += len(chunk)
                    if size > 16384:
                        raise denied('main_unified_authority_unavailable', 503)
                    chunks.append(chunk)
                content = b''.join(chunks)
                value = json.loads(content)
                if not isinstance(value, dict) or type(value.get('active')) is not bool:
                    raise denied('main_unified_authority_unavailable', 503)
                return value
    except HTTPException:
        raise
    except Exception:
        raise denied('main_unified_authority_unavailable', 503) from None


async def verify_web_session(user, claims, request=None):
    """每次私有 Web 访问实时核 Main sid，不缓存正向结果、不执行账户级撤销。"""
    if not enabled() or claims.get('typ', 'session') != 'session':
        return
    if claims.get('auth_method') != 'oauth':
        if recovery_allowed(user, request):
            return
        raise denied('main_unified_login_required')
    binding = claims.get(JWT_KEY)
    oauth = user.oauth.get('oidc') if isinstance(user.oauth, dict) else None
    if (not isinstance(binding, dict) or binding.get('unified_browser') is not True
            or not isinstance(binding.get('oauth_session_id'), str) or not isinstance(oauth, dict)
            or oauth.get('sub') != binding.get('subject') or type(binding.get('auth_expires_at')) is not int
            or binding['auth_expires_at'] <= time.time()):
        raise denied('main_unified_reconnect_required')
    if (not isinstance(binding.get('binding_id'), str) or not OPAQUE.fullmatch(binding['binding_id'])
            or not isinstance(binding.get('subject'), str) or not 1 <= len(binding['subject']) <= 128):
        raise denied()
    row = await binding_session(binding['oauth_session_id'])
    if row is not None:
        stored = row.token.get(BINDING_KEY)
        if row.user_id != user.id or row.provider != 'oidc' or stored != {key: value for key, value in binding.items() if key != 'oauth_session_id'}:
            raise denied()
    # 行被原生合法剪枝不等于 Main 退出；签名 JWT 的绑定仍须由 Main 每请求实际核验。
    live = await rpc('main-session-state', binding)
    if live.get('active') is False:
        raise denied()
    if (live.get('sub') != binding['subject'] or live.get('binding_id') != binding.get('binding_id')
            or live.get('unified_browser') is not True or type(live.get('auth_expires_at')) is not int):
        raise denied('main_unified_authority_unavailable', 503)
    if live['auth_expires_at'] <= time.time():
        raise denied()


def delete_browser_cookies(response):
    """只删除当前 Chat host 的会话 Cookie，不触碰主站 Cookie 或其它设备。"""
    for name in ('token', 'oauth_session_id', 'oauth_id_token', 'owui-session', 'oui-session'):
        response.delete_cookie(name, path='/')


async def local_identity(request):
    """本地签名/账户撤销校验用于清理已被 Main 撤销的当前浏览器，不先要求 Main 活跃。"""
    from open_webui.utils.auth import decode_token, is_valid_token
    from open_webui.models.users import Users
    value = request.headers.get('Authorization', '').split()
    token = value[1] if len(value) == 2 and value[0].lower() == 'bearer' else request.cookies.get('token', '')
    claims = decode_token(token) if token else None
    if not claims or claims.get('typ', 'session') != 'session' or not await is_valid_token(claims, getattr(request.app.state, 'redis', None)):
        return None, None
    return await Users.get_user_by_id(claims.get('id', '')), claims


async def local_signout(request):
    """仅清当前 JWT 关联的 OAuth 行与 host Cookie；用于确认主站退出/换号后的本地清理。"""
    if not enabled() or request.headers.get('Origin') != CHAT:
        raise denied('main_unified_origin_denied', 403)
    user, claims = await local_identity(request)
    binding = claims.get(JWT_KEY) if claims else None
    if user and isinstance(binding, dict):
        row = await binding_session(binding.get('oauth_session_id'))
        if row and row.user_id == user.id and row.provider == 'oidc' and not await OAuthSessions.delete_session_by_id(row.id):
            raise denied('main_unified_local_cleanup_failed', 503)
    response = JSONResponse({'status': True}, headers={'Cache-Control': 'no-store'})
    delete_browser_cookies(response)
    return response


async def unified_signout(request):
    """用户退出先取得原浏览器 sid 的一次票据；上游不可用不假装全站退出。"""
    if request.headers.get('Origin') != CHAT:
        raise denied('main_unified_origin_denied', 403)
    user, claims = await local_identity(request)
    if user is None:
        response = JSONResponse({'status': True, 'redirect_url': CHAT + '/'})
        delete_browser_cookies(response)
        return response
    if claims.get('auth_method') != 'oauth' and recovery_allowed(user, request):
        return None
    binding = claims.get(JWT_KEY)
    if not isinstance(binding, dict) or binding.get('unified_browser') is not True:
        raise denied('main_unified_reconnect_required', 409)
    result = await rpc('unified-logout-ticket', binding)
    if not result['active']:
        response = JSONResponse({'status': True, 'redirect_url': CHAT + '/'})
        delete_browser_cookies(response)
        return response
    parsed = urlsplit(result.get('logout_url', ''))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != 'https' or parsed.netloc != 'pingxingxian.space'
            or parsed.path != '/auth/unified/logout' or parsed.fragment or result.get('expires_in') != 60
            or list(query) != ['ticket'] or len(query['ticket']) != 1 or not OPAQUE.fullmatch(query['ticket'][0])):
        raise denied('main_unified_authority_unavailable', 503)
    return JSONResponse({'status': True, 'redirect_url': result['logout_url']}, headers={'Cache-Control': 'no-store'})
