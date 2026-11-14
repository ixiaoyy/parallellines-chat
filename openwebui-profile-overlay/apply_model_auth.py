"""仅对固定上游或已发布 Responses/免费模型基线应用模型授权恢复。"""
import hashlib
from pathlib import Path
import sys

OPENAI_SHA256 = '1f7dd58683e3c837f7b4962cd7f4a2af0c7a0709f5cdcad9dc1215bdc00a6bf5'
RELEASE_OPENAI_SHA256 = '09ec4460e0059c32c504e04306d0107abea7552999c669b3732a5ea1135ae7a4'


def replace_once(source, old, new):
    """只替换唯一已审阅片段；版本偏移和重复应用均拒绝。"""
    if source.count(old) != 1:
        raise ValueError('unexpected upstream model discovery implementation')
    return source.replace(old, new, 1)


def patch(source):
    """只改目录请求的授权和错误边界，保留 Responses 与免费会话保护。"""
    source = replace_once(source,
        'from open_webui.utils.json_codec import JSONCodec\n',
        'from open_webui.utils.json_codec import JSONCodec\n'
        'from open_webui.utils.main_model_auth import (MainModelDiscoveryError, require_main_model_authorization, check_main_model_authorization_response, is_main_models_connection)\n')
    source = replace_once(source,
        '            async with session.get(\n                url,\n',
        '            require_main_model_authorization(url, config, headers)\n'
        '            async with session.get(\n                url,\n')
    source = replace_once(source,
        '                return await response.json(loads=JSONCodec.loads)\n'
        '    except Exception as e:\n        # Handle connection error here\n',
        '                check_main_model_authorization_response(url, config, response.status)\n'
        '                return await response.json(loads=JSONCodec.loads)\n'
        '    except MainModelDiscoveryError:\n'
        '        raise\n'
        '    except Exception as e:\n'
        '        # 可信目录的网络或解析失败须显式报错；其他提供方保持原生行为。\n'
        '        if is_main_models_connection(url, config):\n'
        '            raise MainModelDiscoveryError() from e\n'
        '        # Handle connection error here\n')
    return source


def apply(root, check=False):
    """root 是隔离的 open_webui 目录；先核精确基线，check 模式不写入。"""
    source_path = Path(root) / 'routers/openai.py'
    source = source_path.read_bytes()
    if hashlib.sha256(source).hexdigest() not in (OPENAI_SHA256, RELEASE_OPENAI_SHA256):
        raise ValueError('OpenAI model discovery differs from reviewed baselines; refusing patch')
    patched = patch(source.decode('utf-8'))
    if not check:
        source_path.write_text(patched, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    apply(Path(sys.argv[1]))
