"""在已发布手动恢复/启动补丁上接入非阻塞后台恢复，不改变原启动截止。"""
import hashlib
from pathlib import Path
import sys

INDEX_SHA256 = '9c2ac41b0072cf866f14a131f163a897cb190928a2a85107d46e2097bc79ba97'
APP_LAYOUT_SHA256 = '31e1df8754c0f01c8cd99e51a545f88d612843547926b89be2c8da988b19f987'
MODULE_NAME = 'main-model-silent-recovery.mjs'


def replace_once(source, old, new):
    """只接受唯一原文，拒绝重复应用和源版本漂移。"""
    if source.count(old) != 1:
        raise ValueError('silent_recovery_frontend_marker_changed')
    return source.replace(old, new, 1)


def patch(source):
    """模型目录错误立即返回；后台恢复只在成功后通知布局单独重读目录。"""
    marker = "import { showMainOAuthAuthorizationRequired, dismissMainOAuthAuthorizationRequired } from './main-oauth-auth-recovery.mjs';\n"
    source = replace_once(source, marker, marker +
        "import { startModelAuthorizationRecovery, clearModelAuthorizationAttempt } from './main-model-silent-recovery.mjs';\n")
    start = source.index('export const getModels = async (')
    end = source.index('export const unloadModel = async ', start)
    method = source[start:end]
    old = ('\t\t\t\tshowMainOAuthAuthorizationRequired(res.status, responseError, toast, (path) =>\n'
           '\t\t\t\t\twindow.location.assign(path)\n\t\t\t\t);\n')
    new = ('\t\t\t\t// 不等待隐藏 frame，原启动组的 12 秒截止与取消合同保持不变。\n'
           "\t\t\t\tif (!base && res.status === 403 && responseError?.detail?.code === 'main_oauth_authorization_required') {\n"
           '\t\t\t\t\tsignal?.throwIfAborted();\n'
           '\t\t\t\t\tstartModelAuthorizationRecovery(token, toast);\n'
           '\t\t\t\t} else {\n'
           + ''.join('\t' + line for line in old.splitlines(keepends=True)) + '\t\t\t\t}\n')
    method = replace_once(method, old, new)
    method = replace_once(method, '\t\t\tdismissMainOAuthAuthorizationRequired(toast);\n',
                          '\t\t\tdismissMainOAuthAuthorizationRequired(toast);\n\t\t\tclearModelAuthorizationAttempt();\n')
    return source[:start] + method + source[end:]


def patch_layout(source):
    """恢复成功只触发一次新的有界目录读取；不重跑启动组、重挂 Chat 或清理草稿。"""
    marker = "\timport { createBootstrapRequests } from '$lib/utils/bootstrap';\n"
    source = replace_once(source, marker, marker +
        "\timport { MODELS_RECOVERED_EVENT, cancelModelAuthorizationRecovery } from '$lib/apis/main-model-silent-recovery.mjs';\n")
    marker = '\tconst setToolServers = async () => {\n'
    return replace_once(source, marker,
        '\t// 后台授权不阻塞启动；成功后只重取模型，沿用原生 12 秒读取与销毁取消。\n'
        '\tonMount(() => {\n'
        '\t\tconst restored = () => {\n'
        '\t\t\tif (destroyed) return;\n'
        '\t\t\tvoid startupRequests.run((signal) => setModels(signal)).catch((error) => {\n'
        "\t\t\t\tif (!destroyed) console.error('Unable to reload authorized models:', error);\n"
        '\t\t\t});\n'
        '\t\t};\n'
        '\t\twindow.addEventListener(MODELS_RECOVERED_EVENT, restored);\n'
        '\t\treturn () => {\n'
        '\t\t\twindow.removeEventListener(MODELS_RECOVERED_EVENT, restored);\n'
        '\t\t\tcancelModelAuthorizationRecovery();\n'
        '\t\t};\n'
        '\t});\n\n' + marker)


def apply(root, check=False):
    """两份精确基线全部通过后再写入，不覆盖上轮发布或已有模块。"""
    root = Path(root)
    module = root / 'src/lib/apis' / MODULE_NAME
    if module.exists():
        raise ValueError('silent_recovery_module_exists')
    prepared = {}
    for relative, expected, transform in [
        ('src/lib/apis/index.ts', INDEX_SHA256, patch),
        ('src/routes/(app)/+layout.svelte', APP_LAYOUT_SHA256, patch_layout),
    ]:
        content = (root / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('silent_recovery_frontend_baseline_changed:' + relative)
        prepared[relative] = transform(content.decode('utf-8'))
    if not check:
        module.write_bytes((Path(__file__).parent / MODULE_NAME).read_bytes())
        for relative, source in prepared.items():
            (root / relative).write_text(source, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    apply(Path(sys.argv[1]))
