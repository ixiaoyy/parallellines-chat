"""只用替身验证实际 helper 和已补丁回调，不加载应用、数据库或外部身份服务。"""
import ast
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

from fastapi import HTTPException
from apply_backend import apply, patch_main, patch_oauth

FEATURE = Path(__file__).parent
BASELINE = Path(os.environ.get('MODEL_RECOVERY_BASELINES', FEATURE.parents[1] / '.build/chat-auto-recovery-20261112/baselines'))


def stub_module(name, **values):
    module = ModuleType(name)
    module.__dict__.update(values)
    return module


def load_helper():
    class FakeIntegration:
        instances = []

        def __init__(self, name, cache=None):
            self.name = name
            self.cache = cache
            self.clear_count = 0
            self.instances.append(self)

        def key(self, state):
            return f'_state_{self.name}_{state}'

        async def set_state_data(self, session, state, data):
            # 模拟已核实 Authlib 的同 namespace 清旧 state 合同。
            prefix = f'_state_{self.name}_'
            for key in list(session):
                if key.startswith(prefix): session.pop(key)
            session[self.key(state)] = data

        async def get_state_data(self, session, state):
            return session.get(self.key(state))

        async def clear_state_data(self, session, state):
            self.clear_count += 1
            session.pop(self.key(state), None)

    dependencies = {
        'authlib.integrations.starlette_client.integration': stub_module('integration', StarletteIntegration=FakeIntegration),
        'open_webui.config': stub_module('config', OAUTH_AUTHORIZE_PARAMS={'prompt': 'consent'}),
        'open_webui.env': stub_module('env', OAUTH_MAX_SESSIONS_PER_USER=2,
                                     WEBUI_AUTH_COOKIE_SECURE=True, WEBUI_AUTH_COOKIE_SAME_SITE='lax'),
        'open_webui.models.oauth_sessions': stub_module('sessions', OAuthSessions=SimpleNamespace()),
        'open_webui.utils.auth': stub_module('auth', get_verified_user_by_token=AsyncMock()),
        'open_webui.utils.oauth': stub_module('oauth', _normalize_token_expiry=lambda token: token.update(expires_at=2_000_000_000),
                                            get_oauth_runtime_config=AsyncMock()),
    }
    with patch.dict(sys.modules, dependencies):
        spec = importlib.util.spec_from_file_location('silent_helper_under_test', FEATURE.parent / 'main_model_silent_recovery.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def status_of(response):
    match = re.search(r'window.parent.postMessage\((\{.*?\}),', response.body.decode())
    return json.loads(match.group(1))['status']


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = load_helper()
        self.user = SimpleNamespace(id='chat-a', role='user', oauth={'oidc': {'sub': 'main-a'}})
        self.h.get_verified_user_by_token = AsyncMock(return_value=self.user)
        self.h.get_oauth_runtime_config = AsyncMock(return_value=SimpleNamespace(
            ENABLE_OAUTH=True, OAUTH_AUDIENCE='', OAUTH_SUB_CLAIM='',
            OAUTH_EMAIL_CLAIM='email', OAUTH_USERNAME_CLAIM='name'))
        self.request = SimpleNamespace(
            headers={'Origin': self.h.CHAT_ORIGIN, 'Authorization': 'Bearer synthetic-chat-jwt'},
            cookies={'token': 'synthetic-chat-jwt'}, state=SimpleNamespace(auth_type='jwt', claims={'typ': 'session'}),
            app=SimpleNamespace(state=SimpleNamespace(redis=object())), session={}, query_params={},
            url_for=lambda *args, **kwargs: self.h.CALLBACK_URL)
        integration = self.h.StarletteIntegration

        class NativeClient:
            async def create_authorization_url(client, redirect_uri, **params):
                return await client.create_url_mock(redirect_uri, **params)

            async def save_authorize_data(client, request, **data):
                await client.save_mock(request, **data)
                await client.framework.set_state_data(request.session, data['state'], data)

            async def authorize_access_token(client, request, **kwargs):
                state = request.query_params.get('state')
                if not await client.framework.get_state_data(request.session, state):
                    raise HTTPException(400, 'native namespace state rejected')
                await client.framework.clear_state_data(request.session, state)
                return await client.exchange_mock(request, **kwargs)

        self.client = NativeClient()
        self.client.server_metadata = {'redirect_uri': self.h.CALLBACK_URL}
        self.client.framework = integration('oidc')
        self.client.load_server_metadata = AsyncMock(return_value={
            'issuer': self.h.ISSUER, 'authorization_endpoint': self.h.AUTHORIZATION_ENDPOINT})

        async def create_url(redirect_uri, **params):
            return {'state': params['state'], 'nonce': 'synthetic-nonce', 'code_verifier': 'synthetic-pkce',
                    'url': self.h.AUTHORIZATION_ENDPOINT + '?' + urlencode({
                        **params, 'redirect_uri': redirect_uri, 'code_challenge_method': 'S256'})}

        self.client.create_url_mock = AsyncMock(side_effect=create_url)
        self.client.save_mock = AsyncMock()
        self.client.exchange_mock = AsyncMock(return_value={
            'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
            'userinfo': {'sub': 'main-a', 'name': 'A', 'email': 'a@example.invalid'}})
        self.client.userinfo = AsyncMock(side_effect=AssertionError('unexpected userinfo'))
        self.manager = SimpleNamespace(get_client=Mock(return_value=self.client))
        self.new_session = SimpleNamespace(id='new-oauth', user_id=self.user.id, provider='oidc', created_at=100)
        self.h.OAuthSessions = SimpleNamespace(
            create_session=AsyncMock(return_value=self.new_session),
            get_sessions_by_user_id=AsyncMock(return_value=[self.new_session]),
            delete_session_by_id=AsyncMock(return_value=True))
        self.h.log = Mock()

    async def prepared(self):
        response = await self.h.prepare_model_recovery(self.request, self.user, self.manager)
        result = json.loads(response.body)
        self.request.query_params['state'] = self.h.STATE_PREFIX + result['request_id']
        context = await self.h.load_model_recovery_context(self.request, self.h.create_model_recovery_client(self.manager))
        return result, context

    def native_callback(self):
        source = patch_oauth((BASELINE / 'oauth.py').read_text(encoding='utf-8'))
        manager_class = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'OAuthManager')
        node = next(n for n in manager_class.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'handle_callback')
        for arg in node.args.args: arg.annotation = None
        forbidden = Mock(side_effect=AssertionError('ordinary login mutation must not run'))
        scope = {
            'get_oauth_runtime_config': self.h.get_oauth_runtime_config, 'OAUTH_PROVIDERS': {'oidc': {}},
            'OAUTH_ACCESS_TOKEN_REQUEST_INCLUDE_CLIENT_ID': False,
            'HTTPException': HTTPException, 'BadSignatureError': type('BadSignatureError', (Exception,), {}),
            'ERROR_MESSAGES': SimpleNamespace(OAUTH_LOGIN_FAILED='OAuth login failed'),
            'log': Mock(), '_build_oauth_callback_error_message': lambda error: 'fixed error',
            'Config': SimpleNamespace(get=forbidden), 'Users': forbidden, 'Auths': forbidden,
            'publish_event': forbidden, 'main_profile_sync_enabled': forbidden,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<patched-native-callback>', 'exec'), scope)
        self.callback_log = scope['log']
        return scope['handle_callback'], forbidden

    async def call_native(self):
        callback, forbidden = self.native_callback()
        with patch.dict(sys.modules, {'open_webui.utils.main_model_silent_recovery': self.h}):
            result = await callback(self.manager, self.request, 'oidc', object(), db='synthetic-db',
                                    model_recovery_client=self.h.create_model_recovery_client(self.manager))
        forbidden.assert_not_called()
        return result

    async def test_prepare_preserves_native_parameters_and_contains_no_credentials(self):
        result, context = await self.prepared()
        self.assertTrue(context['valid'])
        self.assertEqual(context['user_id'], self.user.id)
        args = self.client.create_url_mock.call_args
        self.assertEqual(args.kwargs['prompt'], 'consent')
        self.assertNotIn('scope', args.kwargs)
        state_data = next(iter(self.request.session.values()))
        self.assertEqual(state_data['code_verifier'], 'synthetic-pkce')
        self.assertEqual(state_data['nonce'], 'synthetic-nonce')
        self.assertNotIn('jwt', json.dumps(result))
        self.assertNotIn('jwt', json.dumps(state_data))
        self.assertNotIn('sub', state_data[self.h.STATE_DATA_KEY])

    async def test_recovery_namespace_is_unconsumable_after_rollback_and_preserves_parallel_login(self):
        await self.client.framework.set_state_data(self.request.session, 'ordinary-login', {'nonce': 'ordinary-nonce'})
        _result, context = await self.prepared()
        state = self.request.query_params['state']
        self.assertTrue(context['valid'])
        self.assertEqual(self.client.framework.name, 'oidc')
        self.assertIsNone(await self.client.framework.get_state_data(self.request.session, state))
        # 旧普通客户端只能找自己的 oidc namespace；找不到即拒绝，不能交换 Token 或进入签发。
        with self.assertRaises(HTTPException):
            await self.client.authorize_access_token(self.request)
        self.client.exchange_mock.assert_not_awaited()
        self.h.OAuthSessions.create_session.assert_not_awaited()
        self.assertEqual(await self.client.framework.get_state_data(self.request.session, 'ordinary-login'),
                         {'nonce': 'ordinary-nonce'})
        recovery_client = self.h.create_model_recovery_client(self.manager)
        self.assertEqual(recovery_client.framework.name, 'pl_model_oidc')
        self.assertEqual((await recovery_client.framework.get_state_data(self.request.session, state))[
            self.h.STATE_DATA_KEY]['user_id'], self.user.id)
        await recovery_client.authorize_access_token(self.request)
        self.client.exchange_mock.assert_awaited_once()
        self.assertIsNone(await recovery_client.framework.get_state_data(self.request.session, state))
        self.assertIsNotNone(await self.client.framework.get_state_data(self.request.session, 'ordinary-login'))

    async def test_prepare_rejects_cookie_bearer_conflict_api_key_and_cross_origin(self):
        cases = [
            ('cookie', None), ('cookie', SimpleNamespace(id='chat-b')),
            ('auth_type', 'api_key'), ('origin', 'https://untrusted.invalid'),
            ('authorization', ''), ('typ', 'api_key'),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                original = (self.h.get_verified_user_by_token.return_value, self.request.state.auth_type,
                            dict(self.request.headers), dict(self.request.state.claims))
                if name == 'cookie': self.h.get_verified_user_by_token.return_value = value
                if name == 'auth_type': self.request.state.auth_type = value
                if name == 'origin': self.request.headers['Origin'] = value
                if name == 'authorization': self.request.headers['Authorization'] = value
                if name == 'typ': self.request.state.claims['typ'] = value
                with self.assertRaises(HTTPException) as caught:
                    await self.h.prepare_model_recovery(self.request, self.user, self.manager)
                self.assertEqual(caught.exception.status_code, 403)
                self.h.get_verified_user_by_token.return_value, self.request.state.auth_type, self.request.headers, self.request.state.claims = original
        self.client.save_mock.assert_not_awaited()

    async def test_prepare_rejects_untrusted_discovery_and_has_total_timeout(self):
        self.client.load_server_metadata.return_value['issuer'] = 'https://untrusted.invalid'
        with self.assertRaises(HTTPException):
            await self.h.prepare_model_recovery(self.request, self.user, self.manager)
        self.client.load_server_metadata.side_effect = lambda: asyncio.sleep(1)
        # AsyncMock side_effect 的协程需要真正 await，不能把未等待对象当作 discovery 响应。
        async def stalled(): await asyncio.sleep(1)
        self.client.load_server_metadata.side_effect = stalled
        with patch.object(self.h, 'PREPARE_TIMEOUT', 0.01):
            with self.assertRaises(HTTPException) as caught:
                await self.h.prepare_model_recovery(self.request, self.user, self.manager)
        self.assertEqual(caught.exception.status_code, 503)
        self.client.save_mock.assert_not_awaited()

    async def test_missing_expired_or_wrong_state_never_becomes_ordinary_login(self):
        result, context = await self.prepared()
        self.assertTrue(context['valid'])
        key = '_state_' + self.h.STATE_NAMESPACE + '_' + self.request.query_params['state']
        data = self.request.session[key][self.h.STATE_DATA_KEY]
        for mutate in [lambda: data.update(created_at=int(time.time()) - 121),
                       lambda: data.update(created_at=int(time.time()) + 5),
                       lambda: data.update(request_id='b' * 32)]:
            data.update(created_at=int(time.time()), request_id=result['request_id'])
            mutate()
            response = await self.call_native()
            self.assertEqual(status_of(response), 'failed')
        self.request.session.clear()
        self.assertEqual(status_of(await self.call_native()), 'failed')
        self.client.exchange_mock.assert_not_awaited()
        self.h.OAuthSessions.create_session.assert_not_awaited()

    async def test_actual_native_callback_matches_subject_before_any_mutation(self):
        await self.prepared()
        self.user.oauth['oidc']['sub'] = 'main-b'
        response = await self.call_native()
        self.assertEqual(status_of(response), 'identity_mismatch')
        self.h.OAuthSessions.create_session.assert_not_awaited()
        self.assertNotIn('set-cookie', response.headers)

    async def test_recovery_missing_subject_fails_without_logging_userinfo(self):
        await self.prepared()
        self.client.exchange_mock.side_effect = None
        self.client.exchange_mock.return_value = {
            'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
            'userinfo': {'name': 'private-name', 'email': 'private@example.invalid'}}
        response = await self.call_native()
        self.assertEqual(status_of(response), 'failed')
        self.callback_log.warning.assert_not_called()
        self.callback_log.error.assert_not_called()
        self.h.OAuthSessions.create_session.assert_not_awaited()

    async def test_recovery_token_and_userinfo_errors_never_expand_private_diagnostics(self):
        await self.prepared()
        self.client.exchange_mock.side_effect = RuntimeError('private token detail')
        response = await self.call_native()
        self.assertEqual(status_of(response), 'failed')
        self.callback_log.warning.assert_not_called()
        self.callback_log.error.assert_not_called()
        self.client.exchange_mock.side_effect = None
        self.client.exchange_mock.return_value = {'access_token': 'synthetic-access'}
        self.client.userinfo.side_effect = RuntimeError('private userinfo detail')
        response = await self.call_native()
        self.assertEqual(status_of(response), 'failed')
        self.callback_log.warning.assert_not_called()
        self.callback_log.error.assert_not_called()
        self.h.OAuthSessions.create_session.assert_not_awaited()

    async def test_current_cookie_must_remain_valid_and_same_user(self):
        for user in [None, SimpleNamespace(id='chat-b', oauth={'oidc': {'sub': 'main-a'}})]:
            await self.prepared()
            self.h.get_verified_user_by_token.return_value = user
            response = await self.call_native()
            self.assertEqual(status_of(response), 'identity_mismatch')
            self.h.get_verified_user_by_token.return_value = self.user
        self.h.OAuthSessions.create_session.assert_not_awaited()

    async def test_actual_callback_saves_only_same_user_oauth_cookie_and_replay_fails(self):
        result, _ = await self.prepared()
        response = await self.call_native()
        self.assertEqual(status_of(response), 'success')
        self.assertEqual(self.h.OAuthSessions.create_session.call_args.kwargs['user_id'], self.user.id)
        cookies = response.headers.getlist('set-cookie')
        self.assertEqual(len(cookies), 1)
        self.assertTrue(cookies[0].startswith('oauth_session_id='))
        self.assertIn('HttpOnly', cookies[0])
        self.assertIn('Secure', cookies[0])
        self.assertNotIn('synthetic-access', response.body.decode())
        self.assertNotIn('synthetic-refresh', response.body.decode())
        self.assertEqual(status_of(await self.call_native()), 'failed')
        self.assertEqual(self.h.OAuthSessions.create_session.await_count, 1)

    async def test_missing_refresh_or_storage_failure_never_reports_success(self):
        _, context = await self.prepared()
        for token in [{}, {'access_token': 'synthetic-access'}]:
            response = await self.h.finish_model_recovery(self.request, context, 'main-a', token)
            self.assertEqual(status_of(response), 'failed')
        self.h.OAuthSessions.create_session.assert_not_awaited()
        self.h.OAuthSessions.create_session.return_value = None
        response = await self.h.finish_model_recovery(self.request, context, 'main-a',
                                                     {'access_token': 'a', 'refresh_token': 'r'})
        self.assertEqual(status_of(response), 'failed')
        self.assertNotIn('set-cookie', response.headers)
        self.h.OAuthSessions.create_session.side_effect = RuntimeError('private token diagnostic')
        response = await self.h.finish_model_recovery(self.request, context, 'main-a',
                                                     {'access_token': 'a', 'refresh_token': 'r'})
        self.assertEqual(status_of(response), 'failed')
        self.h.log.warning.assert_called_with('model_authorization_recovery_failed phase=persist')

    async def test_native_session_cap_is_applied_only_after_new_session_is_durable(self):
        _, context = await self.prepared()
        older = [SimpleNamespace(id='old-' + str(n), provider='oidc', created_at=n) for n in range(3)]
        self.h.OAuthSessions.get_sessions_by_user_id.return_value = [self.new_session, *older]
        response = await self.h.finish_model_recovery(self.request, context, 'main-a',
                                                     {'access_token': 'a', 'refresh_token': 'r'})
        self.assertEqual(status_of(response), 'success')
        self.assertEqual([call.args[0] for call in self.h.OAuthSessions.delete_session_by_id.call_args_list],
                         ['old-1', 'old-0'])

    async def test_callback_deadline_covers_token_userinfo_and_state_cleanup(self):
        result, _ = await self.prepared()
        async def stalled(*args, **kwargs): await asyncio.sleep(1)
        self.manager.handle_callback = AsyncMock(side_effect=stalled)
        with patch.object(self.h, 'CALLBACK_TIMEOUT', 0.01):
            response = await self.h.run_model_recovery_callback(self.request, 'oidc', object(), self.manager)
        self.assertEqual(status_of(response), 'failed')
        self.assertEqual(sum(item.clear_count for item in self.h.StarletteIntegration.instances), 1)
        self.h.OAuthSessions.create_session.assert_not_awaited()

    def test_result_html_has_strict_headers_and_fixed_parent_message(self):
        response = self.h.recovery_result('a' * 32, 'success')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertIn("frame-ancestors 'self'", response.headers['content-security-policy'])
        self.assertIn('nonce-', response.headers['content-security-policy'])
        self.assertIn(self.h.CHAT_ORIGIN, response.body.decode())
        self.assertNotIn('location', response.body.decode())
        for provider, state, expected in [('oidc', 'ordinary-state', False), ('github', 'plmr_a', False),
                                          ('oidc', 'plmr_' + 'a' * 32, True)]:
            self.request.query_params['state'] = state
            self.assertEqual(self.h.is_model_recovery_callback(self.request, provider), expected)


class PatchTests(unittest.TestCase):
    def test_patch_changes_only_named_methods_and_refuses_partial_or_duplicate_application(self):
        main = (BASELINE / 'main.py').read_text(encoding='utf-8')
        oauth = (BASELINE / 'oauth.py').read_text(encoding='utf-8')
        updated_main = patch_main(main)
        updated_oauth = patch_oauth(oauth)
        for old, new, changed in [(main, updated_main, {'oauth_login_callback'}),
                                  (oauth, updated_oauth, {'handle_callback'})]:
            old_functions = {(type(node).__name__, node.name, node.lineno): ast.get_source_segment(old, node)
                             for node in ast.walk(ast.parse(old)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            new_functions = {}
            for node in ast.walk(ast.parse(new)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    new_functions.setdefault((type(node).__name__, node.name), []).append(ast.get_source_segment(new, node))
            for (kind, name, _line), text in old_functions.items():
                if name not in changed:
                    self.assertIn(text, new_functions[(kind, name)], name)
        self.assertIn('user=Depends(get_verified_user)', updated_main)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'utils').mkdir()
            (root / 'main.py').write_text(main, encoding='utf-8', newline='\n')
            (root / 'utils/oauth.py').write_text(oauth, encoding='utf-8', newline='\n')
            apply(root, check=True)
            (root / 'utils/oauth.py').write_text(oauth + '\n', encoding='utf-8', newline='\n')
            with self.assertRaises(ValueError): apply(root)
            self.assertEqual((root / 'main.py').read_text(encoding='utf-8'), main)
            (root / 'utils/oauth.py').write_text(oauth, encoding='utf-8', newline='\n')
            apply(root)
            with self.assertRaises(ValueError): apply(root)


if __name__ == '__main__': unittest.main()
