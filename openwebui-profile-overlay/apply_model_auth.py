"""Apply only to the pinned official model discovery implementation."""
import hashlib
from pathlib import Path
import sys

OPENAI_SHA256 = '1f7dd58683e3c837f7b4962cd7f4a2af0c7a0709f5cdcad9dc1215bdc00a6bf5'


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('unexpected upstream model discovery implementation')
    return source.replace(old, new, 1)


def patch(source):
    source = replace_once(source,
        'from open_webui.utils.json_codec import JSONCodec\n',
        'from open_webui.utils.json_codec import JSONCodec\n'
        'from open_webui.utils.main_model_auth import (MainOAuthAuthorizationRequired, require_main_model_authorization, check_main_model_authorization_response)\n')
    source = replace_once(source,
        '            async with session.get(\n                url,\n',
        '            require_main_model_authorization(url, config, headers)\n'
        '            async with session.get(\n                url,\n')
    source = replace_once(source,
        '                return await response.json(loads=JSONCodec.loads)\n'
        '    except Exception as e:\n        # Handle connection error here\n',
        '                check_main_model_authorization_response(url, config, response.status)\n'
        '                return await response.json(loads=JSONCodec.loads)\n'
        '    except MainOAuthAuthorizationRequired:\n'
        '        raise\n'
        '    except Exception as e:\n        # Handle connection error here\n')
    return source


if __name__ == '__main__':
    source_path = Path(sys.argv[1]) / 'routers/openai.py'
    source = source_path.read_bytes()
    if hashlib.sha256(source).hexdigest() != OPENAI_SHA256:
        raise SystemExit('OpenAI model discovery differs from pinned upstream; refusing patch')
    source_path.write_text(patch(source.decode('utf-8')), encoding='utf-8', newline='\n')
