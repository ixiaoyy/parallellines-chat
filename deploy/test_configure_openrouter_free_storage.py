"""维护入口的本地原生适配器模拟；不导入 Chat 应用、数据库或真实模型。"""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import unittest


PATH = Path(__file__).with_name('configure-openrouter-free-storage.py')
SPEC = importlib.util.spec_from_file_location('free_storage', PATH)
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)


def fixture(existing=True, model_ids=None):
    model_ids = ['gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol'] if model_ids is None else model_ids
    return {'rows': {
        'openai.enable': True,
        'openai.api_base_urls': [storage.RELAY, 'https://other.example.invalid/v1'],
        'openai.api_keys': ['', 'DO-NOT-PRINT-OTHER-KEY'],
        storage.CONFIG_KEY: {
            '0': {'enable': True, 'auth_type': 'system_oauth', 'api_type': 'responses', 'forward_cookies': False, 'headers': {}, 'model_ids': list(model_ids)},
            '1': {'enable': True, 'auth_type': 'bearer', 'headers': {'Authorization': 'DO-NOT-PRINT-HEADER'}, 'model_ids': ['unrelated']},
        },
        'models.base_models_cache': False,
        'ui.default_models': 'gpt-6-luna', 'task.model.external': 'gpt-6-luna',
    }, 'model': {'id': storage.MODEL, 'name': 'Keep existing name', 'base_model_id': None, 'is_active': True, 'user_id': 'owner', 'version_id': 'keep-version'} if existing else None,
        'grants': [{'id': 'old-private', 'principal_type': 'group', 'principal_id': 'private-group', 'permission': 'read'}] if existing else [],
        'cache_ttl_seconds': 1, 'other_users_roles_and_balances': {'existing': 'unchanged'}}


class MemoryJournal:
    def __init__(self): self.saved = None; self.writes = 0; self.fail = False
    def create(self, value):
        if self.fail: raise OSError('private backup failure')
        if self.saved is not None: raise FileExistsError('existing backup')
        self.saved = copy.deepcopy(value); self.writes += 1
    def read(self): return copy.deepcopy(self.saved)
    def update(self, value): self.saved = copy.deepcopy(value); self.writes += 1


class NativeMock:
    def __init__(self, state): self.state = copy.deepcopy(state); self.writes = []; self.race_on_cas = False
    async def read_state(self): return copy.deepcopy(self.state)
    async def check_owner(self, user_id): return user_id == 'existing-admin'
    async def create_model(self, user_id):
        self.writes.append('model')
        self.state['model'] = {'id': storage.MODEL, 'name': 'OpenRouter 免费', 'base_model_id': None, 'is_active': True, 'user_id': user_id, 'version_id': 'new-version'}
    async def add_grant(self):
        self.writes.append('grant')
        self.state['grants'].append({'id': 'this-release-grant', **storage.PUBLIC_READ})
        return 'this-release-grant'
    async def remove_grant(self):
        self.writes.append('revoke')
        self.state['grants'] = [grant for grant in self.state['grants'] if storage.grant_key(grant) != storage.grant_key(storage.PUBLIC_READ)]
    async def cas_configs(self, before, after):
        if self.race_on_cas:
            self.state['rows'][storage.CONFIG_KEY]['1']['changed-by-other-admin'] = True
        storage.require(self.state['rows'][storage.CONFIG_KEY] == before, 'configuration_changed_concurrently')
        self.writes.append('filter')
        self.state['rows'][storage.CONFIG_KEY] = copy.deepcopy(after)


class NativeMaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, runtime, journal, action='apply', owner=None, progress=None):
        if progress is None: progress = {'write_attempted': False, 'completed': []}
        return await storage.configure(action, runtime, journal, owner, progress)

    async def test_default_plan_is_readonly_and_never_exports_secrets(self):
        before = fixture(False)
        runtime, journal = NativeMock(before), MemoryJournal()
        result = await self.execute(runtime, journal, 'plan')
        self.assertTrue(result['model_missing'])
        self.assertEqual(runtime.state, before)
        self.assertEqual(runtime.writes, [])
        self.assertIsNone(journal.saved)
        self.assertNotIn('DO-NOT-PRINT', json.dumps(result))

    async def test_apply_preserves_paid_defaults_other_connections_and_old_grant_ids(self):
        before = fixture()
        runtime, journal = NativeMock(before), MemoryJournal()
        result = await self.execute(runtime, journal)
        expected = copy.deepcopy(before)
        expected['rows'][storage.CONFIG_KEY]['0']['model_ids'].append(storage.MODEL)
        expected['grants'].append({'id': 'this-release-grant', **storage.PUBLIC_READ})
        self.assertEqual(runtime.state, expected)
        self.assertEqual(runtime.writes, ['grant', 'filter'])
        self.assertEqual(result['model_requests'], 0)
        self.assertNotIn('DO-NOT-PRINT', json.dumps(journal.saved))
        runtime.writes.clear()
        again = await self.execute(runtime, MemoryJournal())
        self.assertFalse(again['access_changed'])
        self.assertEqual(runtime.writes, [])

    async def test_create_requires_explicit_existing_admin_without_creating_user(self):
        runtime, journal = NativeMock(fixture(False, [])), MemoryJournal()
        with self.assertRaisesRegex(storage.MaintenanceFailure, 'existing_admin_owner_required'):
            await self.execute(runtime, journal, owner='not-admin')
        self.assertEqual(runtime.writes, [])
        self.assertIsNone(journal.saved)
        await self.execute(runtime, journal, owner='existing-admin')
        self.assertEqual(runtime.writes, ['model', 'grant'])
        self.assertEqual(runtime.state['rows'][storage.CONFIG_KEY]['0']['model_ids'], [])
        await self.execute(runtime, journal, 'rollback')
        self.assertIsNotNone(runtime.state['model'])
        self.assertEqual(runtime.state['grants'], [])
        self.assertEqual(runtime.state['other_users_roles_and_balances'], {'existing': 'unchanged'})

    async def test_rollback_restores_only_own_delta_and_is_idempotent(self):
        before = fixture()
        runtime, journal = NativeMock(before), MemoryJournal()
        await self.execute(runtime, journal)
        result = await self.execute(runtime, journal, 'rollback')
        self.assertTrue(result['restored'])
        self.assertEqual(runtime.state, before)
        writes = list(runtime.writes)
        again = await self.execute(runtime, journal, 'rollback')
        self.assertFalse(again['changed'])
        self.assertEqual(runtime.writes, writes)

    async def test_rollback_refuses_concurrent_acl_or_filter_change(self):
        for mutate in [
            lambda state: state['grants'].append({'id': 'another-admin', 'principal_type': 'user', 'principal_id': 'other-user', 'permission': 'read'}),
            lambda state: state['rows'][storage.CONFIG_KEY]['0']['model_ids'].append('another-model'),
            lambda state: state['grants'][-1].update(id='replacement-by-other-admin'),
        ]:
            runtime, journal = NativeMock(fixture()), MemoryJournal()
            await self.execute(runtime, journal)
            mutate(runtime.state)
            concurrent = copy.deepcopy(runtime.state)
            runtime.writes.clear()
            with self.assertRaises(storage.MaintenanceFailure): await self.execute(runtime, journal, 'rollback')
            self.assertEqual(runtime.state, concurrent)
            self.assertEqual(runtime.writes, [])

    async def test_native_config_cas_failure_is_partial_and_does_not_overwrite_other_admin(self):
        runtime, journal = NativeMock(fixture()), MemoryJournal()
        runtime.race_on_cas = True
        progress = {'write_attempted': False, 'completed': []}
        with self.assertRaisesRegex(storage.MaintenanceFailure, 'configuration_changed_concurrently'):
            await self.execute(runtime, journal, progress=progress)
        self.assertTrue(progress['write_attempted'])
        self.assertEqual(progress['completed'], ['model.read_grant'])
        self.assertTrue(runtime.state['rows'][storage.CONFIG_KEY]['1']['changed-by-other-admin'])
        self.assertNotIn(storage.MODEL, runtime.state['rows'][storage.CONFIG_KEY]['0']['model_ids'])
        runtime.race_on_cas = False
        await self.execute(runtime, journal, 'rollback')
        self.assertEqual(runtime.state['grants'], fixture()['grants'])
        self.assertTrue(runtime.state['rows'][storage.CONFIG_KEY]['1']['changed-by-other-admin'])

    async def test_bad_shapes_credentials_caches_aliases_stop_before_backup_or_writes(self):
        def url_key(state):
            state['rows'][storage.CONFIG_KEY][storage.RELAY] = state['rows'][storage.CONFIG_KEY].pop('0')
        def other_url_key(state):
            state['rows'][storage.CONFIG_KEY]['https://other.example.invalid/v1'] = state['rows'][storage.CONFIG_KEY].pop('1')
        def short_keys(state): state['rows']['openai.api_keys'].pop()
        def shared_key(state): state['rows']['openai.api_keys'][0] = 'DO-NOT-PRINT'
        def cache(state): state['rows']['models.base_models_cache'] = True
        def unbounded(state): state['cache_ttl_seconds'] = None
        def alias(state): state['model']['base_model_id'] = 'gpt-6-luna'
        def null_filter(state): state['rows'][storage.CONFIG_KEY]['0']['model_ids'] = None
        for mutate in [url_key, other_url_key, short_keys, shared_key, cache, unbounded, alias, null_filter]:
            runtime, journal = NativeMock(fixture()), MemoryJournal()
            mutate(runtime.state)
            before = copy.deepcopy(runtime.state)
            with self.assertRaises(storage.MaintenanceFailure): await self.execute(runtime, journal)
            self.assertEqual(runtime.state, before)
            self.assertEqual(runtime.writes, [])
            self.assertIsNone(journal.saved)

    async def test_backup_failure_happens_before_any_native_write(self):
        runtime, journal = NativeMock(fixture()), MemoryJournal()
        journal.fail = True
        with self.assertRaises(OSError): await self.execute(runtime, journal)
        self.assertEqual(runtime.writes, [])

    def test_runtime_does_not_import_startup_or_migrations(self):
        tree = ast.parse(PATH.read_text(encoding='utf-8'))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn('open_webui.main', imports)
        self.assertNotIn('open_webui.config', imports)
        runtime = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'load_native_runtime')
        disabled = next(node.lineno for node in ast.walk(runtime) if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "os.environ['ENABLE_DB_MIGRATIONS']")
        native_import = min(node.lineno for node in ast.walk(runtime) if isinstance(node, ast.ImportFrom) and node.module.startswith('open_webui.'))
        self.assertLess(disabled, native_import)
        self.assertNotIn('create_all', PATH.read_text(encoding='utf-8'))


if __name__ == '__main__': unittest.main()
