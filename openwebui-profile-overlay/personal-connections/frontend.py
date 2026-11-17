"""准备个人连接的原生前端增量；仅返回候选文本，由统一应用器核对并写入。"""
import json
from pathlib import Path


MODULE_PATH = 'src/lib/utils/personalConnectionUi.mjs'
MODAL_PATH = 'src/lib/components/AddConnectionModal.svelte'
SETTINGS_PATH = 'src/lib/components/chat/Settings/Connections.svelte'
ROW_PATH = 'src/lib/components/chat/Settings/Connections/Connection.svelte'
TRANSLATION_PATH = 'src/lib/i18n/locales/zh-CN/translation.json'

TRANSLATIONS = {
    'Connection name': '连接名称',
    'For example: My DeepSeek account': '例如：我的 DeepSeek 账号',
    'OpenAI-compatible Chat Completions with Bearer authentication.': '支持 OpenAI 兼容的 Chat Completions 接口，使用 Bearer Key 鉴权。',
    'API keys are encrypted on the server and sync with your account across devices.': 'API Key 在服务端加密保存，随账号跨设备同步。',
    'Model list URL': '模型列表地址',
    'Chat request URL': '对话请求地址',
    'Enter an HTTPS Base URL without credentials, query parameters or a fragment.': '请填写 HTTPS Base URL，不要包含账号密码、查询参数或片段。',
    'Get model list': '获取模型列表',
    'Reading model list...': '正在读取模型列表…',
    'Read {{count}} models. Chat requests have not been tested.': '已读取 {{count}} 个模型，尚未验证对话请求。',
    'The provider returned no models. You can enter a model ID manually.': '服务商返回空模型列表，可以手动填写模型 ID。',
    'Model list request timed out after 15 seconds. You can retry or enter a model ID manually.': '读取模型列表超过 15 秒，可以重试或手动填写模型 ID。',
    'Could not read the model list. Check the URL, API key and browser cross-origin access, or enter a model ID manually.': '无法读取模型列表。请检查地址、API Key 和浏览器跨域访问配置，也可以手动填写模型 ID。',
    'Could not save the connection. Your entries have been kept; please retry.': '连接保存失败，已保留填写内容，请重试。',
    'Could not delete the connection. Please retry.': '连接删除失败，请重试。',
    'Could not save the connection settings. Please retry.': '连接设置保存失败，请重试。',
    'Enter a connection name.': '请填写连接名称。',
    'Use your own API key for personal models; platform models still use your platform account balance.': '个人模型使用自己的 API Key；平台模型仍使用平台账户余额。',
    'Model listing does not send a chat request or verify that generation is available.': '读取模型列表不会发送对话请求，也不代表模型已经可以生成回复。',
    'Direct connections rely on this browser session while requests are running.': '个人直连请求运行期间，需要保持当前浏览器会话可用。',
}


def replace_once(source, before, after):
    """只替换唯一接线点；基线漂移时终止，避免改到其他原生分支。"""
    if source.count(before) != 1:
        raise RuntimeError('personal_frontend_baseline_changed:' + before[:70])
    return source.replace(before, after, 1)


PERSONAL_SCRIPT = r'''
	let connectionName = '';
	let personalReading = false;
	let personalReadStatus: 'idle' | 'success' | 'error' = 'idle';
	let personalReadMessage = '';
	let personalReadUrl = '';
	let personalReadKey = '';
	const personalModelReader = createPersonalModelListReader();

	// 关闭弹窗或修改凭据后取消旧请求，避免旧目录和状态覆盖当前连接。
	const cancelPersonalRead = () => {
		personalModelReader.cancel();
		personalReading = false;
		personalReadStatus = 'idle';
		personalReadMessage = '';
		availableModelIds = [];
	};

	$: if (direct && (!show || url !== personalReadUrl || key !== personalReadKey)) {
		cancelPersonalRead();
		personalReadUrl = url;
		personalReadKey = key;
	}
	// 编辑旧直连时，空前缀同样属于原用法，不能自动改变历史模型 ID 或认证。
	$: personalLegacy = direct && edit && !isPersonalConnectionNamespace(connection?.config?.prefix_id);
	$: personalEndpoints = direct ? personalConnectionEndpoints(url, personalLegacy) : null;
	onDestroy(() => personalModelReader.cancel());

	// 此处仅 GET /models，不发送生成测试；空目录和错误仍允许用户手填 ID。
	const verifyPersonalHandler = async () => {
		if (personalReading || personalPending) return;
		let baseUrl;
		try {
			baseUrl = normalizePersonalConnectionUrl(url, personalLegacy);
		} catch {
			personalReadStatus = 'error';
			personalReadMessage = $i18n.t('Enter an HTTPS Base URL without credentials, query parameters or a fragment.');
			return;
		}
		personalReading = true;
		personalReadStatus = 'idle';
		personalReadMessage = '';
		availableModelIds = [];
		const result = await personalModelReader.read((signal) =>
			verifyOpenAIConnection(localStorage.token, {
				url: baseUrl, key, config: { auth_type }
			}, true, signal)
		);
		if (!result || result.kind === 'busy') return;
		personalReading = false;
		if (result.kind === 'success') {
			availableModelIds = result.modelIds;
			personalReadStatus = 'success';
			personalReadMessage = result.modelIds.length
				? $i18n.t('Read {{count}} models. Chat requests have not been tested.', { count: result.modelIds.length })
				: $i18n.t('The provider returned no models. You can enter a model ID manually.');
		} else {
			personalReadStatus = 'error';
			personalReadMessage = result.error?.name === 'TimeoutError'
				? $i18n.t('Model list request timed out after 15 seconds. You can retry or enter a model ID manually.')
				: $i18n.t('Could not read the model list. Check the URL, API key and browser cross-origin access, or enter a model ID manually.');
		}
	};
'''

PERSONAL_FIELDS = '''
						{#if direct}
							<div class="flex flex-col w-full mt-1.5">
								<label for="personal-connection-name" class="mb-0.5 text-xs text-gray-500">{$i18n.t('Connection name')}</label>
								<input id="personal-connection-name" class={`w-full text-sm ${inputClass}`} type="text"
									bind:value={connectionName} placeholder={$i18n.t('For example: My DeepSeek account')} maxlength="100" required={!personalLegacy} />
							</div>
							<div class="flex flex-col w-full mt-2">
								<label for="personal-connection-key" class="mb-0.5 text-xs text-gray-500">{$i18n.t('API Key')} · {auth_type}</label>
								<SensitiveInput id="personal-connection-key" bind:value={key} placeholder={$i18n.t('API Key')} required={!personalLegacy} />
								<p class="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">{$i18n.t('API keys are encrypted on the server and sync with your account across devices.')}</p>
							</div>
						{/if}
'''

PERSONAL_PREVIEW = '''
						{#if direct}
							<div class="mt-2 space-y-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
								<p>{$i18n.t(personalLegacy && auth_type !== 'bearer' ? 'Chat Completions' : 'OpenAI-compatible Chat Completions with Bearer authentication.')}</p>
								{#if personalEndpoints}
									<p class="break-all"><span>{$i18n.t('Model list URL')}:</span> <code>{personalEndpoints.models}</code></p>
									<p class="break-all"><span>{$i18n.t('Chat request URL')}:</span> <code>{personalEndpoints.chat}</code></p>
								{:else if url.trim()}
									<p>{$i18n.t('Enter an HTTPS Base URL without credentials, query parameters or a fragment.')}</p>
								{/if}
							</div>
							<div class="mt-3">
								<button type="button" class="flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-xs font-normal bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-60 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
									disabled={personalPending || personalReading || loading || !personalEndpoints || (!personalLegacy && !key)} on:click={verifyPersonalHandler}>
									{#if personalReading}<Spinner className="size-3" />{/if}
									{$i18n.t(personalReading ? 'Reading model list...' : 'Get model list')}
								</button>
								<p class="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">{$i18n.t('Model listing does not send a chat request or verify that generation is available.')}</p>
								{#if personalReadMessage}
									<p role="status" aria-live="polite" class="mt-1 text-xs leading-relaxed {personalReadStatus === 'error' ? 'text-red-600 dark:text-red-400' : 'text-gray-600 dark:text-gray-300'}">{personalReadMessage}</p>
								{/if}
							</div>
						{/if}
'''


def modal(source):
    """复用原生弹窗，个人分支只支持本期的 Bearer/Chat Completions。"""
    source = replace_once(source, "import { getContext, onMount } from 'svelte';", "import { getContext, onMount, onDestroy } from 'svelte';")
    source = replace_once(source, "\timport { normalizeTags } from '$lib/utils/tags';", "\timport { normalizeTags } from '$lib/utils/tags';\n\timport { createPersonalModelListReader, isPersonalConnectionNamespace, normalizePersonalConnectionUrl, personalConnectionConfig, personalConnectionEndpoints, personalConnectionName } from '$lib/utils/personalConnectionUi.mjs';")
    source = replace_once(source, '\texport let direct = false;', '\texport let direct = false;\n\texport let personalPending = false;')
    source = replace_once(source, "\tlet showAdvanced = false;", "\tlet showAdvanced = false;\n" + PERSONAL_SCRIPT)
    source = replace_once(source, "\tconst verifyHandler = () => {\n\t\tif (ollama)", "\tconst verifyHandler = () => {\n\t\tif (direct) {\n\t\t\tverifyPersonalHandler();\n\t\t\treturn;\n\t\t}\n\t\tif (ollama)")
    source = replace_once(source, "\tconst submitHandler = async () => {\n\t\tloading = true;", """\tconst submitHandler = async () => {
		if (loading || (direct && (personalPending || personalReading))) return;
		if (direct) {
			try {
				url = normalizePersonalConnectionUrl(url, personalLegacy);
				const personalConfig = personalConnectionConfig({ prefix_id: prefixId, auth_type }, connectionName, personalLegacy);
				connectionName = personalConfig.name;
				// 已有前缀保持不变；新前缀生成后重试保存也复用，避免改变历史模型身份。
				prefixId = personalConfig.prefix_id;
				auth_type = personalConfig.auth_type;
			} catch (error) {
				toast.error($i18n.t(error?.message === 'personal_connection_name_required'
					? 'Enter a connection name.' : 'Enter an HTTPS Base URL without credentials, query parameters or a fragment.'));
				return;
			}
		}
		loading = true;""")
    source = replace_once(source, "\t\t\tconfig: {\n\t\t\t\tenable: enable,", "\t\t\tconfig: {\n\t\t\t\t...(direct ? { name: connectionName.trim() } : {}),\n\t\t\t\tenable: enable,")
    source = replace_once(source, "\t\tawait onSubmit(connection);", """\t\tif (direct) {
			try {
				await onSubmit(connection);
				// 成功重试后撤掉同一保存错误，避免在已保存列表上继续显示失败。
				toast.dismiss('personal-connection-save');
			} catch {
				// 持久化失败时保留整个表单，不能关闭弹窗或伪装保存成功。
				loading = false;
				toast.error($i18n.t('Could not save the connection. Your entries have been kept; please retry.'), { id: 'personal-connection-save' });
				return;
			}
		} else {
			await onSubmit(connection);
		}""")
    source = replace_once(source, "\t\turl = '';\n\t\tkey = '';", "\t\turl = '';\n\t\tkey = '';\n\t\tconnectionName = '';")
    source = replace_once(source, "\t\t\tkey = connection.key;", "\t\t\tkey = connection.key;\n\t\t\tif (direct) connectionName = personalConnectionName(connection.config, connection.url);")
    source = replace_once(source, "\t\t\tauth_type = connection.config.auth_type ?? 'bearer';", "\t\t\tauth_type = direct && !(edit && !isPersonalConnectionNamespace(connection.config?.prefix_id))\n\t\t\t\t? 'bearer' : (connection.config.auth_type ?? 'bearer');")

    # 个人名称和 Key 排在地址之前；管理员原有输入顺序和认证选项保持不变。
    url_start = '\t\t\t\t\t\t<div class="flex gap-2 mt-1.5">'
    source = replace_once(source, url_start, PERSONAL_FIELDS + '\n' + url_start)
    auth_label = source.index('for="select-bearer-or-session"')
    auth_start = source.rfind('\t\t\t\t\t\t<div class="flex gap-2 mt-2">', 0, auth_label)
    auth_end = source.index('\n\t\t\t\t\t\t{#if !ollama && !direct}', auth_label)
    auth_block = source[auth_start:auth_end]
    source = source[:auth_start] + PERSONAL_PREVIEW + '\n\t\t\t\t\t\t{#if !direct}\n' + auth_block + '\n\t\t\t\t\t\t{/if}\n' + source[auth_end:]
    verify_start = source.index('\t\t\t\t\t\t\t<Tooltip content={$i18n.t(\'Verify Connection\')}')
    verify_end = source.index('\n\t\t\t\t\t\t\t<div class="flex flex-col shrink-0 self-end">', verify_start)
    verify_block = source[verify_start:verify_end]
    source = source[:verify_start] + '\t\t\t\t\t\t\t{#if !direct}\n' + verify_block + '\n\t\t\t\t\t\t\t{/if}\n' + source[verify_end:]
    source = replace_once(source, '\t\t\t\t\t\t\t\t\t\t\t<option value="https://api.anthropic.com/v1" />', '\t\t\t\t\t\t\t\t\t\t\t{#if !direct}<option value="https://api.anthropic.com/v1" />{/if}')

    # 个人主流程已露出模型 ID，高级入口放其后；管理员仍沿用原位置和原控件。
    advanced_click = source.index('on:click={() => (showAdvanced = !showAdvanced)}')
    advanced_start = source.rfind('\t\t\t\t\t\t<div class="flex items-center justify-between">', 0, advanced_click)
    advanced_end = source.index('\n\t\t\t\t\t\t{#if showAdvanced}', advanced_click)
    advanced_button = source[advanced_start:advanced_end]
    source = source[:advanced_start] + '\t\t\t\t\t\t{#if !direct}\n' + advanced_button + '\n\t\t\t\t\t\t{/if}\n' + source[advanced_end:]

    # 前缀只作为内部稳定身份，不让用户编辑；旧非空前缀仍由脚本原样带回。
    prefix_label = source.index('for="prefix-id-input"')
    prefix_start = source.rfind('\t\t\t\t\t\t\t<div class="flex gap-2 mt-2">', 0, prefix_label)
    prefix_end = source.index('\n\t\t\t\t\t\t\t{#if !ollama && !direct}', prefix_label)
    prefix_block = source[prefix_start:prefix_end]
    source = source[:prefix_start] + '\t\t\t\t\t\t\t{#if !direct}\n' + prefix_block + '\n\t\t\t\t\t\t\t{/if}\n' + source[prefix_end:]

    # 仅把原 Model IDs 块移出高级折叠；继续复用原生去重、补全与移除能力。
    model_label = source.index("{$i18n.t('Model IDs')}")
    model_start = source.rfind('\t\t\t\t\t\t\t<div class="flex flex-col w-full mt-2">', 0, model_label)
    source = source[:model_start] + '\t\t\t\t\t\t{/if}\n\n\t\t\t\t\t\t{#if direct || showAdvanced}\n' + source[model_start:]
    tags_label = source.index("{$i18n.t('Tags')}")
    tags_start = source.rfind('\t\t\t\t\t\t\t<div class="flex gap-2 mt-2">', 0, tags_label)
    source = source[:tags_start] + '\t\t\t\t\t\t{/if}\n\n\t\t\t\t\t\t{#if direct}\n' + advanced_button + '\n\t\t\t\t\t\t{/if}\n\n\t\t\t\t\t\t{#if showAdvanced}\n' + source[tags_start:]
    source = replace_once(source, '\t\t\t\t\t\t\tdisabled={loading}', '\t\t\t\t\t\t\tdisabled={loading || (direct && (personalPending || personalReading))}')
    # 个人整组配置提交中禁用同表单所有写控件；管理员表单保持原有行为。
    source = replace_once(source, '\t\t\t\t\t<div class="px-1">', '\t\t\t\t\t<fieldset class="min-w-0 w-full border-0 p-0" disabled={direct && (loading || personalPending)}>\n\t\t\t\t\t<div class="px-1">')
    source = replace_once(source, '\t\t\t\t</form>', '\t\t\t\t\t</fieldset>\n\t\t\t\t</form>')
    source = replace_once(source, '\t\t\t\t\t\t\t\t\tshowDeleteConfirmDialog = true;', '\t\t\t\t\t\t\t\t\tif (direct && (loading || personalPending)) return;\n\t\t\t\t\t\t\t\t\tshowDeleteConfirmDialog = true;')
    source = replace_once(source, "\ton:confirm={() => {\n\t\tonDelete();\n\t\tshow = false;\n\t}}", """\ton:confirm={async () => {
		if (direct) {
			if (loading || personalPending) return;
			try {
				await onDelete();
			} catch {
				toast.error($i18n.t('Could not delete the connection. Please retry.'));
				return;
			}
		} else onDelete();
		show = false;
	}}""")
    return source


SETTINGS_SCRIPT = '''
	// 所有编辑先保留为候选，服务端加密保存成功后才更新列表；失败不污染原设置。
	const cloneConfig = (value) => structuredClone(value ?? {
		OPENAI_API_BASE_URLS: [], OPENAI_API_KEYS: [], OPENAI_API_CONFIGS: {}
	});
	let personalPending = false;
	const persistConfig = async (candidate) => {
		// 一次只提交一份整组快照，防止两个连接从同一旧配置出发互相覆盖。
		if (personalPending) throw new Error('personal_connection_pending');
		personalPending = true;
		try {
			// saveSettings 同时等待 HTTP 和目录刷新；本地替换完成前不释放门禁。
			await saveSettings({ directConnections: candidate });
			config = candidate;
		} finally {
			personalPending = false;
		}
	};
	const addConnectionHandler = async (connection) => {
		const candidate = cloneConfig(config);
		const index = candidate.OPENAI_API_BASE_URLS.length;
		candidate.OPENAI_API_BASE_URLS.push(connection.url);
		candidate.OPENAI_API_KEYS.push(connection.key);
		candidate.OPENAI_API_CONFIGS[index] = connection.config;
		await persistConfig(candidate);
	};
	const editConnectionHandler = async (index, connection) => {
		const candidate = cloneConfig(config);
		candidate.OPENAI_API_BASE_URLS[index] = connection.url;
		candidate.OPENAI_API_KEYS[index] = connection.key;
		candidate.OPENAI_API_CONFIGS[index] = connection.config;
		await persistConfig(candidate);
	};
	const deleteConnectionHandler = async (index) => {
		const candidate = cloneConfig(config);
		candidate.OPENAI_API_BASE_URLS.splice(index, 1);
		candidate.OPENAI_API_KEYS.splice(index, 1);
		const configs = {};
		candidate.OPENAI_API_BASE_URLS.forEach((_, nextIndex) => {
			configs[nextIndex] = candidate.OPENAI_API_CONFIGS[nextIndex < index ? nextIndex : nextIndex + 1];
		});
		candidate.OPENAI_API_CONFIGS = configs;
		await persistConfig(candidate);
	};
	const updateHandler = async () => {
		try {
			await persistConfig(cloneConfig(config));
		} catch {
			toast.error($i18n.t('Could not save the connection settings. Please retry.'));
		}
	};
'''


def settings(source):
    """个人连接仍走原生用户设置 HTTP；这里不写浏览器持久存储。"""
    source = replace_once(source, "\timport { settings } from '$lib/stores';", "\timport { settings } from '$lib/stores';\n\timport { toast } from 'svelte-sonner';")
    start = source.index('\tconst addConnectionHandler =')
    end = source.index('\n\tonMount(async () => {', start)
    source = source[:start] + SETTINGS_SCRIPT + source[end:]
    start = source.index('\t\tconfig = $settings?.directConnections ?? {')
    end = source.index('\n\t});', start)
    source = source[:start] + '\t\tconfig = cloneConfig($settings?.directConnections);' + source[end:]
    source = replace_once(source, '<AddConnectionModal direct bind:show={showConnectionModal} onSubmit={addConnectionHandler} />', '<AddConnectionModal direct {personalPending} bind:show={showConnectionModal} onSubmit={addConnectionHandler} />')
    source = replace_once(source, '\t\t\t\t\t\t\ttype="button"', '\t\t\t\t\t\t\ttype="button"\n\t\t\t\t\t\t\tdisabled={personalPending}')
    source = replace_once(source, '\t\t\ttype="submit"', '\t\t\ttype="submit"\n\t\t\tdisabled={personalPending}')
    source = replace_once(source, "{$i18n.t('settings.personal.connections.addConnection.description')}", "{$i18n.t('Use your own API key for personal models; platform models still use your platform account balance.')}")
    start = source.index('\t\t\t\t\t\t<Connection\n')
    end = source.index('\n\t\t\t\t\t{/each}', start)
    source = source[:start] + '''\t\t\t\t\t\t<Connection
							{personalPending}
							{url}
							key={config.OPENAI_API_KEYS[idx]}
							config={config.OPENAI_API_CONFIGS[idx]}
							onSubmit={(connection) => editConnectionHandler(idx, connection)}
							onDelete={() => deleteConnectionHandler(idx)}
						/>''' + source[end:]
    source = replace_once(source, "'Connections managed through the Admin Panel are recommended for everyday use. Direct connections provide convenient access to your own endpoints, but rely on your browser session to keep requests running, making them better suited to testing and temporary use.'", "'Direct connections rely on this browser session while requests are running.'")
    storage = '''
					<p>{$i18n.t('API keys are encrypted on the server and sync with your account across devices.')}</p>'''
    marker = '\t\t\t\t\t<p>\n\t\t\t\t\t\t<!-- LICENSE covers this Open WebUI wordmark.'
    source = replace_once(source, marker, storage + '\n' + marker)
    return source


def row(source):
    """保留原生行、设置齿轮和开关，名称来自已保存的个人连接配置。"""
    source = replace_once(source, "\timport AddConnectionModal from '$lib/components/AddConnectionModal.svelte';", "\timport AddConnectionModal from '$lib/components/AddConnectionModal.svelte';\n\timport { toast } from 'svelte-sonner';\n\timport { personalConnectionName } from '$lib/utils/personalConnectionUi.mjs';")
    source = replace_once(source, '\texport let pipeline = false;', '\texport let pipeline = false;\n\texport let personalPending = false;')
    source = replace_once(source, '\tdirect\n\tbind:show', '\tdirect\n\t{personalPending}\n\tbind:show')
    source = replace_once(source, '\tlet showConfigModal = false;', '\tlet showConfigModal = false;\n\tlet enabled = config?.enable ?? true;\n\t$: enabled = config?.enable ?? true;')
    source = replace_once(source, '\tonDelete={() => {\n\t\tonDelete();', '\tonDelete={async () => {\n\t\tawait onDelete();')
    source = replace_once(source, '\tonSubmit={(connection) => {\n\t\turl = connection.url;', '\tonSubmit={async (connection) => {\n\t\tawait onSubmit(connection);\n\t\turl = connection.url;')
    source = replace_once(source, '\t\tconfig = connection.config;\n\t\tonSubmit(connection);', '\t\tconfig = connection.config;')
    source = replace_once(source, '<div class="group/setting-row flex w-full items-center gap-3">', '''<div class="group/setting-row flex w-full items-center gap-3">
	<div class="min-w-0 flex-1">
		<div class="mb-1 truncate text-xs font-medium text-gray-700 dark:text-gray-300">{personalConnectionName(config, url)}</div>''')
    source = replace_once(source, '\t</Tooltip>\n\n\t<div class="flex shrink-0 items-center gap-1">', '\t</Tooltip>\n\t</div>\n\n\t<div class="flex shrink-0 items-center gap-1">')
    source = replace_once(source, '\t\t\t\t\tbind:value={url}\n\t\t\t\t\tautocomplete="off"', '\t\t\t\t\tvalue={url}\n\t\t\t\t\treadonly\n\t\t\t\t\taria-label={$i18n.t(\'API Base URL\')}\n\t\t\t\t\tautocomplete="off"')
    source = replace_once(source, '\t\t\t\ttype="button"', '\t\t\t\ttype="button"\n\t\t\t\tdisabled={personalPending}')
    source = replace_once(source, '''\t\t\t\tbind:state={config.enable}
				on:change={() => {
					config.enable = config.enable ?? false;
					onSubmit({ url, key, config });
				}}''', '''\t\t\t\tdisabled={personalPending}
				bind:state={enabled}
				on:change={async () => {
					if (personalPending) {
						enabled = config?.enable ?? true;
						return;
					}
					try {
						await onSubmit({ url, key, config: { ...config, enable: enabled } });
					} catch {
						enabled = config?.enable ?? true;
						toast.error($i18n.t('Could not save the connection settings. Please retry.'));
					}
				}}''')
    return source


def translations(source):
    """只追加本功能中文文案，保留已有键值和文件排版。"""
    existing = json.loads(source)
    additions = {key: value for key, value in TRANSLATIONS.items() if key not in existing}
    if not additions:
        return source
    head = source.rstrip()[:-1].rstrip()
    entries = ',\n'.join('\t' + json.dumps(key, ensure_ascii=False) + ': ' + json.dumps(value, ensure_ascii=False) for key, value in additions.items())
    candidate = head + (',' if existing else '') + '\n' + entries + '\n}\n'
    json.loads(candidate)
    return candidate


def prepare(root):
    """读取固定候选源码，返回 native 相对路径与新文本；不写源码或凭据。"""
    root = Path(root)
    files = {
        MODAL_PATH: modal((root / MODAL_PATH).read_text(encoding='utf-8')),
        SETTINGS_PATH: settings((root / SETTINGS_PATH).read_text(encoding='utf-8')),
        ROW_PATH: row((root / ROW_PATH).read_text(encoding='utf-8')),
        TRANSLATION_PATH: translations((root / TRANSLATION_PATH).read_text(encoding='utf-8')),
        MODULE_PATH: Path(__file__).with_name('personal-connection-ui.mjs').read_text(encoding='utf-8'),
    }
    return files
