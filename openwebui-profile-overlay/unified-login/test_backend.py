"""隔离合同测试只加载自有模块；不连接真实 OWUI/Main、数据库、模型或凭据。"""
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

HERE = Path(__file__).parent
def load_helper():
    """未安装的网络依赖用不可发请求的桩替代；测试不把桩当真实 HTTP 验收。"""
    modules = {}
    for name in ['aiohttp', 'open_webui', 'open_webui.config', 'open_webui.models',
                 'open_webui.models.config', 'open_webui.models.oauth_sessions']:
        modules[name] = types.ModuleType(name)
    modules['aiohttp'].BasicAuth = object
    modules['aiohttp'].ClientSession = object
    modules['aiohttp'].ClientTimeout = object
    modules['open_webui.config'].OAUTH_CLIENT_ID = 'parallellines-openwebui'
    modules['open_webui.config'].OAUTH_CLIENT_SECRET = ''
    modules['open_webui.models.config'].Config = types.SimpleNamespace(get_many=AsyncMock(return_value={}))
    modules['open_webui.models.oauth_sessions'].OAuthSessions = types.SimpleNamespace(
        get_session_by_id_strict=AsyncMock(return_value=None), delete_session_by_id=AsyncMock(return_value=True))
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location('main_unified_login_isolated', HERE/'main_unified_login.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module


class UnifiedBackend(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'MAIN_UNIFIED_LOGIN_ENABLED': 'true', 'MAIN_UNIFIED_RECOVERY_ENABLED': 'false'}, clear=False)
        self.env.start(); self.addCleanup(self.env.stop)
        self.m = load_helper()
        self.proof = {'subject': 'main-A', 'binding_id': 'b'*43, 'auth_expires_at': 4102444800, 'unified_browser': True}
        self.claims = {'typ': 'session', 'auth_method': 'oauth', self.m.JWT_KEY: {**self.proof, 'oauth_session_id': 'oauth-row-A'}}
        self.user = types.SimpleNamespace(id='local-A', role='user', oauth={'oidc': {'sub': 'main-A'}})
        self.live = {'active': True, 'sub': 'main-A', 'binding_id': 'b'*43, 'auth_expires_at': 4102444800, 'unified_browser': True}
        self.m.rpc = AsyncMock(return_value=self.live)

    async def test_pruned_oauth_row_keeps_valid_web_session_but_requires_main_each_request(self):
        await self.m.verify_web_session(self.user, self.claims)
        await self.m.verify_web_session(self.user, self.claims)
        self.assertEqual(self.m.rpc.await_count, 2)
        self.assertEqual(self.m.OAuthSessions.delete_session_by_id.await_count, 0)

    async def test_row_owner_metadata_and_local_stable_sub_are_mandatory(self):
        for changes in [{'user_id': 'other'}, {'provider': 'other'}, {'token': {self.m.BINDING_KEY: {}}}]:
            row = {'id': 'oauth-row-A', 'user_id': 'local-A', 'provider': 'oidc', 'token': {self.m.BINDING_KEY: self.proof}, **changes}
            self.m.OAuthSessions.get_session_by_id_strict.return_value = types.SimpleNamespace(**row)
            with self.assertRaises(HTTPException) as caught: await self.m.verify_web_session(self.user, self.claims)
            self.assertEqual(caught.exception.status_code, 401)
        self.m.OAuthSessions.get_session_by_id_strict.return_value = None
        self.user.oauth = {'oidc': {'sub': 'main-B'}}
        with self.assertRaises(HTTPException): await self.m.verify_web_session(self.user, self.claims)
        self.assertEqual(self.m.rpc.await_count, 0)

    async def test_db_failure_and_main_outage_are_503_not_missing_or_logout(self):
        self.m.OAuthSessions.get_session_by_id_strict.side_effect = RuntimeError('synthetic db error')
        with self.assertRaises(HTTPException) as caught: await self.m.verify_web_session(self.user, self.claims)
        self.assertEqual(caught.exception.status_code, 503)
        self.m.OAuthSessions.get_session_by_id_strict.side_effect = None
        self.m.rpc.side_effect = self.m.denied('main_unified_authority_unavailable', 503)
        with self.assertRaises(HTTPException) as caught: await self.m.verify_web_session(self.user, self.claims)
        self.assertEqual(caught.exception.status_code, 503)

    async def test_legacy_and_main_inactive_rejected_automation_unchanged(self):
        with self.assertRaises(HTTPException): await self.m.verify_web_session(self.user, {'typ': 'session', 'auth_method': 'oauth'})
        self.m.rpc.return_value = {'active': False}
        with self.assertRaises(HTTPException) as caught: await self.m.verify_web_session(self.user, self.claims)
        self.assertEqual(caught.exception.status_code, 401)
        self.m.rpc.reset_mock()
        await self.m.verify_web_session(self.user, {'typ': 'automation'})
        self.m.rpc.assert_not_awaited()
        with patch.dict(os.environ, {'MAIN_UNIFIED_LOGIN_ENABLED': 'false'}):
            await self.m.verify_web_session(self.user, {'auth_method': 'password'})

    async def test_userinfo_claims_only_trusted_cookie_binding_and_metadata_stays_encrypted_json(self):
        info = {'iss': self.m.ISSUER, 'aud': self.m.CLIENT, 'sub': 'main-A', 'pl_binding': 'b'*43, 'pl_auth_exp': 4102444800, 'pl_unified_browser': True}
        self.assertEqual(self.m.binding_from_userinfo(info), self.proof)
        for key, value in [('iss', 'https://evil.invalid'), ('aud', 'parallellines-newapi'), ('pl_unified_browser', False), ('pl_auth_exp', True), ('pl_binding', '.'*43)]:
            with self.assertRaises(HTTPException): self.m.binding_from_userinfo({**info, key: value})
        token = {}; await self.m.attach_login_binding('oidc', token, info)
        self.assertEqual(token[self.m.BINDING_KEY], self.proof)
        updated = self.m.preserve_binding(token, {'access_token': 'synthetic-new', self.m.BINDING_KEY: {}})
        self.assertEqual(updated[self.m.BINDING_KEY], self.proof)
        self.m.OAuthSessions.get_session_by_id_strict.return_value = types.SimpleNamespace(user_id='local-A', provider='oidc', token=token)
        self.assertEqual((await self.m.session_claims(self.user, 'oauth-row-A'))[self.m.JWT_KEY]['oauth_session_id'], 'oauth-row-A')

    async def test_recovery_requires_explicit_id_and_loopback_not_oidc_absence_or_forwarded_ip(self):
        self.user.role = 'admin'
        loopback = types.SimpleNamespace(client=types.SimpleNamespace(host='127.0.0.1'))
        remote = types.SimpleNamespace(client=types.SimpleNamespace(host='203.0.113.5'), headers={'X-Forwarded-For': '127.0.0.1'})
        self.assertFalse(self.m.recovery_allowed(self.user, loopback))
        with patch.dict(os.environ, {'MAIN_UNIFIED_RECOVERY_ENABLED': 'true', 'MAIN_UNIFIED_RECOVERY_ADMIN_IDS': '["local-A"]'}):
            self.assertTrue(self.m.recovery_allowed(self.user, loopback))
            self.assertFalse(self.m.recovery_allowed(self.user, remote))
            await self.m.verify_web_session(self.user, {'typ': 'session', 'auth_method': 'password'}, loopback)
            self.user.id = 'not-allowlisted'; self.user.oauth = {}
            self.assertFalse(self.m.recovery_allowed(self.user, loopback))

    async def test_logout_fixed_ticket_origin_outage_and_current_row_only(self):
        request = types.SimpleNamespace(headers={'Origin': self.m.CHAT})
        self.m.local_identity = AsyncMock(return_value=(self.user, self.claims))
        self.m.rpc.return_value = {'active': True, 'logout_url': 'https://pingxingxian.space/auth/unified/logout?ticket='+'t'*43, 'expires_in': 60}
        response = await self.m.unified_signout(request)
        self.assertEqual(response.status_code, 200)
        self.m.OAuthSessions.delete_session_by_id.assert_not_awaited()
        self.m.OAuthSessions.get_session_by_id_strict.return_value = types.SimpleNamespace(id='oauth-row-A', user_id='local-A', provider='oidc')
        response = await self.m.local_signout(request)
        self.m.OAuthSessions.delete_session_by_id.assert_awaited_once_with('oauth-row-A')
        self.assertTrue(any(name == b'set-cookie' and b'token=' in value for name, value in response.raw_headers))
        request.headers['Origin'] = 'https://evil.invalid'
        with self.assertRaises(HTTPException) as caught: await self.m.unified_signout(request)
        self.assertEqual(caught.exception.status_code, 403)

    async def test_rpc_reads_complete_bounded_stream_without_retry_or_redirect(self):
        m = load_helper()
        m.Config.get_many.return_value = {'oauth.client_id': m.CLIENT, 'oauth.client_secret': 's'*40}
        m.BasicAuth = lambda user, password: (user, password)
        m.ClientTimeout = lambda **values: values
        calls = []
        class Context:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
        class Response(Context):
            status = 200
            def __init__(self, chunks): self.chunks = chunks; self.content = self
            async def iter_chunked(self, size):
                self.chunk_size = size
                for value in self.chunks: yield value
        response = Response([b'{"active":', b'true}'])
        class Session(Context):
            def post(self, url, **kwargs): calls.append((url, kwargs)); return response
        m.ClientSession = lambda **kwargs: Session()
        self.assertEqual(await m.rpc('main-session-state', self.proof), {'active': True})
        self.assertEqual(calls[0][0], m.ISSUER+'/main-session-state')
        self.assertFalse(calls[0][1]['allow_redirects'])
        self.assertEqual(calls[0][1]['json'], {'binding_id': 'b'*43, 'subject': 'main-A'})
        for chunks in [[b'{"active":true}', b'unexpected tail'], [b'x'*16385], [b'{"active":1}']]:
            response = Response(chunks)
            with self.assertRaises(HTTPException) as caught: await m.rpc('main-session-state', self.proof)
            self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(len(calls), 4)


if __name__ == '__main__': unittest.main()
