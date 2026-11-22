import test from 'node:test';
import assert from 'node:assert/strict';
import {
	resolveModelCapabilities,
	buildModelCapabilitiesMap,
	filterCapableModelIds,
	allSelectedModelsSupport
} from './model-capabilities.mjs';

test('explicit info.meta.capabilities overrides modality inference and direct defaults', () => {
	const model = {
		id: 'custom-vision',
		direct: true,
		architecture: { input_modalities: ['text'], output_modalities: ['text'] },
		info: {
			meta: {
				capabilities: {
					vision: true,
					web_search: true
				}
			}
		}
	};

	const caps = resolveModelCapabilities(model);
	assert.equal(caps.vision, true);
	assert.equal(caps.web_search, true);
	assert.equal(caps.image_generation, false);
	assert.equal(caps.code_interpreter, false);
});

test('text-only model architecture disables vision and image_generation', () => {
	const model = {
		id: 'deepseek-chat',
		architecture: {
			input_modalities: ['text'],
			output_modalities: ['text']
		}
	};

	const caps = resolveModelCapabilities(model);
	assert.equal(caps.vision, false);
	assert.equal(caps.image_generation, false);
	assert.equal(caps.file_upload, undefined);
});

test('multimodal model architecture enables vision', () => {
	const model = {
		id: 'gpt-6.1-terra',
		openai: {
			architecture: {
				input_modalities: ['text', 'image']
			}
		}
	};

	const caps = resolveModelCapabilities(model);
	assert.equal(caps.vision, true);
});

test('direct personal connection defaults server-orchestrated capabilities to false', () => {
	const model = {
		id: 'personal-deepseek',
		direct: true
	};

	const caps = resolveModelCapabilities(model);
	assert.equal(caps.web_search, false);
	assert.equal(caps.image_generation, false);
	assert.equal(caps.code_interpreter, false);
	assert.equal(caps.terminal, false);
	assert.equal(caps.builtin_tools, false);
});

test('allSelectedModelsSupport returns false for empty selection and checks full match', () => {
	const map = buildModelCapabilitiesMap([
		{ id: 'm1', info: { meta: { capabilities: { web_search: true } } } },
		{ id: 'm2', info: { meta: { capabilities: { web_search: false } } } }
	]);

	assert.equal(allSelectedModelsSupport([], []), false);

	const onlyM1 = filterCapableModelIds(['m1'], 'web_search', map);
	assert.equal(allSelectedModelsSupport(['m1'], onlyM1), true);

	const both = filterCapableModelIds(['m1', 'm2'], 'web_search', map);
	assert.deepEqual(both, ['m1']);
	assert.equal(allSelectedModelsSupport(['m1', 'm2'], both), false);
});
