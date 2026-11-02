"""为固定 Open WebUI 版本增加免费会话及后台任务模型隔离，不改付费模型配置。"""
import ast
import hashlib
from pathlib import Path
import sys


FREE_MODEL = 'openrouter/free'
FUNCTION_SHA256 = {
    ('utils/task.py', 'get_task_model_id'): '635af659a1662f4b8133c08210688a27085fd023640040e66d293a671178e080',
    ('routers/tasks.py', 'apply_task_model_params'): '5bb12320d7384ef5fc0eb6121d5d8738386eff607009346ea38a9bc385a41cf1',
    ('routers/tasks.py', 'generate_moa_response'): 'c6d4c42bd63d30e0359fdf6c8cbb04f46a871e2546f02901b6ee75bb1dec32f4',
    ('utils/chat.py', 'generate_chat_completion'): '05a5d1064190a3b149581209df43e9095d9226daced7acc1c4fbfda8dfe3d2ed',
    ('routers/openai.py', 'generate_chat_completion'): '4505d5a1c5b08ade1a610f6eae41fa3f484efc52d0a33834935b85c6f3f42f2a',
    ('utils/context_compaction.py', '_generate_summary'): '545f09b21147ce6dd7dbaba0be694cc357b66b0db264e6f7ecae73d4dc6c691e',
}
FREE_TASK_SELECTION = '''    # 只按当前会话的原始模型判断；免费任务不继承全局付费 TASK_MODEL_EXTERNAL。
    if default_model_id == 'openrouter/free':
        return default_model_id

'''
FREE_TASK_GUARD = '''    # 管线可改写任务请求；免费会话被改为其他模型时拒绝，不能悄然发出付费请求。
    if task_model_id == 'openrouter/free' and payload.get('model') != 'openrouter/free':
        raise HTTPException(status_code=400, detail={'code': 'free_task_model_changed'})

'''
FREE_MOA_GUARD = '''    # MOA 不经过 apply_task_model_params，发送前保留相同的免费模型边界。
    if model_id == 'openrouter/free' and payload.get('model') != 'openrouter/free':
        raise HTTPException(status_code=400, detail={'code': 'free_task_model_changed'})

'''
FREE_ORIGIN_CAPTURE = '''    # 主聊天使用服务器保存的原始模型；直接任务/原生入口没有该值时捕获当前入站模型。
    # 不建立粘性全局标记，付费父会话选择免费子任务时仍保持原付费会话规则。
    original_model_id = getattr(request.state, 'model_id', None)
    free_chat_origin = original_model_id == 'openrouter/free' or (
        original_model_id is None and form_data.get('model') == 'openrouter/free'
    )

'''
FREE_DISPATCH_GUARD = '''    # 主管线或 inlet filter 改模后，查询等后台任务可能先于主回答调用模型；此处统一拒绝。
    if free_chat_origin and form_data.get('model') != 'openrouter/free':
        raise HTTPException(status_code=400, detail={'code': 'free_chat_model_changed'})

'''
FREE_ROUTE_GUARD = '''    # 免费入口只允许原生可信网关路由，不能通过 arena、直接连接或函数模型另选付费后端。
    if free_chat_origin and (
        getattr(request.state, 'direct', False) or model.get('pipe') or model.get('owned_by') in ('arena', 'ollama')
    ):
        raise HTTPException(status_code=400, detail={'code': 'free_chat_route_changed'})

'''
FREE_PROVIDER_GUARD = '''    # 别名、参数和前缀已处理完成；取 OAuth 头/发送之前再核最终模型及唯一可信连接。
    if free_chat_origin and (
        payload.get('model') != 'openrouter/free'
        or url != 'https://api.pingxingxian.space/oauth-relay/v1'
        or key != ''
        or api_config.get('auth_type') != 'system_oauth'
        or api_config.get('api_type') != 'responses'
        or api_config.get('forward_cookies') is not False
        or api_config.get('headers') not in (None, {})
    ):
        raise HTTPException(status_code=400, detail={'code': 'free_chat_provider_changed'})

'''


def function_source(source, name):
    """按顶层函数名读取唯一函数及位置，返回节点和原文；不接受缺失或重复函数。"""
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(nodes) != 1:
        raise ValueError('reviewed task function missing or duplicated: ' + name)
    return nodes[0], ast.get_source_segment(source, nodes[0])


def patch_file(source, relative_path):
    """在已核对函数内插入免费分支，返回新文件；其他函数原文保持不动。"""
    replacements = []
    for (path, name), expected in FUNCTION_SHA256.items():
        if path != relative_path:
            continue
        node, original = function_source(source, name)
        if hashlib.sha256(original.encode('utf-8')).hexdigest() != expected:
            raise ValueError('task function differs from reviewed v0.12.5 source: ' + name)
        if name == 'get_task_model_id':
            changes = [('    # Set the task model\n', FREE_TASK_SELECTION)]
        elif name == 'apply_task_model_params':
            changes = [("    model = models.get(payload.get('model')) or models.get(task_model_id)\n", FREE_TASK_GUARD)]
        elif name == 'generate_moa_response':
            changes = [('    try:\n        return await generate_chat_completion(request, form_data=payload, user=user)\n', FREE_MOA_GUARD)]
        elif relative_path == 'utils/chat.py':
            changes = [
                ("    log.debug('generate_chat_completion: %s', form_data)\n", FREE_ORIGIN_CAPTURE + FREE_DISPATCH_GUARD),
                ("    if getattr(request.state, 'direct', False) and model_id == getattr(request.state, 'model', {}).get('id'):\n", FREE_ROUTE_GUARD),
            ]
        elif relative_path == 'routers/openai.py':
            changes = [
                ("    if not await Config.get('openai.enable'):\n", FREE_ORIGIN_CAPTURE),
                ('    headers, cookies = await get_headers_and_cookies(request, url, key, api_config, metadata, user=user)\n', FREE_PROVIDER_GUARD),
            ]
        else:
            changes = []
        replacement = original
        for marker, addition in changes:
            if replacement.count(marker) != 1:
                raise ValueError('reviewed free-chat marker changed: ' + name)
            replacement = replacement.replace(marker, addition + marker, 1)
        if relative_path == 'utils/context_compaction.py':
            marker = '    task_model_id = context_compaction_model if context_compaction_model in models else model_id\n'
            if replacement.count(marker) != 1:
                raise ValueError('reviewed context-compaction marker changed')
            replacement = replacement.replace(marker, '''    # 免费会话的自动/手动压缩也必须留在免费模型，不继承独立的付费压缩默认值。
    if model_id == 'openrouter/free':
        task_model_id = model_id
    else:
''' + '    ' + marker, 1)
        start = sum(len(line) for line in source.splitlines(keepends=True)[:node.lineno - 1])
        replacements.append((start, start + len(original), replacement))
    if not replacements:
        raise ValueError('unsupported task source file')
    for start, end, replacement in sorted(replacements, reverse=True):
        source = source[:start] + replacement + source[end:]
    ast.parse(source)
    return source


def apply(root):
    """核对全部目标后写入隔离/镜像源码目录，返回实际文件摘要；不访问数据库或模型。"""
    root = Path(root)
    prepared = {}
    for relative_path in dict.fromkeys(path for path, _ in FUNCTION_SHA256):
        source = (root / relative_path).read_text(encoding='utf-8')
        prepared[relative_path] = patch_file(source, relative_path)
    # 全部文件通过原文约束后才写入，避免源版本不匹配时留下半套补丁。
    for relative_path, source in prepared.items():
        (root / relative_path).write_text(source, encoding='utf-8', newline='\n')
    return {path: hashlib.sha256(source.encode('utf-8')).hexdigest()
            for path, source in prepared.items()}


if __name__ == '__main__':
    for path, digest in apply(sys.argv[1]).items():
        print('PL_FREE_TASK_SOURCE_SHA256=' + path + ':' + digest)
