"""在已核 Chat 前端基线上追加对话发送阶段报错分类与下一步引导，不改变原有登录或消息存储契约。"""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
CHAT_SVELTE = 'src/lib/components/chat/Chat.svelte'
GUIDANCE_MODULE = 'src/lib/apis/chat-error-guidance.mjs'

IMPORT_ANCHOR = "\timport { isPersonalModel } from '$lib/apis/personal-connections-runtime.mjs';\n"
IMPORT_ADDITION = (
    "\timport { presentChatErrorGuidance, REAUTHORIZE_PATH, PLATFORM_QUOTA_URL } "
    "from '$lib/apis/chat-error-guidance.mjs';\n"
)

STORES_ANCHOR = "\t\tshowSidebar,\n"
STORES_ADDITION = "\t\tshowSettings,\n"

HANDLER_OLD = """\tconst handleOpenAIError = (error, responseMessage) => {
\t\tconst responseFailed = responseMessage.done && responseMessage.error;
\t\tconst errorMessage = getErrorMessage(error, $i18n.t('Server connection failed'));
\t\tresponseMessage.error = { content: errorMessage };
\t\tresponseMessage.done = true;
\t\tif (responseMessage.statusHistory) {
\t\t\tresponseMessage.statusHistory = responseMessage.statusHistory.filter(
\t\t\t\t(status) => status.action !== 'knowledge_search'
\t\t\t);
\t\t}
\t\thistory.messages[responseMessage.id] = responseMessage;
\t\tif (!responseFailed) toast.error(errorMessage);
\t};"""

HANDLER_NEW = """\tconst handleOpenAIError = (error, responseMessage) => {
\t\tconst responseFailed = responseMessage.done && responseMessage.error;
\t\tconst errorMessage = getErrorMessage(error, $i18n.t('Server connection failed'));
\t\tconst responseModel = $models.find((m) => m.id === responseMessage?.model);
\t\tconst guided = presentChatErrorGuidance({
\t\t\terror,
\t\t\trawMessage: errorMessage,
\t\t\tisPersonal: isPersonalModel(responseModel),
\t\t\tlocale: $i18n?.language,
\t\t\ttoast: responseFailed ? null : toast,
\t\t\tonReauthorize: () => {
\t\t\t\twindow.location.href = REAUTHORIZE_PATH;
\t\t\t},
\t\t\tonOpenPersonalConnections: () => {
\t\t\t\tshowSettings.set('connections');
\t\t\t},
\t\t\tonOpenPlatformQuota: () => {
\t\t\t\twindow.open(PLATFORM_QUOTA_URL, '_blank', 'noopener,noreferrer');
\t\t\t},
\t\t\tonRetry: responseMessage?.parentId
\t\t\t\t? () => {
\t\t\t\t\t\tregenerateResponse(responseMessage);
\t\t\t\t\t}
\t\t\t\t: null
\t\t});
\t\tresponseMessage.error = { content: guided.messageContent };
\t\tresponseMessage.done = true;
\t\tif (responseMessage.statusHistory) {
\t\t\tresponseMessage.statusHistory = responseMessage.statusHistory.filter(
\t\t\t\t(status) => status.action !== 'knowledge_search'
\t\t\t);
\t\t}
\t\thistory.messages[responseMessage.id] = responseMessage;
\t};"""


def prepare(root):
    """在 Chat.svelte 接入报错引导模块并复制纯逻辑文件。"""
    chat = (root / CHAT_SVELTE).read_text(encoding='utf-8')
    if chat.count(IMPORT_ANCHOR) != 1:
        raise ValueError('chat_error_guidance_import_anchor_changed')
    chat = chat.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_ADDITION, 1)

    if chat.count(STORES_ANCHOR) != 1:
        raise ValueError('chat_error_guidance_stores_anchor_changed')
    chat = chat.replace(STORES_ANCHOR, STORES_ADDITION + STORES_ANCHOR, 1)

    if chat.count(HANDLER_OLD) != 1:
        raise ValueError('chat_error_guidance_handler_anchor_changed')
    chat = chat.replace(HANDLER_OLD, HANDLER_NEW, 1)

    return {
        CHAT_SVELTE: chat,
        GUIDANCE_MODULE: (HERE / 'chat-error-guidance.mjs').read_text(encoding='utf-8'),
    }


def apply(root, check=False):
    """先核整套预期前后像及路径，check 模式不写源码。"""
    root = Path(root).resolve(strict=True)
    files = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))['files']
    for name, hashes in files.items():
        target = root / name
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError('chat_error_guidance_source_path_invalid:' + name)
        before = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if before != hashes['before_sha256']:
            raise ValueError('chat_error_guidance_source_baseline_changed:' + name)
    result = prepare(root)
    if result.keys() != files.keys():
        raise ValueError('chat_error_guidance_source_inventory_changed')
    for name, source in result.items():
        if hashlib.sha256(source.encode()).hexdigest() != files[name]['after_sha256']:
            raise ValueError('chat_error_guidance_source_result_changed:' + name)
    if not check:
        for name, source in result.items():
            (root / name).write_text(source, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.root, args.check)
