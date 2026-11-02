"""在固定源码函数上验证免费任务与原生 Responses，不导入应用、不访问数据库或模型。"""
import ast
import copy
import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock

from apply_openrouter_free import FREE_MODEL, FUNCTION_SHA256, apply, patch_file


SOURCE = Path(os.environ.get('OPENWEBUI_SOURCE_ROOT', str(
    Path(__file__).parents[1] / '.build/performance-build/open-webui-f7ac7ee10962ddec0017f5c29b6740d9b9c5faca/backend/open_webui'
)))
PAID = ['gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol']


class HTTPFailure(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(str(detail))


def load_functions(source, names, scope):
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in nodes:
        node.decorator_list = []
        node.returns = None
        for arg in node.args.args:
            arg.annotation = None
        node.args.defaults = [ast.Constant(value=None) if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'Depends' else value for value in node.args.defaults]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<reviewed-functions>', 'exec'), scope)


class FreeTaskTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_dir():
            raise unittest.SkipTest('set OPENWEBUI_SOURCE_ROOT to the pinned Open WebUI backend source')
        cls.task_source = (SOURCE / 'utils/task.py').read_text(encoding='utf-8')
        cls.router_source = (SOURCE / 'routers/tasks.py').read_text(encoding='utf-8')
        cls.chat_source = (SOURCE / 'utils/chat.py').read_text(encoding='utf-8')
        cls.patched_task = patch_file(cls.task_source, 'utils/task.py')
        cls.patched_router = patch_file(cls.router_source, 'routers/tasks.py')
        cls.patched_chat = patch_file(cls.chat_source, 'utils/chat.py')

    def setUp(self):
        self.models = {model: {'id': model, 'connection_type': 'external', 'info': {'params': {}}}
                       for model in [FREE_MODEL, *PAID]}
        self.scope = {'HTTPException': HTTPFailure, 'log': logging.getLogger('free-task-test')}
        load_functions(self.patched_task, ['get_task_model_id'], self.scope)
        config = {
            'task.model.default': 'local-paid', 'task.model.external': PAID[0],
            'task.model.params': {'max_tokens': 64}, 'task.autocomplete.input_max_length': 200,
        }

        async def get(key):
            return config.get(key, True if key.endswith('.enable') else '')

        async def get_many(*keys):
            return {key: config[key] for key in keys if key in config}

        async def pipeline(request, payload, user, models):
            return payload

        async def template(*args, **kwargs):
            return 'synthetic task only'

        self.network = AsyncMock(return_value={'choices': [{'message': {'content': 'OK'}}]})
        self.scope.update({
            'Config': SimpleNamespace(get=get, get_many=get_many),
            'process_pipeline_inlet_filter': pipeline,
            'apply_params_to_form_data': lambda payload, model, params: {**payload, **{k: v for k, v in (params or {}).items() if k not in payload}},
            'generate_chat_completion': self.network,
            'status': SimpleNamespace(HTTP_200_OK=200, HTTP_400_BAD_REQUEST=400, HTTP_404_NOT_FOUND=404, HTTP_500_INTERNAL_SERVER_ERROR=500),
            'ERROR_MESSAGES': SimpleNamespace(MODEL_NOT_FOUND=lambda: 'missing'),
            'JSONResponse': lambda status_code, content: SimpleNamespace(status_code=status_code, content=content),
            'TASKS': SimpleNamespace(**{name: name for name in ['TITLE_GENERATION', 'FOLLOW_UP_GENERATION', 'TAGS_GENERATION', 'IMAGE_PROMPT_GENERATION', 'QUERY_GENERATION', 'AUTOCOMPLETE_GENERATION', 'EMOJI_GENERATION', 'MOA_RESPONSE_GENERATION']}),
        })
        for name in ['title', 'follow_up', 'tags', 'image_prompt', 'query', 'autocomplete', 'emoji', 'moa_response']:
            self.scope[name + '_generation_template'] = template
        for name in ['TITLE', 'FOLLOW_UP', 'TAGS', 'IMAGE_PROMPT', 'QUERY', 'AUTOCOMPLETE', 'EMOJI', 'MOA']:
            self.scope['DEFAULT_' + name + '_GENERATION_PROMPT_TEMPLATE'] = 'synthetic-template'
        self.scope['moa_response_generation_template'] = lambda *args: 'synthetic task only'
        self.functions = ['generate_title', 'generate_follow_ups', 'generate_chat_tags', 'generate_image_prompt', 'generate_queries', 'generate_autocompletion', 'generate_emoji', 'generate_moa_response']
        load_functions(self.patched_router, ['get_task_model_generation_config', 'apply_task_model_params', *self.functions], self.scope)
        self.request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS=self.models)))
        self.user = SimpleNamespace(id='synthetic-user', email='synthetic@example.invalid')

    def form(self, model):
        return {'model': model, 'messages': [{'role': 'user', 'content': 'synthetic'}], 'prompt': 'synthetic', 'type': 'web_search', 'responses': ['synthetic'], 'stream': False}

    def test_task_selection_preserves_paid_and_local_behavior(self):
        selection = self.scope['get_task_model_id']
        self.assertEqual(selection(FREE_MODEL, 'local-paid', PAID[0], self.models), FREE_MODEL)
        for model in PAID:
            self.assertEqual(selection(model, '', PAID[0], self.models), PAID[0])
        local = {**self.models, 'local': {'connection_type': 'local'}, 'local-paid': {}}
        self.assertEqual(selection('local', 'local-paid', PAID[0], local), 'local-paid')
        self.assertEqual(selection('unknown', '', 'missing', self.models), 'unknown')

    async def test_all_task_endpoints_keep_free_model_despite_paid_global_default(self):
        for name in self.functions:
            with self.subTest(task=name):
                self.network.reset_mock()
                result = await self.scope[name](self.request, self.form(FREE_MODEL), self.user)
                self.assertEqual(result['choices'][0]['message']['content'], 'OK')
                self.network.assert_awaited_once()
                self.assertEqual(self.network.call_args.kwargs['form_data']['model'], FREE_MODEL)

    async def test_pipeline_cannot_redirect_free_task_to_paid_model(self):
        async def redirect(request, payload, user, models):
            return {**payload, 'model': PAID[0]}
        self.scope['process_pipeline_inlet_filter'] = redirect
        for name in self.functions:
            with self.subTest(task=name):
                self.network.reset_mock()
                with self.assertRaises(HTTPFailure) as caught:
                    await self.scope[name](self.request, self.form(FREE_MODEL), self.user)
                self.assertEqual(caught.exception.detail, {'code': 'free_task_model_changed'})
                self.network.assert_not_awaited()

    async def test_paid_task_still_uses_configured_luna(self):
        await self.scope['generate_title'](self.request, self.form(PAID[2]), self.user)
        self.assertEqual(self.network.call_args.kwargs['form_data']['model'], PAID[0])

    async def test_paid_pipeline_model_selection_is_not_rewritten(self):
        async def redirect(request, payload, user, models):
            return {**payload, 'model': PAID[1]}
        self.scope['process_pipeline_inlet_filter'] = redirect
        await self.scope['generate_title'](self.request, self.form(PAID[2]), self.user)
        self.assertEqual(self.network.call_args.kwargs['form_data']['model'], PAID[1])

    def test_middleware_callers_use_original_model(self):
        tree = ast.parse((SOURCE / 'utils/middleware.py').read_text(encoding='utf-8'))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'get_task_model_id']
        self.assertEqual(len(calls), 2)
        self.assertEqual([ast.unparse(node.args[0]) for node in sorted(calls, key=lambda node: node.lineno)], ["body['model']", "form_data['model']"])

    def test_patch_rejects_drift_and_reapplication(self):
        with self.assertRaises(ValueError): patch_file(self.patched_task, 'utils/task.py')
        with self.assertRaises(ValueError): patch_file(self.task_source.replace('task_model_id = default_model_id', 'task_model_id = "drift"'), 'utils/task.py')

    def test_source_application_validates_all_files_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = set(path for path, _ in FUNCTION_SHA256)
            for path in paths:
                (root / path).parent.mkdir(exist_ok=True)
                (root / path).write_bytes((SOURCE / path).read_bytes())
            (root / 'routers/tasks.py').write_text(self.router_source.replace('    model = models.get(', '    model = {} or models.get(', 1), encoding='utf-8')
            before = (root / 'utils/task.py').read_bytes()
            with self.assertRaises(ValueError): apply(root)
            self.assertEqual((root / 'utils/task.py').read_bytes(), before)
            (root / 'routers/tasks.py').write_text(self.router_source, encoding='utf-8')
            receipt = apply(root)
            self.assertEqual(set(receipt), paths)
            self.assertEqual((root / 'utils/task.py').read_text(encoding='utf-8'), self.patched_task)

    def test_native_responses_preserves_free_id_zero_false_and_no_paid_converter_change(self):
        from apply_responses import patch as responses_patch
        native = (SOURCE / 'routers/openai.py').read_text(encoding='utf-8')
        scope = {'JSONCodec': json}
        load_functions(responses_patch(native), ['convert_to_responses_payload', 'convert_responses_result'], scope)
        for streaming in [False, True]:
            payload = scope['convert_to_responses_payload']({'model': FREE_MODEL, 'messages': [{'role': 'user', 'content': 'synthetic'}], 'stream': streaming, 'max_tokens': 0})
            self.assertEqual(payload['model'], FREE_MODEL)
            self.assertEqual(payload['stream'], streaming)
            self.assertEqual(payload['max_output_tokens'], 0)
            self.assertEqual(payload['input'][0]['content'][0], {'type': 'input_text', 'text': 'synthetic'})
        response = scope['convert_responses_result']({'id': 'synthetic-response', 'model': FREE_MODEL, 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}], 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        self.assertEqual(response['choices'][0]['message']['content'], 'OK')
        self.assertEqual(response['model'], FREE_MODEL)
        self.assertEqual(response['usage'], {'input_tokens': 1, 'output_tokens': 1})

    async def test_main_inlet_rewrite_cannot_make_query_task_call_paid_model(self):
        # 执行原生 main 捕获来源的语句及真实主 inlet 片段，再进入实际查询任务和发送分发函数。
        original_form = self.form(FREE_MODEL)
        main = ast.parse((SOURCE / 'main.py').read_text(encoding='utf-8'))
        handler = next(node for node in main.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'chat_completion')
        captures = [node for node in handler.body if isinstance(node, ast.Assign) and (
            any(isinstance(target, ast.Name) and target.id == 'model_id' for target in node.targets)
            or any(ast.unparse(target) == 'request.state.model_id' for target in node.targets)
        )][:2]
        self.assertEqual(len(captures), 2)
        capture_scope = {'request': self.request, 'form_data': original_form}
        exec(compile(ast.fix_missing_locations(ast.Module(body=copy.deepcopy(captures), type_ignores=[])), '<native-model-origin>', 'exec'), capture_scope)
        self.assertEqual(self.request.state.model_id, FREE_MODEL)

        async def main_pipeline(request, payload, user, models):
            return {**payload, 'model': PAID[0]}

        async def inlet_filter(**kwargs):
            return kwargs['form_data'], {}

        middleware = ast.parse((SOURCE / 'utils/middleware.py').read_text(encoding='utf-8'))
        processor = next(node for node in middleware.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload')
        pipeline_node = next(node for node in processor.body if isinstance(node, ast.Try) and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == 'process_pipeline_inlet_filter' for child in ast.walk(node)))
        filter_node = next(node for node in processor.body if isinstance(node, ast.If) and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == 'process_filter_functions' for child in ast.walk(node)))
        body = [copy.deepcopy(node) for node in processor.body if pipeline_node.lineno <= node.lineno <= filter_node.lineno]
        wrapper = ast.parse('async def run_inlet(request, form_data, user, models, model, metadata, extra_params):\n    pass\n').body[0]
        wrapper.body = [*body, ast.Return(value=ast.Name(id='form_data', ctx=ast.Load()))]
        inlet_scope = {'process_pipeline_inlet_filter': main_pipeline, 'process_filter_functions': inlet_filter, 'ENABLE_FUNCTIONS': True, 'get_filter_context': lambda request: None, 'get_filter_functions': AsyncMock(return_value=[])}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), '<native-main-inlet>', 'exec'), inlet_scope)
        rewritten = await inlet_scope['run_inlet'](self.request, original_form, self.user, self.models, self.models[FREE_MODEL], {}, {})
        self.assertEqual(rewritten['model'], PAID[0])
        dispatch_scope = {'HTTPException': HTTPFailure}
        load_functions(self.patched_chat, ['generate_chat_completion'], dispatch_scope)
        self.scope['generate_chat_completion'] = dispatch_scope['generate_chat_completion']
        result = await self.scope['generate_queries'](self.request, rewritten, self.user)
        self.assertEqual(result.status_code, 400)
        self.assertIn('free_chat_model_changed', result.content['detail'])
        self.network.assert_not_awaited()

    async def test_auto_and_manual_compaction_keep_free_model_despite_paid_default(self):
        original = (SOURCE / 'utils/context_compaction.py').read_text(encoding='utf-8')
        patched = patch_file(original, 'utils/context_compaction.py')
        tree = ast.parse(patched)
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_generate_summary')
        function.body = [node for node in function.body if not isinstance(node, ast.ImportFrom)]
        function.returns = None
        for arg in function.args.args: arg.annotation = None
        network = AsyncMock(return_value={'choices': [{'message': {'content': 'summary'}}]})
        scope = {
            'Config': SimpleNamespace(get_many=AsyncMock(return_value={'chat.context_compaction.model': PAID[0], 'task.model.params': {'max_tokens': 64}})),
            'generate_chat_completion': network, 'DEFAULT_CONTEXT_COMPACTION_PROMPT': 'synthetic',
            'get_content_from_message': lambda message: message.get('content', ''),
            'get_last_user_message': lambda messages: 'synthetic',
            'replace_prompt_variable': lambda template, prompt: template,
            'replace_messages_variable': lambda template, *args: template,
            'prompt_variables_template': lambda template, variables: template,
            'prompt_template': AsyncMock(return_value='synthetic'),
            'apply_params_to_form_data': lambda payload, model, params: payload,
            '_response_text': lambda response: response['choices'][0]['message']['content'],
            'JSONCodec': json,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), '<native-compaction>', 'exec'), scope)
        for model, expected in [(FREE_MODEL, FREE_MODEL), (PAID[2], PAID[0])]:
            network.reset_mock()
            result = await scope['_generate_summary'](self.request, self.user, model, self.models, [{'role': 'user', 'content': 'synthetic'}], [], None, '')
            self.assertEqual(result, 'summary')
            self.assertEqual(network.call_args.kwargs['form_data']['model'], expected)
        calls = [node for node in ast.walk(ast.parse(original)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_generate_summary']
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(ast.unparse(node.args[2]) == 'model_id' for node in calls))

    async def test_native_http_route_sends_free_responses_with_current_oauth_and_no_shared_key(self):
        await self.exercise_native_http_route(False)

    async def test_native_http_route_streams_free_responses_and_cleans_up(self):
        await self.exercise_native_http_route(True)

    async def test_direct_free_task_cannot_be_aliased_to_paid_after_task_guard(self):
        await self.exercise_native_http_route(False, alias=PAID[0], expected_error='free_chat_provider_changed')

    async def test_free_origin_rejects_non_oauth_provider_before_credentials_or_network(self):
        await self.exercise_native_http_route(False, untrusted=True, expected_error='free_chat_provider_changed')

    async def exercise_native_http_route(self, streaming, alias=None, untrusted=False, expected_error=None):
        from apply_responses import patch as responses_patch
        response_json = {'id': 'synthetic-response', 'model': FREE_MODEL, 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        sse = b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"OK"}\n\nevent: response.completed\ndata: {"type":"response.completed"}\n\n'

        async def chunks(content):
            yield content

        class Stream:
            def __init__(self, body_iterator, status_code, headers):
                self.body_iterator, self.status_code, self.headers = body_iterator, status_code, headers

        response = SimpleNamespace(status=200, headers={'Content-Type': 'text/event-stream' if streaming else 'application/json'}, content=sse, json=AsyncMock(return_value=response_json))
        session = SimpleNamespace(request=AsyncMock(return_value=response))
        headers = AsyncMock(return_value=({'Authorization': 'Bearer synthetic-main-session'}, None))
        cleanup = AsyncMock()
        scope = {
            'Config': SimpleNamespace(get=AsyncMock(return_value=True)),
            'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=SimpleNamespace(base_model_id=alias, params=SimpleNamespace(model_dump=lambda: {})) if alias else None)),
            'BYPASS_MODEL_ACCESS_CONTROL': False,
            'check_model_access': AsyncMock(),
            'get_openai_connection': AsyncMock(return_value=('https://api.pingxingxian.space/oauth-relay/v1', '', {'auth_type': 'bearer' if untrusted else 'system_oauth', 'api_type': 'responses', 'forward_cookies': False, 'headers': {}})),
            'get_headers_and_cookies': headers,
            'strip_provider_model_prefix': lambda model, prefix: model,
            'JSONCodec': json, 're': re, 'log': logging.getLogger('free-protocol-test'),
            'get_session': AsyncMock(return_value=session), 'get_client_timeout': lambda stream: 'bounded-native-timeout',
            'AIOHTTP_CLIENT_SESSION_SSL': True, 'cleanup_response': cleanup,
            'stream_chunks_handler': chunks, 'StreamingResponse': Stream,
            '_clean_proxy_headers': lambda values: dict(values),
            'HTTPException': HTTPFailure,
            'ERROR_MESSAGES': SimpleNamespace(MODEL_NOT_FOUND=lambda: 'missing', SERVER_CONNECTION_ERROR='connection failed'),
        }
        native = responses_patch(patch_file((SOURCE / 'routers/openai.py').read_text(encoding='utf-8'), 'routers/openai.py'))
        load_functions(native, ['is_openai_new_model', 'openai_reasoning_model_handler', 'convert_to_responses_payload', 'convert_responses_result', 'generate_chat_completion'], scope)
        load_functions((SOURCE / 'utils/session_pool.py').read_text(encoding='utf-8'), ['stream_wrapper'], scope)
        request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={model: {'urlIdx': 0} for model in [FREE_MODEL, *PAID]})))
        if expected_error:
            with self.assertRaises(HTTPFailure) as caught:
                await scope['generate_chat_completion'](request, {'model': FREE_MODEL, 'messages': [{'role': 'user', 'content': 'synthetic'}], 'stream': streaming, 'max_tokens': 64}, self.user)
            self.assertEqual(caught.exception.detail, {'code': expected_error})
            session.request.assert_not_awaited()
            headers.assert_not_awaited()
            return
        result = await scope['generate_chat_completion'](request, {'model': FREE_MODEL, 'messages': [{'role': 'user', 'content': 'synthetic'}], 'stream': streaming, 'max_tokens': 64}, self.user)
        session.request.assert_awaited_once()
        sent = session.request.call_args.kwargs
        self.assertEqual(sent['url'], 'https://api.pingxingxian.space/oauth-relay/v1/responses')
        self.assertEqual(sent['headers'], {'Authorization': 'Bearer synthetic-main-session'})
        self.assertIsNone(sent['cookies'])
        self.assertEqual(sent['timeout'], 'bounded-native-timeout')
        self.assertEqual(json.loads(sent['data'])['model'], FREE_MODEL)
        self.assertEqual(json.loads(sent['data'])['max_output_tokens'], 64)
        self.assertEqual(headers.call_args.args[2], '')
        if streaming:
            self.assertEqual(b''.join([part async for part in result.body_iterator]), sse)
        else:
            self.assertEqual(result['choices'][0]['message']['content'], 'OK')
        cleanup.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
