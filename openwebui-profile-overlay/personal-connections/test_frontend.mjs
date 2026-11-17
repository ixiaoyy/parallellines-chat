import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import test from 'node:test';
import {
	createPersonalConnectionPrefix,
	createPersonalModelListReader,
	isPersonalConnectionNamespace,
	normalizePersonalConnectionUrl,
	personalConnectionConfig,
	personalConnectionEndpoints,
	personalConnectionModelIds,
	personalConnectionName
} from './personal-connection-ui.mjs';

const fakeCrypto = { getRandomValues: (bytes) => { bytes.fill(0xab); return bytes; } };

test('new prefixes are reserved, random-byte based and stable on edit; legacy identities stay literal', () => {
	assert.equal(createPersonalConnectionPrefix(fakeCrypto), 'pl-personal-' + 'ab'.repeat(16));
	const fresh = personalConnectionConfig({}, ' My account ', false, fakeCrypto);
	assert.equal(fresh.name, 'My account');
	assert.match(fresh.prefix_id, /^pl-personal-[a-f0-9]{32}$/);
	assert.equal(isPersonalConnectionNamespace(fresh.prefix_id), true);
	assert.equal(isPersonalConnectionNamespace('pl-personal-legacy'), false);
	assert.equal(fresh.auth_type, 'bearer');
	assert.equal(personalConnectionConfig(fresh, 'Renamed', false, fakeCrypto).prefix_id, fresh.prefix_id);
	for (const prefix_id of ['old', 'old.prefix', ' legacy prefix ']) {
		assert.equal(personalConnectionConfig({ prefix_id, enable: false }, 'Legacy', true, fakeCrypto).prefix_id, prefix_id);
	}
	assert.throws(() => personalConnectionConfig({}, '  ', false, fakeCrypto), /name_required/);
});

test('names fall back for legacy connections without exposing random internal IDs', () => {
	assert.equal(personalConnectionName({ name: ' My key ' }, 'https://api.example.com/v1'), 'My key');
	assert.equal(personalConnectionName({ prefix_id: 'old' }, 'https://api.example.com/v1'), 'old');
	assert.equal(personalConnectionName({ prefix_id: 'pl-personal-' + 'ab'.repeat(16) }, 'https://api.example.com/v1'), 'api.example.com');
	assert.equal(personalConnectionName({}, ''), '');
});

test('Base URL preview matches actual append-only routing and rejects invalid fragments', () => {
	assert.equal(normalizePersonalConnectionUrl(' https://api.example.com/custom/v1/// '), 'https://api.example.com/custom/v1');
	assert.deepEqual(personalConnectionEndpoints('https://api.example.com/v1/'), {
		models: 'https://api.example.com/v1/models', chat: 'https://api.example.com/v1/chat/completions'
	});
	assert.equal(personalConnectionEndpoints('http://localhost:8000/v1', true).models, 'http://localhost:8000/v1/models');
	assert.equal(normalizePersonalConnectionUrl('http://localhost:8000/v1?legacy=1', true), 'http://localhost:8000/v1?legacy=1');
	for (const value of ['', 'not a URL', 'file:///a', 'http://localhost:8000/v1', 'https://user:password@api.example.com/v1', 'https://api.example.com/v1?key=x', 'https://api.example.com/v1#']) {
		assert.equal(personalConnectionEndpoints(value), null);
	}
	assert.equal(personalConnectionEndpoints('https://api.example.com/chat/completions').chat,
		'https://api.example.com/chat/completions/chat/completions');
});

test('model listing is explicit, de-duplicated and cannot turn malformed responses into success', () => {
	assert.deepEqual(personalConnectionModelIds({ data: [{ id: 'one' }, { id: 'one' }, { id: 'two' }, { id: '' }, { id: null }] }), ['one', 'two']);
	assert.deepEqual(personalConnectionModelIds([]), []);
	assert.throws(() => personalConnectionModelIds({ models: [] }), /invalid_models/);
});

test('only one model list read runs at once, cancelled results are discarded', async () => {
	const reader = createPersonalModelListReader();
	let calls = 0;
	let release;
	const first = reader.read(() => { calls += 1; return new Promise((resolve) => { release = resolve; }); });
	await Promise.resolve();
	assert.deepEqual(await reader.read(() => { calls += 1; return []; }), { kind: 'busy' });
	assert.equal(calls, 1);
	reader.cancel();
	assert.equal(await first, null);
	release({ data: [{ id: 'stale' }] });
	assert.deepEqual(await reader.read(() => ({ data: [{ id: 'current' }] })), { kind: 'success', modelIds: ['current'] });
});

test('model listing has a total bound even if a provider ignores the abort signal', async () => {
	const reader = createPersonalModelListReader({ timeoutMs: 10 });
	const result = await reader.read(() => new Promise(() => {}));
	assert.equal(result.kind, 'error');
	assert.equal(result.error.name, 'TimeoutError');
	assert.deepEqual(await reader.read(() => []), { kind: 'success', modelIds: [] });
});

const feature = fileURLToPath(new URL('.', import.meta.url));
const baseline = process.env.PERSONAL_CONNECTIONS_SOURCE ?? fileURLToPath(new URL('../../.build/personal-connections-20261117/source', import.meta.url));
const generated = spawnSync('python', ['-B', '-c',
	"import importlib.util,json,pathlib,sys; p=pathlib.Path(sys.argv[1])/'frontend.py'; s=importlib.util.spec_from_file_location('personal_frontend',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(json.dumps(m.prepare(pathlib.Path(sys.argv[2]))))",
	feature, baseline], { encoding: 'utf8', maxBuffer: 2 * 1024 * 1024 });
assert.equal(generated.status, 0, generated.stderr);
const files = JSON.parse(generated.stdout);

test('the three changed native components compile with the pinned Svelte and TypeScript toolchain', async () => {
	const require = createRequire(baseline + '/package.json');
	const compiler = require('svelte/compiler');
	const ts = require('typescript');
	for (const [filename, source] of Object.entries(files).filter(([name]) => name.endsWith('.svelte'))) {
		const preprocessed = await compiler.preprocess(source, {
			script: ({ content, attributes }) => attributes.lang === 'ts' ? {
				code: ts.transpileModule(content, { compilerOptions: {
					target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext,
					verbatimModuleSyntax: true
				} }).outputText
			} : undefined
		}, { filename });
		assert.doesNotThrow(() => compiler.compile(preprocessed.code, { filename, generate: 'client' }), filename);
	}
});

function submitHarness({ save = async () => {}, existingPrefix = '', isDirect = true, isEdit = Boolean(existingPrefix),
	url = 'https://api.example.com/v1/', key = 'fixture-key', authType = 'bearer' } = {}) {
	const modal = files['src/lib/components/AddConnectionModal.svelte'];
	const start = modal.indexOf('\tconst submitHandler = async () => {');
	const end = modal.indexOf('\n\tconst init = () => {', start);
	const messages = [];
	const code = `let direct=${isDirect}, edit=${isEdit}, loading=false, personalPending=false, personalReading=false, url=${JSON.stringify(url)}, key=${JSON.stringify(key)},
		connectionName='My connection', prefixId=${JSON.stringify(existingPrefix)}, auth_type=${JSON.stringify(authType)}, ollama=false, azure=false,
		apiVersion='', modelIds=['model'], headers='', enable=true, tags=[], connectionType='external', forwardCookies=false,
		passthroughParams='', provider='', apiType='', showAdvanced=false, show=true;
		const connection = {config:{prefix_id:prefixId, auth_type}};
		const personalLegacy = direct && edit && !isPersonalConnectionNamespace(connection.config.prefix_id);
		const parsePassthroughParams = () => [];
		const $i18n = { t: value => value };
		const toast = {
			error: (value, options = {}) => messages.push({ action: 'error', value, id: options.id }),
			dismiss: id => messages.push({ action: 'dismiss', id })
		};
		${modal.slice(start, end)}
		return { submit: submitHandler, events: messages, state: () => ({ show, loading, url, key, prefixId, connectionName }) };`;
	return Function('onSubmit', 'personalConnectionConfig', 'normalizePersonalConnectionUrl', 'isPersonalConnectionNamespace', 'messages', code)(
		save, (config, name, legacy) => personalConnectionConfig(config, name, legacy, fakeCrypto), normalizePersonalConnectionUrl, isPersonalConnectionNamespace, messages);
}

test('failed save keeps the personal dialog and its entries, then reuses the identity on retry', async () => {
	let failure = true;
	const saved = [];
	const form = submitHarness({ save: async (connection) => { saved.push(connection); if (failure) throw new Error('fixture'); } });
	await form.submit();
	assert.equal(form.state().show, true);
	assert.equal(form.state().loading, false);
	assert.equal(form.state().key, 'fixture-key');
	assert.equal(form.state().connectionName, 'My connection');
	failure = false;
	await form.submit();
	assert.equal(form.state().show, false);
	assert.equal(saved[0].config.prefix_id, saved[1].config.prefix_id);
	assert.equal(saved[1].config.name, 'My connection');
});

test('editing a legacy direct connection never renames its model namespace', async () => {
	let saved;
	const form = submitHarness({ existingPrefix: 'legacy.account', save: async (connection) => { saved = connection; } });
	await form.submit();
	assert.equal(saved.config.prefix_id, 'legacy.account');
	assert.equal(form.state().show, false);
});

test('non-direct native saves preserve their unprefixed configuration contract', async () => {
	let saved;
	const form = submitHarness({ isDirect: false, save: async (connection) => { saved = connection; } });
	await form.submit();
	assert.equal(saved.config.prefix_id, '');
	assert.equal('name' in saved.config, false);
});

test('new personal connections require HTTPS without imposing the new gate on legacy prefixes', async () => {
	let calls = 0;
	const fresh = submitHarness({ url: 'http://localhost:8000/v1/', save: async () => { calls += 1; } });
	await fresh.submit();
	assert.equal(calls, 0);
	assert.equal(fresh.state().show, true);
	const old = submitHarness({ existingPrefix: 'legacy', url: 'http://localhost:8000/v1/', save: async () => { calls += 1; } });
	await old.submit();
	assert.equal(calls, 1);
	assert.equal(old.state().show, false);
});

test('failed settings writes do not append duplicates or mutate the saved connection arrays', async () => {
	const source = files['src/lib/components/chat/Settings/Connections.svelte'];
	const start = source.indexOf('\tconst cloneConfig =');
	const end = source.indexOf('\n\tonMount(async () => {', start);
	let failure = true;
	const persisted = [];
	const initial = { OPENAI_API_BASE_URLS: ['https://old.example/v1'], OPENAI_API_KEYS: ['old-fixture'], OPENAI_API_CONFIGS: { 0: { prefix_id: 'old', name: 'Old' } } };
	const form = Function('saveSettings', 'initial', `let config=structuredClone(initial);
		const toast={error:()=>{}}; const $i18n={t:value=>value};
		${source.slice(start, end)}
		return {add:addConnectionHandler, edit:editConnectionHandler, remove:deleteConnectionHandler, state:()=>config};`)(
		async (settings) => { persisted.push(settings); if (failure) throw new Error('fixture'); }, initial);
	const connection = { url: 'https://new.example/v1', key: 'new-fixture', config: { name: 'New', prefix_id: 'new' } };
	await assert.rejects(() => form.add(connection), /fixture/);
	assert.deepEqual(form.state(), initial);
	failure = false;
	await form.add(connection);
	assert.equal(form.state().OPENAI_API_BASE_URLS.length, 2);
	await form.remove(0);
	assert.deepEqual(form.state().OPENAI_API_KEYS, ['new-fixture']);
	assert.equal(form.state().OPENAI_API_CONFIGS[0].prefix_id, 'new');
});

test('legacy compatibility: editing an empty-prefix connection retains empty key, original auth and HTTP URL', async () => {
	let saved;
	const form = submitHarness({ isEdit: true, existingPrefix: '', key: '', authType: 'none',
		url: 'http://legacy.example/v1/', save: async (connection) => { saved = connection; } });
	await form.submit();
	assert.equal(saved.config.prefix_id, '');
	assert.equal(saved.config.auth_type, 'none');
	assert.equal(saved.key, '');
	assert.equal(saved.url, 'http://legacy.example/v1');
	assert.equal(form.state().show, false);
	const config = personalConnectionConfig({ prefix_id: '', auth_type: 'session' }, '', true,
		{ getRandomValues: () => { throw new Error('must_not_generate_legacy_id'); } });
	assert.equal(config.prefix_id, '');
	assert.equal(config.auth_type, 'session');
});

test('legacy compatibility: editing a reserved namespace still enforces new HTTPS and Bearer prerequisites', async () => {
	let saved;
	const prefix = 'pl-personal-' + 'ac'.repeat(16);
	const invalid = submitHarness({ isEdit: true, existingPrefix: prefix, authType: 'none',
		url: 'http://api.example.com/v1', save: async (connection) => { saved = connection; } });
	await invalid.submit();
	assert.equal(saved, undefined);
	assert.equal(invalid.state().show, true);
	const valid = submitHarness({ isEdit: true, existingPrefix: prefix, authType: 'none',
		save: async (connection) => { saved = connection; } });
	await valid.submit();
	assert.equal(saved.config.prefix_id, prefix);
	assert.equal(saved.config.auth_type, 'bearer');
});

test('legacy compatibility: the revised modal keeps optional legacy fields and compiles', async () => {
	const source = files['src/lib/components/AddConnectionModal.svelte'];
	assert.match(source, /personalLegacy = direct && edit && !isPersonalConnectionNamespace\(connection\?\.config\?\.prefix_id\)/);
	assert.match(source, /SensitiveInput id="personal-connection-key"[^\n]*required=\{!personalLegacy\}/);
	assert.ok(source.includes("? 'bearer' : (connection.config.auth_type ?? 'bearer')"));
	const require = createRequire(baseline + '/package.json');
	const compiler = require('svelte/compiler');
	const ts = require('typescript');
	const filename = 'src/lib/components/AddConnectionModal.svelte';
	const preprocessed = await compiler.preprocess(source, { script: ({ content, attributes }) => attributes.lang === 'ts'
		? { code: ts.transpileModule(content, { compilerOptions: { target: ts.ScriptTarget.ESNext,
			module: ts.ModuleKind.ESNext, verbatimModuleSyntax: true } }).outputText } : undefined }, { filename });
	assert.doesNotThrow(() => compiler.compile(preprocessed.code, { filename, generate: 'client' }));
});

test('UI acceptance: direct Advanced follows Model IDs while the admin button keeps its original position', () => {
	const source = files['src/lib/components/AddConnectionModal.svelte'];
	const toggle = 'on:click={() => (showAdvanced = !showAdvanced)}';
	const adminButton = source.indexOf(toggle);
	const directButton = source.indexOf(toggle, adminButton + toggle.length);
	const modelIds = source.indexOf("{$i18n.t('Model IDs')}");
	const tags = source.indexOf("{$i18n.t('Tags')}");
	assert.equal(source.indexOf(toggle, directButton + toggle.length), -1);
	assert.ok(adminButton < modelIds);
	assert.ok(modelIds < directButton && directButton < tags);
	assert.ok(source.slice(source.lastIndexOf('{#if', adminButton), adminButton).startsWith('{#if !direct}'));
	assert.ok(source.slice(source.lastIndexOf('{#if', directButton), directButton).startsWith('{#if direct}'));
});

test('UI acceptance: successful personal save retry dismisses the stable previous error toast', async () => {
	let failure = true;
	const form = submitHarness({ save: async () => { if (failure) throw new Error('fixture'); } });
	await form.submit();
	assert.equal(form.state().show, true);
	assert.equal(form.events.length, 1);
	assert.equal(form.events[0].action, 'error');
	assert.equal(form.events[0].id, 'personal-connection-save');
	failure = false;
	await form.submit();
	assert.equal(form.state().show, false);
	assert.deepEqual(form.events[1], { action: 'dismiss', id: form.events[0].id });
});

const twoConnections = {
	OPENAI_API_BASE_URLS: ['https://first.example/v1', 'https://second.example/v1'],
	OPENAI_API_KEYS: ['first-fixture', 'second-fixture'],
	OPENAI_API_CONFIGS: { 0: { prefix_id: 'first', enable: true }, 1: { prefix_id: 'second', enable: true } }
};

function pendingSettingsHarness(saveSettings) {
	const source = files['src/lib/components/chat/Settings/Connections.svelte'];
	const start = source.indexOf('\tconst cloneConfig =');
	const end = source.indexOf('\n\tonMount(async () => {', start);
	return Function('saveSettings', 'initial', `let config=structuredClone(initial);
		const toast={error:()=>{}}; const $i18n={t:value=>value};
		${source.slice(start, end)}
		return {add:addConnectionHandler, edit:editConnectionHandler, remove:deleteConnectionHandler,
			save:updateHandler, pending:()=>personalPending, state:()=>structuredClone(config)};`)(saveSettings, twoConnections);
}

const changedConnection = (index) => ({
	url: twoConnections.OPENAI_API_BASE_URLS[index], key: twoConnections.OPENAI_API_KEYS[index],
	config: { ...twoConnections.OPENAI_API_CONFIGS[index], enable: false }
});

test('persistence pending: delayed first write and refresh reject a second stale whole-config update', async () => {
	let releaseHttp;
	let releaseRefresh;
	const http = new Promise((resolve) => { releaseHttp = resolve; });
	const refresh = new Promise((resolve) => { releaseRefresh = resolve; });
	let calls = 0;
	const form = pendingSettingsHarness(async () => { calls += 1; await http; await refresh; });
	const first = form.edit(0, changedConnection(0));
	assert.equal(form.pending(), true);
	assert.equal(calls, 1);
	await assert.rejects(() => form.edit(1, changedConnection(1)), /personal_connection_pending/);
	assert.deepEqual(form.state(), twoConnections);
	releaseHttp();
	await Promise.resolve();
	assert.equal(form.pending(), true, 'the gate also covers post-HTTP refresh');
	await assert.rejects(() => form.add(changedConnection(1)), /personal_connection_pending/);
	await assert.rejects(() => form.remove(0), /personal_connection_pending/);
	await form.save();
	assert.equal(calls, 1);
	releaseRefresh();
	await first;
	assert.equal(form.pending(), false);
	assert.equal(form.state().OPENAI_API_CONFIGS[0].enable, false);
	assert.equal(form.state().OPENAI_API_CONFIGS[1].enable, true);
	await form.edit(1, changedConnection(1));
	assert.equal(calls, 2);
	assert.equal(form.state().OPENAI_API_CONFIGS[0].enable, false);
	assert.equal(form.state().OPENAI_API_CONFIGS[1].enable, false);
});

test('persistence pending: a failed write releases the gate without changing either saved connection', async () => {
	let fail;
	let calls = 0;
	const failure = new Promise((_, reject) => { fail = reject; });
	const form = pendingSettingsHarness(async () => { calls += 1; if (calls === 1) await failure; });
	const first = form.edit(0, changedConnection(0));
	assert.equal(form.pending(), true);
	await assert.rejects(() => form.remove(1), /personal_connection_pending/);
	const rejected = assert.rejects(first, /delayed_fixture_failure/);
	fail(new Error('delayed_fixture_failure'));
	await rejected;
	assert.equal(form.pending(), false);
	assert.deepEqual(form.state(), twoConnections);
	await form.edit(1, changedConnection(1));
	assert.equal(calls, 2);
	assert.equal(form.state().OPENAI_API_CONFIGS[0].enable, true);
	assert.equal(form.state().OPENAI_API_CONFIGS[1].enable, false);
});

test('persistence pending: every personal write control uses the gate and all changed components compile', async () => {
	const modal = files['src/lib/components/AddConnectionModal.svelte'];
	const settings = files['src/lib/components/chat/Settings/Connections.svelte'];
	const row = files['src/lib/components/chat/Settings/Connections/Connection.svelte'];
	assert.ok(modal.includes('disabled={direct && (loading || personalPending)}'));
	assert.ok(modal.includes('if (direct && (loading || personalPending)) return;'));
	assert.ok(modal.includes('if (loading || personalPending) return;'));
	assert.ok(modal.includes('if (loading || (direct && (personalPending || personalReading))) return;'));
	assert.ok(settings.includes('<AddConnectionModal direct {personalPending}'));
	assert.equal(settings.match(/disabled=\{personalPending\}/g).length, 2);
	assert.ok(settings.includes('<Connection\n\t\t\t\t\t\t\t{personalPending}'));
	assert.equal(row.match(/disabled=\{personalPending\}/g).length, 2);
	assert.ok(row.includes('\tdirect\n\t{personalPending}\n'));
	const require = createRequire(baseline + '/package.json');
	const compiler = require('svelte/compiler');
	const ts = require('typescript');
	for (const [filename, source] of Object.entries(files).filter(([name]) => name.endsWith('.svelte'))) {
		const preprocessed = await compiler.preprocess(source, { script: ({ content, attributes }) => attributes.lang === 'ts'
			? { code: ts.transpileModule(content, { compilerOptions: { target: ts.ScriptTarget.ESNext,
				module: ts.ModuleKind.ESNext, verbatimModuleSyntax: true } }).outputText } : undefined }, { filename });
		assert.doesNotThrow(() => compiler.compile(preprocessed.code, { filename, generate: 'client' }), filename);
	}
});
