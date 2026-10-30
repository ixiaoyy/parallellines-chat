"""Apply the model-authorization toast to the exact pinned frontend source."""
import argparse
import hashlib
from pathlib import Path

UPSTREAM_COMMIT = 'f7ac7ee10962ddec0017f5c29b6740d9b9c5faca'
INDEX_SHA256 = '5a4962e03abfaa25e169c065ea6fffc51ea6cc1cb7e70056be032ff7c9f2e6bf'
MODULE_NAME = 'main-oauth-auth-recovery.mjs'


def patch(source):
    imports = "import { getOpenAIModelsDirect } from './openai';\n"
    if source.count(imports) != 1:
        raise ValueError('Unexpected frontend imports')
    source = source.replace(imports, imports + (
        "import { toast } from 'svelte-sonner';\n"
        "import { showMainOAuthAuthorizationRequired, dismissMainOAuthAuthorizationRequired } "
        "from './main-oauth-auth-recovery.mjs';\n"
    ), 1)
    start = source.index('export const getModels = async (')
    end = source.index('export const unloadModel = async ', start)
    method = source[start:end]
    original = (
        '\t\t.then(async (res) => {\n'
        '\t\t\tif (!res.ok) throw await res.json();\n'
        '\t\t\treturn res.json();\n'
        '\t\t})\n'
    )
    replacement = (
        '\t\t.then(async (res) => {\n'
        '\t\t\tif (!res.ok) {\n'
        '\t\t\t\tconst responseError = await res.json();\n'
        '\t\t\t\tshowMainOAuthAuthorizationRequired(res.status, responseError, toast, (path) =>\n'
        '\t\t\t\t\twindow.location.assign(path)\n'
        '\t\t\t\t);\n'
        '\t\t\t\tthrow responseError;\n'
        '\t\t\t}\n'
        '\t\t\tconst responseBody = await res.json();\n'
        '\t\t\tdismissMainOAuthAuthorizationRequired(toast);\n'
        '\t\t\treturn responseBody;\n'
        '\t\t})\n'
    )
    if method.count(original) != 1:
        raise ValueError('Unexpected getModels response handler')
    return source[:start] + method.replace(original, replacement, 1) + source[end:]


def apply(root, check=False):
    index = root / 'src/lib/apis/index.ts'
    module = index.parent / MODULE_NAME
    content = index.read_bytes()
    if hashlib.sha256(content).hexdigest() != INDEX_SHA256:
        raise ValueError('Frontend differs from pinned upstream; refusing patch')
    if module.exists():
        raise ValueError('Recovery module already exists; refusing overwrite')
    patched = patch(content.decode('utf-8'))
    module_content = (Path(__file__).parent / MODULE_NAME).read_bytes()
    if not check:
        module.write_bytes(module_content)
        index.write_text(patched, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Checkout of the pinned Open WebUI commit')
    parser.add_argument('--check', action='store_true', help='Validate without writing')
    args = parser.parse_args()
    apply(args.root, args.check)
