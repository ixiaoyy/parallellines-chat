import test from 'node:test';
import assert from 'node:assert/strict';
import {
	ERROR_TOAST_ID,
	classifyChatError,
	presentChatErrorGuidance
} from './chat-error-guidance.mjs';

test('classifies platform OAuth expiration and triggers reauthorize action', () => {
	const calls = [];
	const toastCalls = [];
	const toast = {
		error(message, options) {
			toastCalls.push({ message, options });
		}
	};

	const result = presentChatErrorGuidance({
		error: { status: 403, detail: { code: 'main_oauth_authorization_required' } },
		rawMessage: 'Authorization required',
		isPersonal: false,
		locale: 'zh-CN',
		toast,
		onReauthorize: () => calls.push('reauthorize')
	});

	assert.equal(result.kind, 'auth');
	assert.equal(result.source, 'platform');
	assert.equal(result.actionType, 'reauthorize');
	assert.equal(toastCalls.length, 1);
	assert.equal(toastCalls[0].options.id, ERROR_TOAST_ID);
	assert.equal(toastCalls[0].options.duration, Infinity);
	assert.equal(toastCalls[0].options.action.label, '重新授权');
	toastCalls[0].options.action.onClick();
	assert.deepEqual(calls, ['reauthorize']);
});

test('classifies personal API key 401 as personal connection error without OAuth redirect', () => {
	const calls = [];
	const toastCalls = [];
	const toast = {
		error(message, options) {
			toastCalls.push({ message, options });
		}
	};

	const result = presentChatErrorGuidance({
		error: { status: 401, error: { code: 'invalid_api_key' } },
		rawMessage: 'Incorrect API key provided',
		isPersonal: true,
		locale: 'zh-CN',
		toast,
		onReauthorize: () => calls.push('reauthorize'),
		onOpenPersonalConnections: () => calls.push('personal_connections')
	});

	assert.equal(result.kind, 'auth');
	assert.equal(result.source, 'personal');
	assert.equal(result.actionType, 'open_personal_connections');
	toastCalls[0].options.action.onClick();
	assert.deepEqual(calls, ['personal_connections']);
});

test('distinguishes platform quota vs personal quota insufficiency', () => {
	const platform = classifyChatError(
		{ status: 402, message: 'insufficient_user_quota' },
		{ isPersonal: false, locale: 'zh-CN' }
	);
	assert.equal(platform.kind, 'quota');
	assert.equal(platform.actionType, 'open_platform_quota');
	assert.match(platform.title, /平台额度不足/);

	const personal = classifyChatError(
		{ status: 402, message: 'insufficient_quota' },
		{ isPersonal: true, locale: 'en-US' }
	);
	assert.equal(personal.kind, 'quota');
	assert.equal(personal.actionType, 'open_personal_connections');
	assert.match(personal.title, /personal API connection/i);
});

test('classifies 429 rate limit with retry and never reauthorize', () => {
	const calls = [];
	const toastCalls = [];
	const result = presentChatErrorGuidance({
		error: { status: 429, message: 'Too Many Requests' },
		rawMessage: 'Too Many Requests',
		isPersonal: false,
		locale: 'zh-CN',
		toast: { error: (m, o) => toastCalls.push({ m, o }) },
		onReauthorize: () => calls.push('reauthorize'),
		onRetry: () => calls.push('retry')
	});

	assert.equal(result.kind, 'rate_limit');
	assert.equal(result.actionType, 'retry');
	toastCalls[0].o.action.onClick();
	assert.deepEqual(calls, ['retry']);
});

test('classifies network/5xx errors as retryable while preserving raw detail', () => {
	const result = presentChatErrorGuidance({
		error: { status: 503 },
		rawMessage: 'Server connection failed',
		isPersonal: false,
		locale: 'zh-CN',
		toast: null
	});

	assert.equal(result.kind, 'network');
	assert.equal(result.actionType, 'retry');
	assert.match(result.messageContent, /网络或模型服务暂时不可用/);
	assert.match(result.messageContent, /Server connection failed/);
});

test('does not throw when toast.error throws and preserves fallback message for unknown errors', () => {
	const result = presentChatErrorGuidance({
		error: { message: 'Unexpected provider payload' },
		rawMessage: 'Unexpected provider payload',
		locale: 'en-US',
		toast: {
			error() {
				throw new Error('toast broken');
			}
		}
	});

	assert.equal(result.kind, 'unknown');
	assert.equal(result.messageContent, 'Unexpected provider payload');
});
