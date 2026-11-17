"""真实 cryptography + 隔离原生设置边界测试；不连数据库、服务器或模型。"""
from __future__ import annotations

import ast
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_serializer

import credentials
from main_personal_connections import (
    PREFIX,
    PersonalConnectionCredentialError,
    decrypt_personal_connection_settings as decrypt,
    encrypt_personal_connection_settings as encrypt,
    redact_personal_connection_settings as redact,
)


SOURCE = Path(os.environ.get('PERSONAL_CONNECTIONS_SOURCE', Path(__file__).parents[2] / '.build/personal-connections-20261117/source'))
SECRET = 'synthetic-stable-webui-secret'
OWNER = 'synthetic-user-A'


def settings(keys=None):
    """构造隔离个人连接及须保留的原生界面偏好。"""
    return {'ui': {'theme': 'dark', 'directConnections': {
        'OPENAI_API_BASE_URLS': ['https://fixture.example.invalid/v1'],
        'OPENAI_API_KEYS': ['synthetic-key-A'] if keys is None else keys,
        'OPENAI_API_CONFIGS': {'0': {'enable': True, 'model_ids': ['fixture-model']}},
    }}, 'notifications': {'email': False}}


class CryptoTests(unittest.TestCase):
    def test_round_trip_owner_binding_and_non_mutation(self):
        plain = settings(['synthetic-key-A', '', '第二个测试密钥'])
        before = deepcopy(plain)
        sealed = encrypt(plain, OWNER, SECRET)
        self.assertEqual(plain, before)
        self.assertNotIn('synthetic-key-A', str(sealed))
        self.assertTrue(sealed['ui']['directConnections']['OPENAI_API_KEYS'][0].startswith(PREFIX))
        self.assertEqual(sealed['ui']['directConnections']['OPENAI_API_KEYS'][1], '')
        self.assertEqual(decrypt(sealed, OWNER, SECRET), before)
        with self.assertRaises(PersonalConnectionCredentialError):
            decrypt(sealed, 'synthetic-user-B', SECRET)

    def test_bad_cipher_wrong_secret_and_missing_secret_fail_safely(self):
        sealed = encrypt(settings(), OWNER, SECRET)
        for value, secret in [(sealed, ''), (sealed, 'different-secret'), (settings([PREFIX + 'damaged']), SECRET), (settings(['plpc:v2:future']), SECRET)]:
            with self.subTest(secret_present=bool(secret)):
                with self.assertRaises(PersonalConnectionCredentialError) as caught:
                    decrypt(value, OWNER, secret)
                self.assertEqual(caught.exception.status_code, 503)
                self.assertNotIn('damaged', str(caught.exception))
                self.assertNotIn('synthetic-key-A', str(caught.exception))
        with self.assertRaises(PersonalConnectionCredentialError):
            encrypt(settings(), OWNER, '')

    def test_legacy_and_keyless_reads_need_no_new_prerequisite(self):
        values = [None, {}, {'ui': None}, {'ui': {'directConnections': None}},
                  {'ui': {'theme': 'light'}}, settings([]), settings(['']),
                  {'ui': {'directConnections': {'OPENAI_API_KEYS': None}}}]
        for value in values:
            self.assertEqual(decrypt(value, OWNER, ''), value)
            self.assertEqual(encrypt(value, OWNER, ''), value)
        legacy = settings()
        self.assertEqual(decrypt(legacy, OWNER, ''), legacy)
        self.assertTrue(encrypt(legacy, OWNER, SECRET)['ui']['directConnections']['OPENAI_API_KEYS'][0].startswith(PREFIX))

    def test_cipher_reuse_is_not_double_encrypted(self):
        sealed = encrypt(settings(), OWNER, SECRET)
        self.assertEqual(encrypt(sealed, OWNER, SECRET), sealed)
        with self.assertRaises(PersonalConnectionCredentialError):
            encrypt(sealed, 'synthetic-user-B', SECRET)

    def test_invalid_key_shapes_are_rejected_without_echo(self):
        for keys in ['not-a-list', [None], [False], [42], [{}]]:
            with self.subTest(keys_type=type(keys).__name__):
                with self.assertRaises(PersonalConnectionCredentialError) as caught:
                    encrypt(settings(keys), OWNER, SECRET)
                self.assertEqual(caught.exception.status_code, 400)

    def test_redaction_preserves_non_secret_fields_and_input(self):
        for value in [settings(), encrypt(settings(), OWNER, SECRET)]:
            before = deepcopy(value)
            safe = redact(value)
            self.assertNotIn('OPENAI_API_KEYS', safe['ui']['directConnections'])
            self.assertEqual(safe['ui']['directConnections']['OPENAI_API_BASE_URLS'], before['ui']['directConnections']['OPENAI_API_BASE_URLS'])
            self.assertEqual(safe['notifications'], before['notifications'])
            self.assertEqual(value, before)


def extract(source, names):
    """提取已打补丁的原生函数，去除路由装饰器，避免加载真实应用/数据库。"""
    selected = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    return 'from __future__ import annotations\n' + '\n\n'.join(ast.unparse(node) for node in selected)


class NativeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.changed = credentials.prepare(SOURCE / 'backend/open_webui')
        self.commits = 0
        self.events = []
        owner = self

        class UserSettings(BaseModel):
            ui: dict | None = {}
            model_config = ConfigDict(extra='allow')

        model_tree = ast.parse(self.changed['models/users.py'])
        user_model = next(node for node in model_tree.body if isinstance(node, ast.ClassDef) and node.name == 'UserModel')
        serializer = next(node for node in user_model.body if isinstance(node, ast.FunctionDef) and node.name == 'serialize_settings')
        serializer_source = '    ' + ast.unparse(serializer).replace('\n', '\n    ')
        minimal_user = f'''class UserModel(BaseModel):
    id: str
    role: str = 'admin'
    settings: UserSettings | None = None
{serializer_source}
'''
        model_namespace = {'BaseModel': BaseModel, 'UserSettings': UserSettings, 'field_serializer': field_serializer,
                           'redact_personal_connection_settings': redact}
        exec('from __future__ import annotations\n' + minimal_user, model_namespace)
        UserModel = model_namespace['UserModel']
        UserModel.model_rebuild(_types_namespace={'UserSettings': UserSettings})

        self.UserSettings = UserSettings
        self.UserModel = UserModel
        self.row = SimpleNamespace(id=OWNER, role='admin', settings=settings())

        class Session:
            async def get(self, model, id):
                return owner.row if id == owner.row.id else None

            async def commit(self):
                owner.commits += 1

        class Context:
            async def __aenter__(self):
                return Session()

            async def __aexit__(self, *args):
                return False

        table = next(node for node in model_tree.body if isinstance(node, ast.ClassDef) and node.name == 'UsersTable')
        update = next(node for node in table.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'update_user_settings_by_id')
        namespace = {'get_async_db_context': lambda db=None: Context(), 'User': object,
                     'UserModel': SimpleNamespace(model_validate=lambda row: UserModel(id=row.id, role=row.role, settings=row.settings)),
                     'encrypt_personal_connection_settings': encrypt, 'WEBUI_SECRET_KEY': SECRET}
        exec('from __future__ import annotations\n' + ast.unparse(update), namespace)
        self.model_namespace = namespace

        class Users:
            update_user_settings_by_id = namespace['update_user_settings_by_id']

        class Config:
            @staticmethod
            async def get(key):
                return {'theme': 'light', 'chatBubble': True} if key == 'ui.default_interface_settings' else {}

        async def publish_event(request, event, actor, subject_id):
            owner.events.append({'actor': actor.model_dump(), 'subject_id': subject_id})

        route_source = self.changed['routers/users.py']
        route_namespace = {'UserSettings': UserSettings, 'Config': Config, 'Users': Users(), 'WEBUI_SECRET_KEY': SECRET,
                           'PersonalConnectionCredentialError': PersonalConnectionCredentialError,
                           'decrypt_personal_connection_settings': decrypt, 'HTTPException': HTTPException,
                           'Depends': lambda dependency: None, 'get_verified_user': lambda: None,
                           'get_async_session': lambda: None, 'InterfaceSettings': SimpleNamespace(model_fields={}),
                           'publish_event': publish_event, 'EVENTS': SimpleNamespace(USER_SETTINGS_UPDATED='settings-updated')}
        exec(extract(route_source, {'merge_user_ui_settings', '_personal_connection_settings_for_user',
                                   'get_user_settings_by_session_user', 'update_user_settings_by_session_user'}), route_namespace)
        self.routes = route_namespace

    def current(self):
        return self.UserModel(id=self.row.id, role=self.row.role, settings=deepcopy(self.row.settings))

    async def save(self, patch):
        return await self.routes['update_user_settings_by_session_user'](
            request=object(), form_data=self.UserSettings.model_validate(patch), user=self.current(), db=None)

    async def test_explicit_save_persists_cipher_and_returns_clear_current_user_only(self):
        response = await self.save(settings())
        self.assertEqual(self.commits, 1)
        self.assertTrue(self.row.settings['ui']['directConnections']['OPENAI_API_KEYS'][0].startswith(PREFIX))
        self.assertEqual(response.ui['directConnections']['OPENAI_API_KEYS'], ['synthetic-key-A'])
        self.assertNotIn('OPENAI_API_KEYS', self.events[0]['actor']['settings']['ui']['directConnections'])

    async def test_raw_and_merged_get_do_not_write_or_mutate_storage(self):
        self.row.settings = encrypt(settings(), OWNER, SECRET)
        sealed = deepcopy(self.row.settings)
        for raw in [True, False]:
            result = await self.routes['get_user_settings_by_session_user'](raw=raw, user=self.current())
            self.assertEqual(result.ui['directConnections']['OPENAI_API_KEYS'], ['synthetic-key-A'])
            self.assertEqual(result.ui.get('chatBubble'), None if raw else True)
        self.assertEqual(self.row.settings, sealed)
        self.assertEqual(self.commits, 0)

    async def test_raw_legacy_read_with_missing_secret_does_not_migrate(self):
        self.routes['WEBUI_SECRET_KEY'] = ''
        legacy = deepcopy(self.row.settings)
        result = await self.routes['get_user_settings_by_session_user'](raw=True, user=self.current())
        self.assertEqual(result.ui['directConnections']['OPENAI_API_KEYS'], ['synthetic-key-A'])
        self.assertEqual(self.row.settings, legacy)
        self.assertEqual(self.commits, 0)

    async def test_preference_patch_preserves_cipher_bytes_and_unrelated_settings(self):
        self.row.settings = encrypt(settings(), OWNER, SECRET)
        sealed = self.row.settings['ui']['directConnections']['OPENAI_API_KEYS'][:]
        result = await self.save({'ui': {'theme': 'light'}})
        self.assertEqual(self.row.settings['ui']['directConnections']['OPENAI_API_KEYS'], sealed)
        self.assertEqual(result.ui['theme'], 'light')
        self.assertEqual(self.row.settings['notifications'], {'email': False})

    async def test_notification_model_caller_keeps_cipher_without_new_read_prerequisite(self):
        self.row.settings = encrypt(settings(), OWNER, SECRET)
        sealed = self.row.settings['ui']['directConnections']['OPENAI_API_KEYS'][:]
        self.model_namespace['WEBUI_SECRET_KEY'] = ''
        await self.routes['Users'].update_user_settings_by_id(OWNER, {'notifications': {'email': True}})
        self.assertEqual(self.row.settings['ui']['directConnections']['OPENAI_API_KEYS'], sealed)
        self.assertEqual(self.row.settings['notifications'], {'email': True})

    async def test_null_delete_and_keyless_connections_need_no_cipher(self):
        self.row.settings = encrypt(settings(), OWNER, SECRET)
        self.model_namespace['WEBUI_SECRET_KEY'] = ''
        self.routes['WEBUI_SECRET_KEY'] = ''
        result = await self.save({'ui': {'directConnections': None}})
        self.assertNotIn('directConnections', result.ui)
        result = await self.save(settings(['']))
        self.assertEqual(result.ui['directConnections']['OPENAI_API_KEYS'], [''])

    async def test_corrupt_saved_cipher_fails_before_ordinary_preference_write(self):
        self.row.settings = settings([PREFIX + 'damaged'])
        with self.assertRaises(HTTPException) as caught:
            await self.save({'ui': {'theme': 'light'}})
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.commits, 0)
        self.assertNotIn('damaged', str(caught.exception.detail))

    async def test_missing_key_rejects_new_secret_before_commit(self):
        self.model_namespace['WEBUI_SECRET_KEY'] = ''
        with self.assertRaises(HTTPException) as caught:
            await self.save(settings())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.commits, 0)
        self.assertEqual(self.events, [])

    async def test_cross_user_cipher_cannot_be_saved_and_does_not_change_storage(self):
        before = deepcopy(self.row.settings)
        foreign = encrypt(settings(), 'synthetic-user-B', SECRET)
        with self.assertRaises(HTTPException) as caught:
            await self.save(foreign)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.row.settings, before)
        self.assertEqual(self.commits, 0)

    def test_ordinary_user_model_serialization_redacts_legacy_and_cipher(self):
        for saved in [settings(), encrypt(settings(), OWNER, SECRET)]:
            user = self.UserModel(id=OWNER, settings=saved)
            self.assertNotIn('OPENAI_API_KEYS', user.model_dump()['settings']['ui']['directConnections'])
            self.assertEqual(user.settings.ui['directConnections']['OPENAI_API_KEYS'], saved['ui']['directConnections']['OPENAI_API_KEYS'])

    def test_prepare_is_read_only_and_source_drift_stops(self):
        self.assertEqual(set(self.changed), {'models/users.py', 'routers/users.py', 'utils/main_personal_connections.py'})
        self.assertNotIn('serialize_settings', (SOURCE / 'backend/open_webui/models/users.py').read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            credentials.patch_models(self.changed['models/users.py'])


if __name__ == '__main__':
    unittest.main()
