// 统一归一当前选中模型的输入区能力（联网搜索、视觉/图片、附件上传、内置工具等），
// 优先尊重管理端显式配置，其次读取上游/个人连接返回的真实能力元数据，不改后端或计费契约。

const KNOWN_CAPABILITIES = [
	'vision',
	'file_upload',
	'web_search',
	'image_generation',
	'code_interpreter',
	'terminal',
	'builtin_tools'
];

function asBoolean(value) {
	return typeof value === 'boolean' ? value : undefined;
}

function normalizeStringArray(value) {
	if (!Array.isArray(value)) return null;
	return value
		.filter((item) => typeof item === 'string' && item.trim())
		.map((item) => item.trim().toLowerCase());
}

/**
 * 归一化单个模型的能力映射。
 * 优先级：
 * 1. Open WebUI 模型记录显式配置 `model.info.meta.capabilities`
 * 2. 模型或上游对象上的 `capabilities`（`model.capabilities` / `model.openai.capabilities`）
 * 3. `architecture.input_modalities` / `output_modalities`（如 OpenRouter / OpenAI 兼容接口）
 * 4. 个人直连（`direct: true`）默认关闭未显式声明的服务端专属能力（`web_search`、`image_generation`、`code_interpreter`、`terminal`、`builtin_tools`）
 */
export function resolveModelCapabilities(model) {
	if (!model || typeof model !== 'object') return {};

	const resolved = {};
	const explicit = model.info?.meta?.capabilities;
	const topLevel = model.capabilities;
	const openaiCaps = model.openai?.capabilities;

	// 1. 先从模态信息推断基础能力（最低优先级，可被显式布尔值覆盖）
	const inputModalities =
		normalizeStringArray(model.architecture?.input_modalities) ??
		normalizeStringArray(model.openai?.architecture?.input_modalities) ??
		normalizeStringArray(model.input_modalities);
	if (inputModalities && inputModalities.length > 0) {
		resolved.vision = inputModalities.includes('image');
		// 若仅声明音频/embedding 等非文本非文件模态，则关闭文件上传
		if (
			!inputModalities.includes('file') &&
			!inputModalities.includes('image') &&
			!inputModalities.includes('text')
		) {
			resolved.file_upload = false;
		}
	}

	const outputModalities =
		normalizeStringArray(model.architecture?.output_modalities) ??
		normalizeStringArray(model.openai?.architecture?.output_modalities) ??
		normalizeStringArray(model.output_modalities);
	if (outputModalities && outputModalities.length > 0) {
		if (!outputModalities.includes('image')) {
			resolved.image_generation = false;
		}
	}

	// 2. 个人直连模型若未显式声明，保守关闭依赖服务端编排的能力，避免前端显示不可用按钮
	if (model.direct === true) {
		for (const serverCap of [
			'web_search',
			'image_generation',
			'code_interpreter',
			'terminal',
			'builtin_tools'
		]) {
			if (resolved[serverCap] === undefined) {
				resolved[serverCap] = false;
			}
		}
	}

	// 3. 依次合并上游能力与 Open WebUI 显式能力（后者优先级最高）
	for (const source of [openaiCaps, topLevel, explicit]) {
		if (!source || typeof source !== 'object') continue;
		for (const cap of KNOWN_CAPABILITIES) {
			const boolVal = asBoolean(source[cap]);
			if (boolVal !== undefined) {
				resolved[cap] = boolVal;
			}
		}
	}

	return resolved;
}

export function buildModelCapabilitiesMap(models) {
	if (!Array.isArray(models)) return {};
	const map = {};
	for (const model of models) {
		if (model && typeof model.id === 'string' && model.id) {
			map[model.id] = resolveModelCapabilities(model);
		}
	}
	return map;
}

export function filterCapableModelIds(modelIds, capability, capabilitiesById) {
	if (!Array.isArray(modelIds) || modelIds.length === 0) return [];
	return modelIds.filter((id) => {
		if (!id) return false;
		const value = capabilitiesById?.[id]?.[capability];
		return value ?? true;
	});
}

export function allSelectedModelsSupport(selectedModelIds, capableModelIds) {
	if (!Array.isArray(selectedModelIds) || selectedModelIds.length === 0) return false;
	if (!Array.isArray(capableModelIds)) return false;
	return capableModelIds.length === selectedModelIds.length;
}
