"""在已核 Chat 前端基线上对齐输入区按钮与当前选中模型的真实能力。"""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
MESSAGE_INPUT = 'src/lib/components/chat/MessageInput.svelte'
CAPABILITIES_MODULE = 'src/lib/apis/model-capabilities.mjs'

IMPORT_ANCHOR = "\timport type { TerminalContext } from '$lib/types';\n"
IMPORT_ADDITION = (
    "\timport {\n"
    "\t\tbuildModelCapabilitiesMap,\n"
    "\t\tfilterCapableModelIds,\n"
    "\t\tallSelectedModelsSupport\n"
    "\t} from '$lib/apis/model-capabilities.mjs';\n"
)

MAP_OLD = """\tlet modelCapabilitiesById: ModelCapabilitiesById = {};
\t$: modelCapabilitiesById = Object.fromEntries(
\t\t($models ?? []).map((model) => [model.id, model.info?.meta?.capabilities ?? {}])
\t);

\tconst getCapableModelIds = (
\t\tmodelIds: string[],
\t\tcapability: ModelCapability,
\t\tcapabilitiesById: ModelCapabilitiesById
\t) => modelIds.filter((id) => capabilitiesById[id]?.[capability] ?? true);"""

MAP_NEW = """\tlet modelCapabilitiesById: ModelCapabilitiesById = {};
\t$: modelCapabilitiesById = buildModelCapabilitiesMap($models ?? []);

\tconst getCapableModelIds = (
\t\tmodelIds: string[],
\t\tcapability: ModelCapability,
\t\tcapabilitiesById: ModelCapabilitiesById
\t) => filterCapableModelIds(modelIds, capability, capabilitiesById);"""

FILE_UPLOAD_OLD = """\tlet fileUploadEnabled: boolean = true;
\t$: fileUploadEnabled =
\t\t(getFilesystemUploadTerminal($selectedTerminalId, $terminalServers, $settings) ||
\t\t\tfileUploadCapableModels.length === selectedModelIds.length) &&
\t\t($_user?.role === 'admin' || $_user?.permissions?.chat?.file_upload);"""

FILE_UPLOAD_NEW = """\tlet fileUploadEnabled: boolean = true;
\t$: fileUploadEnabled =
\t\t(getFilesystemUploadTerminal($selectedTerminalId, $terminalServers, $settings) ||
\t\t\tallSelectedModelsSupport(selectedModelIds, fileUploadCapableModels)) &&
\t\t($_user?.role === 'admin' || $_user?.permissions?.chat?.file_upload);"""

BUTTONS_OLD = """\tlet showWebSearchButton = false;
\t$: showWebSearchButton =
\t\tselectedModelIds.length === webSearchCapableModels.length &&
\t\t$config?.features?.enable_web_search &&
\t\t($_user.role === 'admin' || $_user?.permissions?.features?.web_search);

\tlet showImageGenerationButton = false;
\t$: showImageGenerationButton =
\t\tselectedModelIds.length === imageGenerationCapableModels.length &&
\t\t$config?.features?.enable_image_generation &&
\t\t($_user.role === 'admin' || $_user?.permissions?.features?.image_generation);

\tlet showCodeInterpreterButton = false;
\t$: showCodeInterpreterButton =
\t\t!$selectedTerminalId &&
\t\tselectedModelIds.length === codeInterpreterCapableModels.length &&
\t\t$config?.features?.enable_code_interpreter &&
\t\t($_user.role === 'admin' || $_user?.permissions?.features?.code_interpreter);"""

BUTTONS_NEW = """\tlet showWebSearchButton = false;
\t$: showWebSearchButton =
\t\tallSelectedModelsSupport(selectedModelIds, webSearchCapableModels) &&
\t\t$config?.features?.enable_web_search &&
\t\t($_user.role === 'admin' || $_user?.permissions?.features?.web_search);

\tlet showImageGenerationButton = false;
\t$: showImageGenerationButton =
\t\tallSelectedModelsSupport(selectedModelIds, imageGenerationCapableModels) &&
\t\t$config?.features?.enable_image_generation &&
\t\t($_user.role === 'admin' || $_user?.permissions?.features?.image_generation);

\tlet showCodeInterpreterButton = false;
\t$: showCodeInterpreterButton =
\t\t!$selectedTerminalId &&
\t\tallSelectedModelsSupport(selectedModelIds, codeInterpreterCapableModels) &&
\t\t$config?.features?.enable_code_interpreter &&
\t\t($_user.role === 'admin' || $_user?.permissions?.features?.code_interpreter);"""


def prepare(root):
    """在 MessageInput.svelte 接入统一能力归一化模块。"""
    source = (root / MESSAGE_INPUT).read_text(encoding='utf-8')
    for label, old, new in (
        ('import', IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_ADDITION),
        ('map', MAP_OLD, MAP_NEW),
        ('file_upload', FILE_UPLOAD_OLD, FILE_UPLOAD_NEW),
        ('buttons', BUTTONS_OLD, BUTTONS_NEW),
    ):
        if source.count(old) != 1:
            raise ValueError(f'model_capabilities_anchor_changed:{label}')
        source = source.replace(old, new, 1)

    return {
        MESSAGE_INPUT: source,
        CAPABILITIES_MODULE: (HERE / 'model-capabilities.mjs').read_text(encoding='utf-8'),
    }


def apply(root, check=False):
    """先核整套预期前后像及路径，check 模式不写源码。"""
    root = Path(root).resolve(strict=True)
    files = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))['files']
    for name, hashes in files.items():
        target = root / name
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError('model_capabilities_source_path_invalid:' + name)
        before = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if before != hashes['before_sha256']:
            raise ValueError('model_capabilities_source_baseline_changed:' + name)
    result = prepare(root)
    if result.keys() != files.keys():
        raise ValueError('model_capabilities_source_inventory_changed')
    for name, content in result.items():
        if hashlib.sha256(content.encode()).hexdigest() != files[name]['after_sha256']:
            raise ValueError('model_capabilities_source_result_changed:' + name)
    if not check:
        for name, content in result.items():
            (root / name).write_text(content, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.root, args.check)
