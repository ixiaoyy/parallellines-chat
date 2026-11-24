import ast
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from apply_model_auth import apply, patch, OPENAI_SHA256, RELEASE_OPENAI_SHA256
from main_model_auth import (
    MainModelDiscoveryError, MainOAuthAuthorizationRequired, RELAY_MODELS_URL,
    is_main_models_connection,
    require_main_model_authorization, check_main_model_authorization_response,
)


class Context:
    def __init__(self, value): self.value = value
    async def __aenter__(self): return self.value
    async def __aexit__(self, *args): return False


class ModelAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source_path = os.environ.get('OPENWEBUI_AUTH_OPENAI')
        self.original = Path(source_path).read_bytes() if source_path else (
            Path(__file__).parents[1] / '.build/model-auth-baseline/routers/openai.py').read_bytes()
        source = self.original
        self.assertIn(hashlib.sha256(source).hexdigest(), (OPENAI_SHA256, RELEASE_OPENAI_SHA256))
        tree = ast.parse(patch(source.decode('utf-8')))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'send_get_request')
        # Exercise the actual patched upstream request function without importing
        # application startup, its database or any live model provider.
        for arg in function.args.args: arg.annotation = None
        self.headers = AsyncMock(return_value=({}, None))
        self.response = SimpleNamespace(status=200, json=AsyncMock(return_value={'data': [{'id': 'model'}]}))
        self.session = SimpleNamespace(get=Mock(return_value=Context(self.response)))
        scope = {
            'aiohttp': SimpleNamespace(ClientSession=lambda **kw: Context(self.session)),
            '_MODEL_LIST_TIMEOUT': object(), 'AIOHTTP_CLIENT_SESSION_SSL': True,
            'get_headers_and_cookies': self.headers, 'ENABLE_FORWARD_USER_INFO_HEADERS': False,
            'require_main_model_authorization': require_main_model_authorization,
            'check_main_model_authorization_response': check_main_model_authorization_response,
            'MainModelDiscoveryError': MainModelDiscoveryError,
            'is_main_models_connection': is_main_models_connection,
            'JSONCodec': SimpleNamespace(loads=object()), 'log': SimpleNamespace(error=Mock()),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<patched-upstream>', 'exec'), scope)
        self.send = scope['send_get_request']
        self.config = {'auth_type': 'system_oauth'}

    async def request(self, **kwargs):
        return await self.send(request=object(), url=kwargs.get('url', RELAY_MODELS_URL),
                               user=SimpleNamespace(id='synthetic-user'), config=kwargs.get('config', self.config))

    async def test_missing_oauth_fails_before_network_and_never_empty_success(self):
        with self.assertRaises(MainOAuthAuthorizationRequired) as caught:
            await self.request()
        self.session.get.assert_not_called()
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail['code'], 'main_oauth_authorization_required')
        self.assertEqual(caught.exception.detail['reauthorize_url'], '/oauth/oidc/login')
        self.assertEqual(caught.exception.headers['Cache-Control'], 'no-store')

    async def test_valid_oauth_returns_actual_models(self):
        self.headers.return_value = ({'Authorization': 'Bearer synthetic-only'}, None)
        self.assertEqual(await self.request(), {'data': [{'id': 'model'}]})
        self.session.get.assert_called_once()

    async def test_expired_oauth_401_requires_manual_reauthorization_without_exposing_body(self):
        self.headers.return_value = ({'Authorization': 'Bearer synthetic-only'}, None)
        self.response.status = 401
        self.response.json.return_value = {'error': 'private upstream details'}
        with self.assertRaises(MainOAuthAuthorizationRequired):
            await self.request()
        self.response.json.assert_not_awaited()

    async def test_binding_quota_and_service_failures_do_not_request_login(self):
        self.headers.return_value = ({'Authorization': 'Bearer synthetic-only'}, None)
        for status in [403, 429, 500, 503]:
            with self.subTest(status=status):
                self.response.status = status
                with self.assertRaises(MainModelDiscoveryError) as caught:
                    await self.request()
                self.assertNotIsInstance(caught.exception, MainOAuthAuthorizationRequired)
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.detail['code'], 'main_model_discovery_failed')
                self.assertNotIn('reauthorize_url', caught.exception.detail)
        self.response.json.assert_not_awaited()

    async def test_trusted_network_and_json_failures_are_explicit_service_errors(self):
        self.headers.return_value = ({'Authorization': 'Bearer synthetic-only'}, None)
        self.session.get.side_effect = TimeoutError('private upstream diagnostic')
        with self.assertRaises(MainModelDiscoveryError) as caught:
            await self.request()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn('private', str(caught.exception.detail))
        self.session.get.side_effect = None
        self.response.json.side_effect = ValueError('private malformed response')
        with self.assertRaises(MainModelDiscoveryError) as caught:
            await self.request()
        self.assertEqual(caught.exception.status_code, 503)

    async def test_other_connections_preserve_upstream_behavior(self):
        self.response.status = 401
        await self.request(url='https://unrelated.example.invalid/v1/models')
        await self.request(config={'auth_type': 'bearer'})
        await self.request(url=RELAY_MODELS_URL + '?unexpected=true')
        self.session.get.side_effect = TimeoutError('ordinary provider error')
        self.assertIsNone(await self.request(url='https://unrelated.example.invalid/v1/models'))

    def test_patch_rejects_already_modified_source(self):
        source = self.original.decode('utf-8')
        with self.assertRaises(ValueError): patch(patch(source))

    def test_patch_preserves_all_non_discovery_functions_and_connection_guards(self):
        original = self.original.decode('utf-8')
        updated = patch(original)
        before = ast.parse(original)
        after = ast.parse(updated)
        before_functions = {node.name: ast.get_source_segment(original, node)
                            for node in before.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        after_functions = {node.name: ast.get_source_segment(updated, node)
                           for node in after.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertEqual(before_functions.keys(), after_functions.keys())
        for name in before_functions.keys() - {'send_get_request'}:
            self.assertEqual(before_functions[name], after_functions[name], name)

    def test_application_checks_exact_baseline_and_refuses_drift_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'routers/openai.py'
            target.parent.mkdir()
            target.write_bytes(self.original)
            apply(root, check=True)
            self.assertEqual(target.read_bytes(), self.original)
            target.write_bytes(self.original + b'\n')
            with self.assertRaises(ValueError): apply(root)
            self.assertEqual(target.read_bytes(), self.original + b'\n')
            target.write_bytes(self.original)
            apply(root)
            with self.assertRaises(ValueError): apply(root)


if __name__ == '__main__': unittest.main()
