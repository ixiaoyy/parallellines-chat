"""执行实际 ASGI 审计 middleware；仅日志与身份依赖为桩，不连接数据库/服务器。"""
import ast
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from enum import Enum
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, AsyncGenerator, Dict, MutableMapping, Optional, cast
import unittest
import uuid

from starlette.requests import Request

import audit


HERE = Path(__file__).parent
BASELINE = Path(os.environ.get('PERSONAL_CONNECTIONS_BASELINE', HERE.parents[1] / '.build/unified-login-20261115/source'))
SOURCE = BASELINE / 'backend/open_webui/utils/audit.py'
KEY = 'synthetic-personal-api-key-not-real'


class FakeUser:
    """原生 middleware 只需已核身份的元数据，不读取任何凭据。"""
    def model_dump(self, include=None):
        value = {'id': 'fixture-owner', 'name': 'Fixture', 'email': 'fixture@example.invalid', 'role': 'user'}
        return {key: value[key] for key in include} if include else value


class CapturingLogger:
    """保留实际 AuditLogger.write，只捕获它最终提交的结构化日志事件。"""
    def __init__(self):
        self.events = []
        self.errors = []

    def bind(self, **kwargs):
        return self

    def log(self, level, message, **entry):
        self.events.append(entry)

    def error(self, *args, **kwargs):
        self.errors.append(args)

    def debug(self, *args, **kwargs):
        self.errors.append(args)

    def warning(self, *args, **kwargs):
        self.errors.append(args)


def load_native_middleware(source, *, max_body_size=64):
    """只剥离外部导入；类、正文采集、ASGI 包装、脱敏与事件构造均执行真实源码。"""
    names = {'AuditLogEntry', 'AuditLevel', 'AuditLogger', 'AuditContext', 'redact_passwords', 'AuditLoggingMiddleware'}
    original = ast.parse(source)
    selected = [node for node in original.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    if {node.name for node in selected} != names:
        raise AssertionError('native audit consumer inventory changed')
    tree = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *selected], type_ignores=[])
    ast.fix_missing_locations(tree)
    logger = CapturingLogger()
    module = ModuleType('personal_audit_native_fixture')
    module.__dict__.update({
        're': re, 'uuid': uuid, 'asynccontextmanager': asynccontextmanager, 'asdict': asdict,
        'dataclass': dataclass, 'Enum': Enum, 'Any': Any, 'AsyncGenerator': AsyncGenerator,
        'Dict': Dict, 'MutableMapping': MutableMapping, 'Optional': Optional, 'cast': cast,
        'MAX_BODY_LOG_SIZE': max_body_size, 'AUDIT_LOG_LEVEL': 'REQUEST_RESPONSE', 'logger': logger,
        'UserModel': FakeUser, 'Request': Request,
    })
    sys.modules[module.__name__] = module
    exec(compile(tree, str(SOURCE), 'exec'), module.__dict__)
    return module, logger


async def exercise(source, path, *, level='REQUEST_RESPONSE', method='POST', get_enabled=False,
                   request_body=None, response_body=None, status=207):
    """穿过原生 receive/send 包装并捕获最终日志，同时确认请求/响应原字节仍正确转发。"""
    module, logger = load_native_middleware(source)
    module.AUDIT_LOG_LEVEL = level
    request_body = request_body if request_body is not None else json.dumps({'ui': {'directConnections': {'OPENAI_API_KEYS': [KEY]}}}).encode()
    response_body = response_body if response_body is not None else request_body
    incoming = [request_body[:17], request_body[17:]]
    received = bytearray()
    sent = []

    async def receive():
        chunk = incoming.pop(0)
        return {'type': 'http.request', 'body': chunk, 'more_body': bool(incoming)}

    async def send(message):
        sent.append(message)

    async def app(scope, wrapped_receive, wrapped_send):
        while True:
            message = await wrapped_receive()
            received.extend(message.get('body', b''))
            if not message.get('more_body'):
                break
        await wrapped_send({'type': 'http.response.start', 'status': status, 'headers': []})
        await wrapped_send({'type': 'http.response.body', 'body': response_body[:23], 'more_body': True})
        await wrapped_send({'type': 'http.response.body', 'body': response_body[23:], 'more_body': False})

    middleware = module.AuditLoggingMiddleware(app, audit_level=module.AuditLevel(level), audit_get_requests=get_enabled)
    scope = {'type': 'http', 'http_version': '1.1', 'asgi': {'version': '3.0'}, 'scheme': 'https',
             'method': method, 'path': path, 'raw_path': path.encode(), 'root_path': '', 'query_string': b'raw=true',
             'headers': [(b'host', b'fixture.invalid'), (b'user-agent', b'fixture')],
             'client': ('127.0.0.1', 12345), 'server': ('fixture.invalid', 443),
             'state': {'token': 'synthetic-session', 'user': FakeUser()}}
    await middleware(scope, receive, send)
    if received != request_body:
        raise AssertionError('middleware altered original request bytes')
    if b''.join(message.get('body', b'') for message in sent) != response_body:
        raise AssertionError('middleware altered original response bytes')
    if sent[0]['status'] != status:
        raise AssertionError('middleware altered response status')
    return logger


class AuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = SOURCE.read_text(encoding='utf-8')
        self.patched = audit.patch_file(self.original)

    def tearDown(self):
        sys.modules.pop('personal_audit_native_fixture', None)

    async def test_settings_post_request_and_response_levels_keep_metadata_without_secrets(self):
        for level in ('REQUEST', 'REQUEST_RESPONSE'):
            for path in ('/api/v1/users/user/settings/update', '/api/v1/users/user/settings/update/'):
                with self.subTest(level=level, path=path):
                    logger = await exercise(self.patched, path, level=level)
                    self.assertEqual(logger.errors, [])
                    self.assertEqual(len(logger.events), 1)
                    event = logger.events[0]
                    self.assertNotIn(KEY, json.dumps(event))
                    self.assertEqual(event['request_object'], '')
                    self.assertEqual(event['response_object'], '')
                    self.assertEqual(event['user']['id'], 'fixture-owner')
                    self.assertEqual(event['verb'], 'POST')
                    self.assertEqual(event['audit_level'], level)
                    self.assertIn(path, event['request_uri'])
                    self.assertTrue(event['id'])
                    # 原生 REQUEST 级别只采请求；REQUEST_RESPONSE 的状态采集应保留。
                    self.assertEqual(event['response_status_code'], 207 if level == 'REQUEST_RESPONSE' else None)

    async def test_settings_get_with_audit_enabled_never_logs_decrypted_keys(self):
        for path in ('/api/v1/users/user/settings', '/api/v1/users/user/settings/'):
            with self.subTest(path=path):
                logger = await exercise(self.patched, path, method='GET', get_enabled=True, request_body=b'')
                self.assertEqual(logger.errors, [])
                self.assertEqual(len(logger.events), 1)
                self.assertEqual(logger.events[0]['response_status_code'], 207)
                self.assertEqual(logger.events[0]['response_object'], '')
                self.assertNotIn(KEY, json.dumps(logger.events))

    async def test_long_truncated_and_invalid_json_bodies_are_never_collected(self):
        bodies = [b'{"OPENAI_API_KEYS":["' + KEY.encode() + b'x' * 10000,
                  b'x' * 3000 + KEY.encode(), KEY.encode() + b'not-json' * 1000]
        for body in bodies:
            with self.subTest(length=len(body)):
                logger = await exercise(self.patched, '/api/v1/users/user/settings/update', request_body=body)
                event = logger.events[0]
                self.assertEqual(event['request_object'], '')
                self.assertEqual(event['response_object'], '')
                self.assertNotIn(KEY, json.dumps(event))

    async def test_unrelated_and_similar_paths_keep_original_body_and_password_redaction(self):
        request = b'{"theme":"dark","password":"synthetic-password"}'
        response = b'{"theme":"dark","ok":true}'
        for path in ('/api/v1/users/user/info', '/api/v1/users/user/settings/history', '/api/v1/users/user/settings-backup'):
            with self.subTest(path=path):
                baseline = await exercise(self.original, path, request_body=request, response_body=response)
                patched = await exercise(self.patched, path, request_body=request, response_body=response)
                self.assertEqual(patched.errors, [])
                for field in ('request_object', 'response_object', 'response_status_code', 'audit_level', 'verb', 'user'):
                    self.assertEqual(patched.events[0][field], baseline.events[0][field])
                self.assertNotIn('synthetic-password', patched.events[0]['request_object'])
                self.assertEqual(patched.events[0]['response_object'], response.decode())

    async def test_disabled_get_auditing_and_auth_body_protection_are_unchanged(self):
        logger = await exercise(self.patched, '/api/v1/users/user/settings', method='GET', request_body=b'')
        self.assertEqual(logger.events, [])
        for path in ('/api/v1/auths/signin', '/oauth/oidc/callback'):
            logger = await exercise(self.patched, path)
            self.assertEqual(logger.events[0]['request_object'], '')
            self.assertEqual(logger.events[0]['response_object'], '')

    def test_prepare_is_read_only_and_duplicate_or_drift_is_rejected(self):
        result = audit.prepare(SOURCE.parents[1])
        self.assertEqual(result, {'utils/audit.py': self.patched})
        self.assertEqual(SOURCE.read_text(encoding='utf-8'), self.original)
        with self.assertRaisesRegex(ValueError, 'anchor_changed'):
            audit.patch_file(self.patched)
        with self.assertRaisesRegex(ValueError, 'anchor_changed'):
            audit.patch_file(self.original.replace('capture_body = not (', 'capture_body = bool('))


if __name__ == '__main__':
    unittest.main()
