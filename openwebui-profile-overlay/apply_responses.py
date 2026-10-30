"""为固定版本的原生 Responses 转换补齐三个已批准模型的推理参数。"""
import ast
import hashlib
from pathlib import Path
import sys


CONVERTER_SHA256 = '387ee56a1a25050ee2570def60a0deb57d51a310bec930105381c83cfe3e4ceb'
MARKER = "    responses_payload = {**payload, 'input': input_items}\n"
NORMALIZE = '''
    if responses_payload.get('model') in ('gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.1-sol'):
        # 三个模型保留用户选择的推理级别；原生 reasoning.effort 优先于旧参数。
        if 'reasoning_effort' in responses_payload:
            effort = responses_payload.pop('reasoning_effort')
            if 'reasoning' not in responses_payload:
                responses_payload['reasoning'] = {'effort': effort}
            elif isinstance(responses_payload['reasoning'], dict):
                responses_payload['reasoning'] = {'effort': effort, **responses_payload['reasoning']}

        # 未指定 effort 时仍使用模型的推理默认值，不关闭推理来迁就采样参数。
        reasoning = responses_payload.get('reasoning')
        effort = reasoning.get('effort') if isinstance(reasoning, dict) else None
        responses_payload.pop('logprobs', None)
        if effort != 'none':
            for key in ('temperature', 'top_p', 'top_logprobs'):
                responses_payload.pop(key, None)
            if isinstance(responses_payload.get('include'), list):
                responses_payload['include'] = [
                    item for item in responses_payload['include']
                    if item != 'message.output_text.logprobs'
                ]
'''


def patch(source):
    """仅修改已核对的原生转换函数，允许同镜像中保留独立的登录/授权补丁。"""
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name == 'convert_to_responses_payload']
    if len(nodes) != 1:
        raise ValueError('native Responses converter missing or duplicated')
    original = ast.get_source_segment(source, nodes[0])
    if hashlib.sha256(original.encode('utf-8')).hexdigest() != CONVERTER_SHA256:
        raise ValueError('native Responses converter differs from reviewed v0.12.5 source')
    if original.count(MARKER) != 1:
        raise ValueError('native Responses payload marker changed')
    replacement = original.replace(MARKER, MARKER + NORMALIZE, 1)
    start = sum(len(line) for line in source.splitlines(keepends=True)[:nodes[0].lineno - 1])
    return source[:start] + replacement + source[start + len(original):]


if __name__ == '__main__':
    source_path = Path(sys.argv[1]) / 'routers/openai.py'
    result = patch(source_path.read_text(encoding='utf-8'))
    source_path.write_text(result, encoding='utf-8', newline='\n')
    # 供部署回执核对实际运行文件；不执行语法检查或模型调用。
    print('PL_RESPONSES_SOURCE_SHA256=' + hashlib.sha256(result.encode('utf-8')).hexdigest())
