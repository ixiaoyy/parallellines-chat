"""固定 Open WebUI 的个人直连费用隔离；只生成源码补丁，不读密钥或调用服务。"""
import ast
import hashlib
from pathlib import Path


# 无 WebSocket 上下文的历史入口只能识别本次保留的精确命名空间，旧模型 ID 不受影响。
PERSONAL_MODEL_PATTERN = r'pl-personal-[0-9a-f]{32}\..+'
FUNCTION_SHA256 = {
    ('utils/task.py', 'get_task_model_id'): (
        '635af659a1662f4b8133c08210688a27085fd023640040e66d293a671178e080',
        'c4b95e479bdf98d2674c9cce8e874764be155b5fcdc2b3abc028deb553006cb8',
    ),
    ('routers/tasks.py', 'apply_task_model_params'): (
        '5bb12320d7384ef5fc0eb6121d5d8738386eff607009346ea38a9bc385a41cf1',
        '1d2c3c44dbb1a303f7d9841c67d1b0d2430de0e8e3f9030b434efae58db19243',
    ),
    ('utils/chat.py', 'generate_direct_chat_completion'): (
        'c6e95bafe1b402c21839911395c35a2b5db85567900c0d79fd42b8cf702fbf98',
    ),
    ('utils/chat.py', 'generate_chat_completion'): (
        '05a5d1064190a3b149581209df43e9095d9226daced7acc1c4fbfda8dfe3d2ed',
        '4f82dd2b58458289b0f1bb611c95b0bdf2f1d382bb6b0f34d33c32950421c1a0',
    ),
    ('utils/context_compaction.py', '_generate_summary'): (
        '545f09b21147ce6dd7dbaba0be694cc357b66b0db264e6f7ecae73d4dc6c691e',
        '1976a1dc8b04ab11b1cdbe71fb9b1d93ab4195486f0ae96ff06aa809d610e48c',
    ),
    ('utils/middleware.py', 'background_tasks_handler'): (
        '2e4f28ec27a5ed6f59a4dfb6650f9480ffb8f3e6706551cdbaebb22eca0b9c87',
    ),
}
TARGET_FILES = tuple(dict.fromkeys(path for path, _ in FUNCTION_SHA256))

PERSONAL_TASK_SELECTION = f'''    # 个人直连的辅助任务沿用本人所选模型，不继承平台付费任务默认项。
    if models.get(default_model_id, {{}}).get('direct'):
        return default_model_id
    # 手动历史任务没有浏览器会话时明确拒绝，不能借平台默认模型继续生成。
    if isinstance(default_model_id, str) and re.fullmatch({PERSONAL_MODEL_PATTERN!r}, default_model_id):
        raise HTTPException(status_code=400, detail={{'code': 'personal_connection_session_required'}})

'''
PERSONAL_TASK_GUARD = '''    # 任务管线可改写模型；个人直连不能因此转为平台模型或另一条个人连接。
    if models.get(task_model_id, {}).get('direct') and payload.get('model') != task_model_id:
        raise HTTPException(status_code=400, detail={'code': 'personal_connection_model_changed'})

'''
PERSONAL_DISPATCH_GUARD = '''    # 以主入口建立的 direct 上下文为准，拦截主管线、工具和辅助任务改模后的发送。
    if getattr(request.state, 'direct', False):
        personal_model_id = getattr(request.state, 'model', {}).get('id')
        if not personal_model_id or form_data.get('model') != personal_model_id:
            raise HTTPException(status_code=400, detail={'code': 'personal_connection_model_changed'})

'''
PERSONAL_SESSION_GUARD = '''    # 只允许当前登录用户自己的浏览器会话承接请求，缺会话时不转平台代理。
    if not metadata.get('session_id') or metadata.get('user_id') != user.id:
        raise HTTPException(status_code=400, detail={'code': 'personal_connection_session_required'})

'''
PERSONAL_STREAM_LIMIT = f'''    # 新个人连接的浏览器若断开且未送终止帧，服务端也必须释放队列；旧 direct 时限不变。
    personal_stream = isinstance(form_data.get('model'), str) and bool(re.fullmatch({PERSONAL_MODEL_PATTERN!r}, form_data['model']))
    personal_deadline = time.monotonic() + 660 if personal_stream else None

'''
PERSONAL_SUMMARY_ORIGIN = f'''    # 自动压缩保留原个人连接；手动历史入口缺少该上下文时关闭模型摘要。
    personal_model_id = (
        getattr(request.state, 'model', {{}}).get('id')
        if getattr(request.state, 'direct', False) else None
    )
    if personal_model_id and model_id != personal_model_id:
        raise HTTPException(status_code=400, detail={{'code': 'personal_connection_model_changed'}})
    if isinstance(model_id, str) and re.fullmatch({PERSONAL_MODEL_PATTERN!r}, model_id) and not personal_model_id:
        raise HTTPException(status_code=400, detail={{'code': 'personal_connection_session_required'}})

'''
PERSONAL_BACKGROUND_METADATA = '''
    # 初始标题可早于主请求的 metadata 赋值；复制请求状态承接已核 ctx，不能串改并发主请求。
    if getattr(request.state, 'direct', False):
        request = Request(
            {**request.scope, 'state': {**request.scope.get('state', {}), 'metadata': dict(metadata)}},
            receive=request.receive,
        )
'''


def _function(source, name):
    """定位唯一顶层函数，返回节点与完整原文；版本漂移或函数重复即停止。"""
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(nodes) != 1:
        raise ValueError('personal_connection_function_missing_or_duplicated:' + name)
    return nodes[0], ast.get_source_segment(source, nodes[0])


def _insert(source, marker, addition):
    """仅在唯一已核源码锚点前插入，不接受模糊匹配或重复应用。"""
    if source.count(marker) != 1:
        raise ValueError('personal_connection_source_anchor_changed:' + marker[:70])
    return source.replace(marker, addition + marker, 1)


def patch_file(source, relative_path):
    """补丁单个后端文件并返回源码；路径相对 backend/open_webui，不写入文件。"""
    targets = [(name, hashes) for (path, name), hashes in FUNCTION_SHA256.items() if path == relative_path]
    if not targets:
        raise ValueError('personal_connection_unsupported_backend_file:' + relative_path)
    replacements = []
    for name, expected in targets:
        node, original = _function(source, name)
        if hashlib.sha256(original.encode('utf-8')).hexdigest() not in expected:
            raise ValueError('personal_connection_function_baseline_changed:' + name)
        patched = original
        if name == 'get_task_model_id':
            patched = _insert(patched, '    # Set the task model\n', PERSONAL_TASK_SELECTION)
        elif name == 'apply_task_model_params':
            patched = _insert(patched, "    model = models.get(payload.get('model')) or models.get(task_model_id)\n", PERSONAL_TASK_GUARD)
        elif name == 'generate_direct_chat_completion':
            patched = _insert(patched, "    user_id = metadata.get('user_id')\n", PERSONAL_SESSION_GUARD)
            patched = _insert(patched, "    user_id = metadata.get('user_id')\n", PERSONAL_STREAM_LIMIT)
            marker = '                        data = await queue.get()  # Wait for new messages\n'
            patched = patched.replace(marker, '''                        if personal_stream:
                            data = await asyncio.wait_for(queue.get(), timeout=min(90, max(0, personal_deadline - time.monotonic())))
                        else:
                            data = await queue.get()  # Wait for new messages
''', 1)
            patched = _insert(patched, '                except Exception as e:\n', '''                except TimeoutError:
                    # 明确结束失败流，不把长时间失联伪装成已完成回答。
                    yield f'data: {JSONCodec.dumps({"error": {"code": "personal_connection_stream_timeout", "message": "个人连接响应已超时，请检查浏览器和连接后重试。"}})}\\n\\n'
''')
        elif name == 'generate_chat_completion':
            patched = _insert(patched, "    log.debug('generate_chat_completion: %s', form_data)\n", PERSONAL_DISPATCH_GUARD)
        elif name == '_generate_summary':
            patched = _insert(patched, "    task_config = await Config.get_many(\n", PERSONAL_SUMMARY_ORIGIN)
            free_marker = "    if model_id == 'openrouter/free':\n"
            if free_marker in patched:
                patched = patched.replace(free_marker,
                    "    if personal_model_id:\n        task_model_id = personal_model_id\n    elif model_id == 'openrouter/free':\n", 1)
            else:
                marker = '    task_model_id = context_compaction_model if context_compaction_model in models else model_id\n'
                patched = patched.replace(marker,
                    '    if personal_model_id:\n        task_model_id = personal_model_id\n    else:\n    ' + marker, 1)
        elif name == 'background_tasks_handler':
            marker = "    event_emitter = ctx['event_emitter']\n"
            patched = _insert(patched, marker, '')
            patched = patched.replace(marker, marker + PERSONAL_BACKGROUND_METADATA, 1)
        start = sum(len(line) for line in source.splitlines(keepends=True)[:node.lineno - 1])
        replacements.append((start, start + len(original), patched))
    for start, end, patched in sorted(replacements, reverse=True):
        source = source[:start] + patched + source[end:]
    if relative_path == 'utils/task.py':
        source = _insert(source, 'from open_webui.config import DEFAULT_RAG_TEMPLATE\n', 'from fastapi import HTTPException\n')
    elif relative_path == 'utils/chat.py':
        source = _insert(source, 'import random\n', 'import re\n')
    elif relative_path == 'utils/context_compaction.py':
        source = _insert(source, 'from fastapi.responses import JSONResponse\n', 'from fastapi import HTTPException\n')
    ast.parse(source)
    return source


def prepare(root):
    """只读核对后端根目录，返回全部目标的新源码；调用方统一验收后负责写入。"""
    root = Path(root)
    return {path: patch_file((root / path).read_text(encoding='utf-8'), path) for path in TARGET_FILES}
