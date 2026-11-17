"""接续已核统一登录源码，保留旧 direct 和平台连接的调用合同。"""
from pathlib import Path


def replace_once(source, old, new):
    """唯一锚点拒绝重复应用或源代码漂移。"""
    if source.count(old) != 1:
        raise ValueError('personal_runtime_marker_changed:' + old[:65])
    return source.replace(old, new, 1)


def prepare(root):
    """只读准备 API/Socket 接线和运行模块，由统一应用器全部校验后写入。"""
    root = Path(root)
    files = {}
    path = 'src/lib/apis/openai/index.ts'
    source = (root / path).read_text(encoding='utf-8')
    source = replace_once(source, '\tdirect: boolean = false\n) => {', '\tdirect: boolean = false,\n\tsignal?: AbortSignal\n) => {')
    marker = "\t\tres = await fetch(`${url}/models`, {\n\t\t\tmethod: 'GET',"
    source = replace_once(source, marker, marker + '\n\t\t\t...(signal ? { signal } : {}),')
    start = source.index('export const verifyOpenAIConnection = ')
    end = source.index('export const chatCompletion = ', start)
    method = source[start:end]
    split = method.index('\t} else {')
    direct = method[:split]
    direct = replace_once(direct, "\t\t\t.catch((err) => {\n\t\t\t\terror = `OpenAI: ${err?.error?.message ?? 'Network Problem'}`;", "\t\t\t.catch((err) => {\n\t\t\t\tif (signal?.aborted) throw signal.reason;\n\t\t\t\terror = '个人连接模型列表读取失败，请检查地址、Key 和跨域设置。';")
    method = direct + method[split:]
    source = source[:start] + method + source[end:]
    source = replace_once(source, "\turl: string = `${WEBUI_BASE_URL}/api`\n): Promise<[Response | null, AbortController]> => {", "\turl: string = `${WEBUI_BASE_URL}/api`,\n\tsignal?: AbortSignal\n): Promise<[Response | null, AbortController]> => {")
    start = source.index('export const chatCompletion = ')
    end = source.index('export const generateOpenAIChatCompletion = ', start)
    method = source[start:end]
    method = replace_once(method, 'signal: controller.signal,', 'signal: signal ?? controller.signal,')
    # 个人分支有自己的脱敏错误边界；旧 Playground 调用仍保持原控制器和日志行为。
    method = replace_once(method, '\t\tconsole.error(err);', '\t\tif (!signal) console.error(err);')
    source = source[:start] + method + source[end:]
    files[path] = source

    path = 'src/lib/apis/index.ts'
    source = (root / path).read_text(encoding='utf-8')
    source = replace_once(source, "import { config } from '$lib/stores';", "import { config, user, settings } from '$lib/stores';\nimport { decoratePersonalModel, isPersonalPrefix, readPersonalModels, deferPersonalModelDiscovery } from '$lib/apis/personal-connections-runtime.mjs';")
    start = source.index('export const getModels = ')
    end = source.index('export const unloadModel = ', start)
    method = source[start:end]
    method = replace_once(method, '\tsignal?: AbortSignal\n) => {', '\tsignal?: AbortSignal,\n\tpersonalDiscovery?: (value: { models: object[]; prefixes: string[]; errors: number }) => void\n) => {')
    # 仅平台 OAuth 模型授权失败可与个人目录分离；401、取消及其他失败仍按旧边界抛出。
    method = replace_once(method, '\tif (error) {\n\t\tthrow error;\n\t}', "\tconst personalOnly = !base && error?.detail?.code === 'main_oauth_authorization_required' && connections;\n\tif (error && !personalOnly) {\n\t\tthrow error;\n\t}")
    method = replace_once(method, '\t\t\tconst requests = [];', '\t\t\tconst requests = [];\n\t\t\tconst connectionIndexes = [];')
    method = replace_once(method, '\t\t\t\t\tconst enable = apiConfig?.enable ?? true;', '\t\t\t\t\tconnectionIndexes.push(idx);\n\t\t\t\t\tconst enable = apiConfig?.enable ?? true;')
    marker = '\t\t\t\t\tif (enable) {\n'
    method = replace_once(method, marker, marker + "\t\t\t\t\t\t// 启动组不等待第三方目录；保留原生平台/身份的 12 秒截止。\n\t\t\t\t\t\tif (personalDiscovery && isPersonalPrefix(apiConfig.prefix_id) && !modelIds.length) {\n\t\t\t\t\t\t\trequests.push(Promise.resolve({ data: [], urlIdx: idx }));\n\t\t\t\t\t\t\tcontinue;\n\t\t\t\t\t\t}\n")
    method = replace_once(method, 'return await getOpenAIModelsDirect(url, OPENAI_API_KEYS[idx], signal)', 'return await (isPersonalPrefix(apiConfig.prefix_id)\n\t\t\t\t\t\t\t\t\t\t? readPersonalModels(getOpenAIModelsDirect, url, OPENAI_API_KEYS[idx], signal)\n\t\t\t\t\t\t\t\t\t\t: getOpenAIModelsDirect(url, OPENAI_API_KEYS[idx], signal))')
    method = replace_once(method, 'const apiConfig = OPENAI_API_CONFIGS[idx.toString()] ?? {};\n\n\t\t\t\tlet models', 'const connectionIndex = connectionIndexes[idx];\n\t\t\t\tconst apiConfig = OPENAI_API_CONFIGS[connectionIndex] ?? {};\n\n\t\t\t\tlet models')
    method = replace_once(method, 'models = models.map((model) => ({ ...model, openai: { id: model.id }, urlIdx: idx }));', 'models = models.map((model) => ({ ...model, openai: { id: model.id }, urlIdx: connectionIndex }));')
    method = replace_once(method, '\t\t\t\tlocalModels = localModels.concat(models);', '\t\t\t\tmodels = models.map((model) => decoratePersonalModel(model, apiConfig, OPENAI_API_BASE_URLS[connectionIndex], get(user)?.id));\n\t\t\t\tlocalModels = localModels.concat(models);')
    method = replace_once(method, '\treturn models;', "\tconst deferredPersonal = personalDiscovery && !base && connections && deferPersonalModelDiscovery({\n\t\tconnections, ownerId: get(user)?.id, signal, load: getOpenAIModelsDirect, normalizeTags, onResult: personalDiscovery,\n\t\tgetState: () => ({ connections: get(settings)?.directConnections, ownerId: get(user)?.id })\n\t});\n\tif (personalOnly) {\n\t\tif (!models.length && !deferredPersonal) throw error;\n\t\ttoast.error('平台模型需要重新授权，个人连接仍可使用。');\n\t}\n\treturn models;")
    files[path] = source[:start] + method + source[end:]

    path = 'src/routes/+layout.svelte'
    source = (root / path).read_text(encoding='utf-8')
    source = replace_once(source, '<script>', '<script>\n\timport { isPersonalModel, handlePersonalCompletion, cancelPersonalRequests, cancelPersonalChatRequests, revalidatePersonalRequests } from \'$lib/apis/personal-connections-runtime.mjs\';')
    marker = "\tconst bc = new BroadcastChannel('active-tab-channel');"
    source = replace_once(source, marker, "\t// 账号和连接变化即时撤销个人请求，不让旧 Socket 继续使用另一账号的 Key。\n\tonMount(() => {\n\t\tlet owner = $user?.id;\n\t\tconst stopUser = user.subscribe((value) => {\n\t\t\tif (owner !== value?.id) cancelPersonalRequests();\n\t\t\towner = value?.id;\n\t\t});\n\t\tconst stopSettings = settings.subscribe((value) => revalidatePersonalRequests(value?.directConnections, $user?.id));\n\t\treturn () => { stopUser(); stopSettings(); cancelPersonalRequests(); };\n\t});\n\n" + marker)
    marker = "\t\t\t} else if (type === 'request:chat:completion') {\n"
    source = replace_once(source, marker, marker + "\t\t\t\tif (isPersonalModel(data?.model?.id)) {\n\t\t\t\t\tawait handlePersonalCompletion({ data, chatId: event.chat_id, messageId: event.message_id, connections: $settings?.directConnections, ownerId: $user?.id, getOwnerId: () => $user?.id, socket: $socket, cb, chatCompletion });\n\t\t\t\t\treturn;\n\t\t\t\t}\n")
    marker = '\tconst chatEventHandler = async (event, cb) => {\n\t\tif (event.shared) return;\n'
    source = replace_once(source, marker, marker + "\t\t// 停止生成必须先于当前页/后台/临时会话过滤，按原事件精确取消上游请求。\n\t\tif (event?.data?.type === 'chat:tasks:cancel') {\n\t\t\tcancelPersonalChatRequests(event.chat_id, event.message_id);\n\t\t}\n")
    files[path] = source
    path = 'src/routes/(app)/+layout.svelte'
    source = (root / path).read_text(encoding='utf-8')
    source = replace_once(source, '<script lang="ts">', '<script lang="ts">\n\timport { mergePersonalModelDiscovery } from \'$lib/apis/personal-connections-runtime.mjs\';')
    source = replace_once(source, '\tconst setModels = async (signal?: AbortSignal) => {', '\tlet personalModelReadVersion = 0;\n\tconst setModels = async (signal?: AbortSignal) => {\n\t\tconst version = ++personalModelReadVersion;\n\t\tconst owner = $user?.id;')
    marker = '\t\t\tfalse, false, signal\n'
    source = replace_once(source, marker, "\t\t\tfalse, false, signal, (discovery) => {\n\t\t\t\tif (destroyed || signal?.aborted || owner !== $user?.id || version !== personalModelReadVersion) return;\n\t\t\t\tmodels.update((current) => mergePersonalModelDiscovery(current, discovery));\n\t\t\t\tif (discovery.errors) toast.error('部分个人模型列表读取失败，可在外部连接中重试或手动填写模型 ID。');\n\t\t\t}\n")
    files[path] = source
    path = 'src/lib/components/chat/Chat.svelte'
    source = (root / path).read_text(encoding='utf-8')
    source = replace_once(source, '<script lang="ts">', '<script lang="ts">\n\timport { isPersonalModel } from \'$lib/apis/personal-connections-runtime.mjs\';')
    marker = '\t\tconst model = atSelectedModel?.id ?? selectedModels.find((modelId) => modelId);\n'
    source = replace_once(source, marker, marker + "\t\t// 独立历史压缩接口没有浏览器会话；明确关闭，避免转用平台默认摘要模型。\n\t\tif (isPersonalModel(model)) {\n\t\t\ttoast.message('个人连接暂不支持手动压缩；自动压缩仍使用当前个人模型。');\n\t\t\treturn;\n\t\t}\n")
    files[path] = source
    path = 'src/lib/components/chat/SettingsModal.svelte'
    source = (root / path).read_text(encoding='utf-8')
    marker = '\t\tawait models.set(await getModels());\n\t};\n\n\tconst getModels = '
    source = replace_once(source, marker, "\t\t// 个人连接已成功持久化后，模型目录失败不能伪装成保存失败或诱发重复添加。\n\t\tif (Object.prototype.hasOwnProperty.call(updated, 'directConnections')) {\n\t\t\ttry { await models.set(await getModels()); }\n\t\t\tcatch { toast.error('连接已保存，但模型列表未能刷新，请检查连接后重试。'); }\n\t\t\treturn;\n\t\t}\n" + marker)
    files[path] = source
    files['src/lib/apis/personal-connections-runtime.mjs'] = (Path(__file__).parent / 'personal-connections-runtime.mjs').read_text(encoding='utf-8')
    return files
