"""执行个人直连补丁后的原生任务/分发/socket函数；不启动应用或访问数据库、真实模型。"""
import ast
import asyncio
import copy
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock
import uuid

from backend import FUNCTION_SHA256, TARGET_FILES, patch_file, prepare


OVERLAY = Path(__file__).parents[1]
SOURCE = Path(os.environ.get('OPENWEBUI_SOURCE_ROOT', str(
    OVERLAY.parent / '.build/performance-build/open-webui-f7ac7ee10962ddec0017f5c29b6740d9b9c5faca/backend/open_webui'
)))
PERSONAL = 'pl-personal-' + 'a' * 32 + '.example-model'
OTHER_PERSONAL = 'pl-personal-' + 'b' * 32 + '.example-model'
PAID = ['gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol']
FREE = 'openrouter/free'
TASK_NAMES = ['TITLE_GENERATION', 'FOLLOW_UP_GENERATION', 'TAGS_GENERATION', 'IMAGE_PROMPT_GENERATION',
              'QUERY_GENERATION', 'AUTOCOMPLETE_GENERATION', 'EMOJI_GENERATION', 'MOA_RESPONSE_GENERATION']
TASK_FUNCTIONS = ['generate_title', 'generate_follow_ups', 'generate_chat_tags', 'generate_image_prompt',
                  'generate_queries', 'generate_autocompletion', 'generate_emoji', 'generate_moa_response']


class HTTPFailure(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(str(detail))


class State:
    def __init__(self, state):
        self.__dict__ = state


class Request:
    """保留 ASGI state 字典引用，验证原生 Request(scope, receive) 的复制边界。"""
    def __init__(self, scope, receive=None):
        self.scope, self.receive = scope, receive
        self.state = State(scope.setdefault('state', {}))
        self.app = scope['app']


def load_functions(source, names, scope):
    """只装载指定原生函数；替换依赖边界，不导入 FastAPI 应用或数据库模型。"""
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('native fixture functions missing')
    for node in nodes:
        node.decorator_list = []
        node.returns = None
        node.body = [item for item in node.body if not isinstance(item, ast.ImportFrom)]
        for arg in [*node.args.args, *node.args.kwonlyargs]:
            arg.annotation = None
        node.args.defaults = [ast.Constant(None) if isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                              and value.func.id == 'Depends' else value for value in node.args.defaults]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<native-personal-fixture>', 'exec'), scope)


class PersonalBackendTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_dir():
            raise unittest.SkipTest('OPENWEBUI_SOURCE_ROOT must identify the pinned backend fixture')
        spec = importlib.util.spec_from_file_location('personal_free_fixture', OVERLAY / 'apply_openrouter_free.py')
        free = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(free)
        cls.original = {path: (SOURCE / path).read_text(encoding='utf-8') for path in TARGET_FILES}
        # 此默认 fixture 为未应用 free 的固定源码；同时验证发布顺序中的 free → personal。
        cls.free = {path: free.patch_file(text, path) if any(p == path for p, _ in free.FUNCTION_SHA256) else text
                    for path, text in cls.original.items()}
        cls.patched = {path: patch_file(text, path) for path, text in cls.free.items()}
        cls.socket_source = (SOURCE / 'socket/main.py').read_text(encoding='utf-8')

    def setUp(self):
        self.user = SimpleNamespace(id='synthetic-user', role='user', email='synthetic@example.invalid')
        self.platform_models = {model: {'id': model, 'connection_type': 'external', 'info': {'params': {}}}
                                for model in [FREE, *PAID]}
        self.personal_model = {'id': PERSONAL, 'direct': True, 'urlIdx': 0, 'info': {'params': {}}}
        self.models = {**self.platform_models, PERSONAL: self.personal_model}
        self.metadata = {'user_id': self.user.id, 'session_id': 'synthetic-session',
                         'chat_id': 'synthetic-chat', 'message_id': 'synthetic-response'}
        self.request = self.request_for(True)
        self.config = {'task.model.default': 'local-task', 'task.model.external': PAID[0],
                       'task.model.params': {'max_tokens': 64}, 'task.autocomplete.input_max_length': 200,
                       'chat.context_compaction.model': PAID[0]}

        async def get(key):
            return self.config.get(key, True if key.endswith('.enable') else '')

        async def get_many(*keys):
            return {key: self.config[key] for key in keys if key in self.config}

        self.platform = AsyncMock(return_value={'choices': [{'message': {'content': 'PLATFORM'}}]})
        self.browser = AsyncMock(return_value={'choices': [{'message': {'content': 'PERSONAL'}}]})
        self.emitter = AsyncMock()
        self.session_pool = {self.metadata['session_id']: {'id': self.user.id}}
        self.scope = {
            'HTTPException': HTTPFailure, 'Request': Request, 'asyncio': asyncio, 'uuid': uuid, 'time': time,
            'log': logging.getLogger('personal-backend-test'), 'logging': logging,
            'Config': SimpleNamespace(get=get, get_many=get_many), 're': re, 'JSONCodec': json,
            'BYPASS_MODEL_ACCESS_CONTROL': False, 'check_model_access': AsyncMock(),
            'generate_openai_chat_completion': self.platform, 'EVENT_QUEUES': {},
            'sio': SimpleNamespace(call=self.browser), 'SESSION_POOL': self.session_pool,
            'WEBSOCKET_EVENT_CALLER_TIMEOUT': 2, 'socketio': SimpleNamespace(exceptions=SimpleNamespace(TimeoutError=TimeoutError)),
            'get_user_id_from_session_pool': lambda sid: self.session_pool.get(sid, {}).get('id'),
            'is_saved_chat_id': lambda value: bool(value and not value.startswith('temp')),
            'status': SimpleNamespace(HTTP_200_OK=200, HTTP_400_BAD_REQUEST=400, HTTP_404_NOT_FOUND=404, HTTP_500_INTERNAL_SERVER_ERROR=500),
            'ERROR_MESSAGES': SimpleNamespace(MODEL_NOT_FOUND=lambda: 'missing', FEATURE_DISABLED=lambda name: name),
            'JSONResponse': lambda status_code, content: SimpleNamespace(status_code=status_code, content=content),
            'TASKS': SimpleNamespace(**{name: name for name in TASK_NAMES}),
            'process_pipeline_inlet_filter': AsyncMock(side_effect=lambda request, payload, user, models: payload),
            'apply_params_to_form_data': lambda payload, model, params: {**payload, **{k: v for k, v in (params or {}).items() if k not in payload}},
            'get_content_from_message': lambda message: message.get('content', ''),
            'get_last_user_message': lambda messages: next((m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'), ''),
            'replace_prompt_variable': lambda template, prompt: template,
            'replace_messages_variable': lambda template, *args: template,
            'prompt_variables_template': lambda template, variables: template,
            'prompt_template': AsyncMock(return_value='synthetic-summary-prompt'),
            'DEFAULT_CONTEXT_COMPACTION_PROMPT': 'synthetic',
            '_response_text': lambda response: response['choices'][0]['message']['content'],
        }
        for name in ['title', 'follow_up', 'tags', 'image_prompt', 'query', 'autocomplete', 'emoji']:
            self.scope[name + '_generation_template'] = AsyncMock(return_value='synthetic-task-prompt')
        for name in ['TITLE', 'FOLLOW_UP', 'TAGS', 'IMAGE_PROMPT', 'QUERY', 'AUTOCOMPLETE', 'EMOJI', 'MOA']:
            self.scope['DEFAULT_' + name + '_GENERATION_PROMPT_TEMPLATE'] = 'synthetic-template'
        self.scope['moa_response_generation_template'] = lambda *args: 'synthetic-task-prompt'
        load_functions(self.socket_source, ['get_event_call'], self.scope)
        self.event_lookup = AsyncMock(side_effect=self.scope['get_event_call'])
        self.scope['get_event_call'] = self.event_lookup
        self.load_patched(self.patched)

    def request_for(self, direct, *, metadata=True):
        state = {'model_id': PERSONAL if direct else PAID[2]}
        if direct:
            state.update(direct=True, model=self.personal_model)
        if metadata:
            state['metadata'] = dict(self.metadata)
        return Request({'state': state, 'app': SimpleNamespace(state=SimpleNamespace(MODELS=self.platform_models))})

    def load_patched(self, sources):
        load_functions(sources['utils/task.py'], ['get_task_model_id'], self.scope)
        load_functions(sources['routers/tasks.py'], ['apply_task_model_params', 'get_task_model_generation_config', *TASK_FUNCTIONS], self.scope)
        load_functions(sources['utils/chat.py'], ['generate_chat_completion', 'generate_direct_chat_completion'], self.scope)
        load_functions(sources['utils/context_compaction.py'], ['_generate_summary'], self.scope)

    def form(self, model=PERSONAL):
        return {'model': model, 'messages': [{'role': 'user', 'content': 'synthetic'}], 'prompt': 'synthetic',
                'responses': ['synthetic'], 'type': 'web_search', 'stream': False}

    async def test_all_native_task_endpoints_use_personal_key_route_and_metadata(self):
        for name in TASK_FUNCTIONS:
            with self.subTest(task=name):
                self.browser.reset_mock()
                self.event_lookup.reset_mock()
                result = await self.scope[name](self.request, self.form(), self.user)
                self.assertEqual(result['choices'][0]['message']['content'], 'PERSONAL')
                self.browser.assert_awaited_once()
                event = self.browser.call_args.args[1]['data']
                self.assertEqual(event['type'], 'request:chat:completion')
                self.assertEqual(event['data']['form_data']['model'], PERSONAL)
                self.assertEqual(event['data']['model']['id'], PERSONAL)
                passed = self.event_lookup.call_args.args[0]
                for key, value in self.metadata.items():
                    self.assertEqual(passed[key], value)
                self.assertIn('task', passed)
                self.assertEqual(self.browser.call_args.kwargs['to'], self.metadata['session_id'])
        self.platform.assert_not_awaited()
        self.scope['check_model_access'].assert_not_awaited()

    async def test_direct_task_pipeline_rewrites_are_rejected_before_any_provider(self):
        for changed in [PAID[0], OTHER_PERSONAL]:
            self.scope['process_pipeline_inlet_filter'] = AsyncMock(
                side_effect=lambda request, payload, user, models: {**payload, 'model': changed})
            for name in TASK_FUNCTIONS:
                with self.subTest(task=name, changed=changed):
                    try:
                        result = await self.scope[name](self.request, self.form(), self.user)
                    except HTTPFailure as error:
                        self.assertEqual(error.detail['code'], 'personal_connection_model_changed')
                    else:
                        self.assertEqual(result.status_code, 400)
                        self.assertIn('personal_connection_model_changed', result.content['detail'])
        self.browser.assert_not_awaited()
        self.platform.assert_not_awaited()

    async def test_main_inlet_rewrite_cannot_make_query_task_call_platform(self):
        # 执行主入口保存来源的原生语句，再调用真实查询任务和最终分发。
        tree = ast.parse((SOURCE / 'main.py').read_text(encoding='utf-8'))
        handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'chat_completion')
        captures = [n for n in handler.body if isinstance(n, ast.Assign) and any(
            ast.unparse(target) in ('model_id', 'request.state.model_id') for target in n.targets)][:2]
        self.assertEqual(len(captures), 2)
        scope = {'request': self.request, 'form_data': self.form()}
        exec(compile(ast.fix_missing_locations(ast.Module(body=copy.deepcopy(captures), type_ignores=[])), '<native-origin>', 'exec'), scope)
        self.assertEqual(self.request.state.model_id, PERSONAL)
        middleware = ast.parse(self.original['utils/middleware.py'])
        processor = next(node for node in middleware.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload')
        pipeline = next(node for node in processor.body if isinstance(node, ast.Try) and any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == 'process_pipeline_inlet_filter'
            for child in ast.walk(node)))
        filters = next(node for node in processor.body if isinstance(node, ast.If) and any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == 'process_filter_functions'
            for child in ast.walk(node)))
        wrapper = ast.parse('async def run_inlet(request, form_data, user, models, model, metadata, extra_params):\n    pass\n').body[0]
        wrapper.body = [copy.deepcopy(node) for node in processor.body if pipeline.lineno <= node.lineno <= filters.lineno]
        wrapper.body.append(ast.Return(value=ast.Name(id='form_data', ctx=ast.Load())))

        async def rewrite(request, payload, user, models):
            return {**payload, 'model': PAID[0]}

        async def inlet_filter(**kwargs):
            return kwargs['form_data'], {}

        inlet_scope = {'process_pipeline_inlet_filter': rewrite, 'process_filter_functions': inlet_filter,
                       'ENABLE_FUNCTIONS': True, 'get_filter_context': lambda request: None,
                       'get_filter_functions': AsyncMock(return_value=[])}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), '<native-inlet>', 'exec'), inlet_scope)
        rewritten = await inlet_scope['run_inlet'](self.request, self.form(), self.user, self.models, self.personal_model, {}, {})
        self.assertEqual(rewritten['model'], PAID[0])
        result = await self.scope['generate_queries'](self.request, rewritten, self.user)
        self.assertEqual(result.status_code, 400)
        self.assertIn('personal_connection_model_changed', result.content['detail'])
        self.platform.assert_not_awaited()
        self.browser.assert_not_awaited()

    async def test_disconnected_or_other_users_socket_never_uses_platform(self):
        for owner in [None, 'another-user']:
            with self.subTest(owner=owner):
                self.session_pool.clear()
                if owner:
                    self.session_pool[self.metadata['session_id']] = {'id': owner}
                result = await self.scope['generate_queries'](self.request, self.form(), self.user)
                self.assertEqual(result.status_code, 400)
                self.assertIn('Client session disconnected', result.content['detail'])
        self.browser.assert_not_awaited()
        self.platform.assert_not_awaited()

    async def test_missing_or_wrong_metadata_is_explicit_and_not_a_fallback(self):
        for metadata in [{}, {**self.metadata, 'session_id': None}, {**self.metadata, 'user_id': 'other-user'}]:
            with self.subTest(metadata=metadata):
                self.request.state.metadata = metadata
                result = await self.scope['generate_queries'](self.request, self.form(), self.user)
                self.assertEqual(result.status_code, 400)
                self.assertIn('personal_connection_session_required', result.content['detail'])
        self.browser.assert_not_awaited()
        self.platform.assert_not_awaited()

    async def test_paid_and_free_task_behavior_and_permissions_are_preserved(self):
        request = self.request_for(False)
        await self.scope['generate_title'](request, self.form(PAID[2]), self.user)
        self.assertEqual(self.platform.call_args.kwargs['form_data']['model'], PAID[0])
        self.scope['check_model_access'].assert_awaited()
        self.platform.reset_mock()
        request.state.model_id = FREE
        for name in TASK_FUNCTIONS:
            with self.subTest(task=name):
                self.platform.reset_mock()
                await self.scope[name](request, self.form(FREE), self.user)
                self.assertEqual(self.platform.call_args.kwargs['form_data']['model'], FREE)
        self.browser.assert_not_awaited()
        local = {**self.models, 'local': {'connection_type': 'local'}, 'local-task': {}}
        self.assertEqual(self.scope['get_task_model_id']('local', 'local-task', PAID[0], local), 'local-task')
        self.assertEqual(self.scope['get_task_model_id']('old-unknown', '', PAID[0], self.models), PAID[0])

    async def test_existing_free_filter_guard_is_preserved(self):
        request = self.request_for(False)
        request.state.model_id = FREE
        self.scope['process_pipeline_inlet_filter'] = AsyncMock(
            side_effect=lambda request, payload, user, models: {**payload, 'model': PAID[0]})
        for name in TASK_FUNCTIONS:
            with self.subTest(task=name), self.assertRaises(HTTPFailure) as error:
                await self.scope[name](request, self.form(FREE), self.user)
            self.assertEqual(error.exception.detail['code'], 'free_task_model_changed')
        self.platform.assert_not_awaited()

    async def test_auto_compaction_uses_personal_and_manual_reserved_history_requires_session(self):
        args = ([{'role': 'user', 'content': 'synthetic'}], [], None, '')
        result = await self.scope['_generate_summary'](self.request, self.user, PERSONAL, self.platform_models, *args)
        self.assertEqual(result, 'PERSONAL')
        self.platform.assert_not_awaited()
        self.browser.reset_mock()
        for name in [PERSONAL, 'pl-personal-' + 'c' * 32 + '.other']:
            with self.subTest(model=name), self.assertRaises(HTTPFailure) as error:
                await self.scope['_generate_summary'](self.request_for(False), self.user, name, self.platform_models, *args)
            self.assertEqual(error.exception.detail['code'], 'personal_connection_session_required')
        self.platform.assert_not_awaited()
        self.browser.assert_not_awaited()
        for model in [FREE, PAID[2], 'old-unknown-model']:
            request = self.request_for(False)
            request.state.model_id = model
            self.platform.reset_mock()
            await self.scope['_generate_summary'](request, self.user, model, self.platform_models, *args)
            self.assertEqual(self.platform.call_args.kwargs['form_data']['model'], FREE if model == FREE else PAID[0])

    async def test_summary_model_rewrite_is_rejected(self):
        self.scope['apply_params_to_form_data'] = lambda payload, model, params: {**payload, 'model': PAID[0]}
        with self.assertRaises(HTTPFailure) as error:
            await self.scope['_generate_summary'](self.request, self.user, PERSONAL, self.platform_models,
                [{'role': 'user', 'content': 'synthetic'}], [], None, '')
        self.assertEqual(error.exception.detail['code'], 'personal_connection_model_changed')
        self.platform.assert_not_awaited()
        self.browser.assert_not_awaited()

    async def test_initial_title_background_uses_ctx_metadata_without_mutating_parent(self):
        # 原生初始标题可早于 request.state.metadata 建立；运行真实 handler → task → socket。
        original = self.request_for(True, metadata=False)
        messages = {'u': {'id': 'u', 'role': 'user', 'content': 'synthetic'},
                    'synthetic-response': {'id': 'synthetic-response', 'role': 'assistant', 'content': '', 'model': PERSONAL}}
        chats = SimpleNamespace(get_messages_map_by_chat_id=AsyncMock(return_value=messages),
            update_chat_title_by_id=AsyncMock(), update_chat_tags_by_id=AsyncMock(),
            upsert_message_to_chat_by_id_and_message_id=AsyncMock())
        self.scope.update({'Chats': chats, 'get_message_list': lambda mapping, message_id: list(mapping.values()),
                          'replace_skill_mentions_with_labels': lambda value: value,
                          'review_memory_after_turn': AsyncMock()})
        self.browser.return_value = {'choices': [{'message': {'content': '{"title":"Personal title"}'}}]}
        load_functions(self.patched['utils/middleware.py'], ['background_tasks_handler'], self.scope)
        await self.scope['background_tasks_handler']({'request': original, 'form_data': self.form(), 'user': self.user,
            'metadata': self.metadata, 'tasks': {'TITLE_GENERATION': True}, 'event_emitter': self.emitter})
        chats.update_chat_title_by_id.assert_awaited_once_with('synthetic-chat', 'Personal title')
        self.assertFalse(hasattr(original.state, 'metadata'))
        self.assertEqual(self.event_lookup.call_args.args[0]['session_id'], self.metadata['session_id'])
        self.assertEqual(self.event_lookup.call_args.args[0]['user_id'], self.user.id)
        self.platform.assert_not_awaited()

    async def test_direct_main_stream_uses_native_socket_queue(self):
        class Stream:
            def __init__(self, iterator, **kwargs):
                self.body_iterator = iterator
        self.scope['StreamingResponse'] = Stream

        async def reply(event_name, event, **kwargs):
            channel = event['data']['data']['channel']
            await self.scope['EVENT_QUEUES'][channel].put('data: {"choices":[]}')
            await self.scope['EVENT_QUEUES'][channel].put({'done': True})
            return {'status': True}
        self.browser.side_effect = reply
        form = {**self.form(), 'stream': True}
        result = await self.scope['generate_chat_completion'](self.request, form, self.user)
        self.assertEqual([part async for part in result.body_iterator], ['data: {"choices":[]}\n\n'])
        self.assertEqual(self.scope['EVENT_QUEUES'], {})
        self.platform.assert_not_awaited()

    async def test_personal_stream_idle_and_total_timeouts_emit_error_and_release_queue(self):
        class Stream:
            def __init__(self, iterator, **kwargs):
                self.body_iterator = iterator
        self.scope['StreamingResponse'] = Stream
        for now, expected_timeout in [(0, 90), (700, 0)]:
            with self.subTest(now=now):
                wait_for = AsyncMock(side_effect=TimeoutError)

                async def bounded_wait(awaitable, timeout):
                    # 关闭本次尚未等待的 queue.get coroutine；不让测试制造悬空协程。
                    awaitable.close()
                    return await wait_for(timeout=timeout)

                self.scope['asyncio'] = SimpleNamespace(**{name: getattr(asyncio, name) for name in
                    ['Queue', 'timeout', 'create_task', 'wait', 'gather']}, wait_for=bounded_wait)
                clock = iter([0, now])
                self.scope['time'] = SimpleNamespace(monotonic=lambda: next(clock))
                self.browser.return_value = {'status': True}
                result = await self.scope['generate_chat_completion'](self.request, {**self.form(), 'stream': True}, self.user)
                chunks = [part async for part in result.body_iterator]
                self.assertEqual(len(chunks), 1)
                self.assertEqual(json.loads(chunks[0][6:])['error']['code'], 'personal_connection_stream_timeout')
                wait_for.assert_awaited_once_with(timeout=expected_timeout)
                self.assertEqual(self.scope['EVENT_QUEUES'], {})
        self.platform.assert_not_awaited()

    async def test_legacy_direct_stream_does_not_acquire_new_queue_timeout(self):
        class Stream:
            def __init__(self, iterator, **kwargs):
                self.body_iterator = iterator
        self.scope['StreamingResponse'] = Stream
        wait_for = AsyncMock(side_effect=AssertionError('legacy direct must keep native queue wait'))
        self.scope['asyncio'] = SimpleNamespace(**{name: getattr(asyncio, name) for name in
            ['Queue', 'timeout', 'create_task', 'wait', 'gather']}, wait_for=wait_for)
        legacy = 'legacy-direct.example'
        request = self.request_for(True)
        request.state.model = {**self.personal_model, 'id': legacy}
        request.state.model_id = legacy

        async def reply(event_name, event, **kwargs):
            channel = event['data']['data']['channel']
            await self.scope['EVENT_QUEUES'][channel].put({'done': True})
            return {'status': True}

        self.browser.side_effect = reply
        result = await self.scope['generate_chat_completion'](request, {**self.form(legacy), 'stream': True}, self.user)
        self.assertEqual([part async for part in result.body_iterator], [])
        wait_for.assert_not_awaited()
        self.assertEqual(self.scope['EVENT_QUEUES'], {})

    def test_exact_namespace_only_and_no_context_cannot_select_platform_task(self):
        select = self.scope['get_task_model_id']
        with self.assertRaises(HTTPFailure) as error:
            select(PERSONAL, '', PAID[0], self.platform_models)
        self.assertEqual(error.exception.detail['code'], 'personal_connection_session_required')
        for old in ['pl-personal-old.model', 'pl-personal-' + 'A' * 32 + '.model', 'pl-personal-' + 'a' * 31 + '.model']:
            self.assertEqual(select(old, '', PAID[0], self.platform_models), PAID[0])

    def test_native_and_free_baselines_apply_and_unrelated_functions_are_byte_identical(self):
        for sources in [self.original, self.free]:
            for path, source in sources.items():
                with self.subTest(path=path):
                    patched = patch_file(source, path)
                    originals = {n.name: ast.get_source_segment(source, n) for n in ast.parse(source).body
                                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                    changed = {name for p, name in FUNCTION_SHA256 if p == path}
                    for node in ast.parse(patched).body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in changed:
                            self.assertEqual(ast.get_source_segment(patched, node), originals[node.name])
                    with self.assertRaises(ValueError):
                        patch_file(patched, path)

    def test_rejects_source_drift_and_unsupported_paths(self):
        with self.assertRaises(ValueError):
            patch_file(self.free['utils/task.py'].replace('task_model_id = default_model_id', 'task_model_id = "drift"'), 'utils/task.py')
        with self.assertRaises(ValueError):
            patch_file('', 'routers/unknown.py')

    def test_prepare_reads_all_targets_without_writing(self):
        before = {path: (SOURCE / path).read_bytes() for path in TARGET_FILES}
        result = prepare(SOURCE)
        self.assertEqual(set(result), set(TARGET_FILES))
        self.assertEqual(before, {path: (SOURCE / path).read_bytes() for path in TARGET_FILES})


if __name__ == '__main__':
    unittest.main()
