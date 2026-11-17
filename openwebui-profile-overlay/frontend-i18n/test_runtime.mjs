import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const source = path.resolve(process.env.CHAT_I18N_SOURCE);
const require = createRequire(path.join(source, 'package.json'));
const ts = require('typescript');
const dictionary = JSON.parse(fs.readFileSync(path.join(source, 'src/lib/i18n/locales/en-US/translation.json')));
const helper = ts.transpileModule(fs.readFileSync(path.join(source, 'src/lib/i18n/main-ui.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText.replace(/^import .*;\r?\n/gm, '');
const translationModule = `const get = () => ({ t: key => (${JSON.stringify(dictionary)})[key] ?? key }); const i18n = {};\n${helper}`;
const helperUrl = 'data:text/javascript;base64,' + Buffer.from(translationModule).toString('base64');
const runtime = fs.readFileSync(path.join(source, 'src/lib/apis/personal-connections-runtime.mjs'), 'utf8').replace("from '$lib/i18n/main-ui'", `from '${helperUrl}'`);
const feature = await import('data:text/javascript;base64,' + Buffer.from(runtime).toString('base64'));

test('actual personal runtime translates only displayed provider failures and preserves final cleanup', async () => {
  const id = 'pl-personal-' + 'a'.repeat(32);
  const ownerId = 'fixture-owner';
  const url = 'https://fixture.example.invalid/v1';
  const connections = { OPENAI_API_BASE_URLS: [url], OPENAI_API_KEYS: ['fixture-key-not-real'], OPENAI_API_CONFIGS: { 0: { prefix_id: id, name: '用户命名 Do not translate', model_ids: ['fixture-model'] } } };
  const model = feature.decoratePersonalModel({ id: id + '.fixture-model', name: 'fixture-model', openai: { id: 'fixture-model' } }, connections.OPENAI_API_CONFIGS[0], url, ownerId);
  assert.equal(model.personal_connection.name, '用户命名 Do not translate');
  for (const [status, message] of [[401, 'The personal API key is invalid or expired.'], [403, 'The personal connection has insufficient quota or permissions. Check your provider account.'], [429, 'The personal connection is rate limited. Try again later.'], [500, 'The personal connection request failed. Check the provider status.']]) {
    const callbacks = []; const emitted = [];
    await feature.handlePersonalCompletion({ data: { model, form_data: { model: model.id, stream: true }, channel: 'fixture-channel' }, chatId: 'fixture-chat', messageId: 'fixture-message', connections, ownerId, getOwnerId: () => ownerId,
      socket: { emit: (...args) => emitted.push(args) }, cb: value => callbacks.push(value), chatCompletion: async (key, body, origin) => {
        assert.equal(key, 'fixture-key-not-real'); assert.equal(body.model, 'fixture-model'); assert.equal(origin, url); return [{ ok: false, status }];
      } });
    assert.deepEqual(callbacks, [{ error: message }]);
    assert.deepEqual(emitted, [['fixture-channel', { done: true }]]);
  }
});
