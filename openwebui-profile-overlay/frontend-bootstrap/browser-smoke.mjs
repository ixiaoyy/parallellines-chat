import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import zlib from 'node:zlib';
import { createRequire } from 'node:module';

const repository = path.resolve(import.meta.dirname, '../..');
const require = createRequire(process.env.PLAYWRIGHT_PACKAGE_JSON ?? path.join(repository, '../ParallelLines/apps/web/package.json'));
const { chromium } = require('@playwright/test');
const candidate = path.resolve(process.argv[2] ?? '.build/frontend-bootstrap-20261103/release/frontend-build');
const evidence = path.resolve(process.argv[3] ?? '.build/frontend-bootstrap-20261103/browser-evidence');
const baselineOnly = process.argv.includes('--baseline');
const objectOnly = process.argv.includes('--object');
const normalOnly = process.argv.includes('--normal');
const extraOnly = process.argv.includes('--extra') || objectOnly;
const publicManifest = JSON.parse(fs.readFileSync(path.join(candidate, '../frontend-build-manifest.json'), 'utf8'));
const settingsFiles = baselineOnly ? [] : Object.keys(publicManifest.files).filter(file => file.endsWith('.js') && fs.readFileSync(path.join(candidate, file), 'utf8').includes('id="settings-tabs-container"'));
if (!baselineOnly) assert.equal(settingsFiles.length, 1, 'SettingsModal must be a single identifiable lazy entry');
const settingsChunk = baselineOnly ? null : '/' + settingsFiles[0];
fs.mkdirSync(evidence, { recursive: true });
const mime = { '.html': 'text/html', '.js': 'application/javascript', '.mjs': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.wasm': 'application/wasm' };
const server = http.createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://fixture').pathname);
  let file = path.resolve(candidate, '.' + pathname);
  if (!file.startsWith(candidate + path.sep) && file !== candidate) { response.writeHead(403).end(); return; }
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(candidate, 'index.html');
  response.writeHead(200, { 'Content-Type': mime[path.extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' });
  response.end(fs.readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
const token = `eyJhbGciOiJub25lIn0.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.fixture`;
const results = [];
const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
const model = { id: 'fixture-model', name: 'Fixture model', owned_by: 'openai', info: { id: 'fixture-model', meta: { description: 'Isolated fixture', capabilities: {} } } };
const config = { status: true, name: 'Open WebUI', version: '0.12.5', default_locale: 'zh-CN', default_models: 'fixture-model', default_pinned_models: '',
  features: { auth: true, enable_websocket: false, enable_signup: false, enable_login_form: false, enable_direct_connections: false, enable_tool_servers: false, enable_channels: false, enable_community_sharing: false, enable_version_update_check: false }, oauth: { providers: {} }, permissions: {} };

async function open({ name = 'normal', role = 'user', url = '/', mobile = false, stall = null, sessionStatus = 200, scaleTimers = false, failSettings = false, modelStatus = 200, stallConfigAfter = null } = {}) {
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 } });
  await context.addInitScript(({ token, scaleTimers }) => {
    localStorage.setItem('token', token); localStorage.setItem('locale', 'zh-CN'); localStorage.setItem('theme', 'light'); localStorage.setItem('version', '0.12.5');
    if (scaleTimers) { const original = window.setTimeout; window.setTimeout = (handler, delay, ...args) => original(handler, delay === 12000 ? 500 : delay, ...args); }
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === 'parallellines.settings-retry.v1') console.log('TEST_SETTINGS_RETRY:' + value);
      return setItem.call(this, key, value);
    };
  }, { token, scaleTimers });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const state = { name, calls: [], errors: [], pending: [], stall, sessionStatus, failSettings, modelStatus, stallConfigAfter, resources: new Set(), resourceCalls: [], retryWrites: [] };
  page.on('pageerror', error => state.errors.push(error.message));
  page.on('console', message => { if (message.text().startsWith('TEST_SETTINGS_RETRY:')) state.retryWrites.push(JSON.parse(message.text().slice('TEST_SETTINGS_RETRY:'.length))); });
  page.on('response', response => { if (/\.(js|css)$/.test(new URL(response.url()).pathname)) state.resources.add(new URL(response.url()).pathname); });
  await context.route('**/*', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin !== base) { await route.abort(); return; }
    const endpoint = url.pathname;
    if (/\.(js|css)$/.test(endpoint)) state.resourceCalls.push(endpoint);
    if (state.failSettings && endpoint === settingsChunk) {
      state.failSettings = false;
      await route.fulfill({ status: 503, contentType: 'application/javascript', body: 'Unavailable fixture module' }); return;
    }
    if (!endpoint.startsWith('/api/') && !endpoint.startsWith('/ws/') && !endpoint.startsWith('/openai/') && !endpoint.startsWith('/ollama/')) { await route.continue(); return; }
    state.calls.push(endpoint);
    if (state.stall === endpoint || (endpoint === '/api/config' && state.stallConfigAfter !== null && state.calls.filter(value => value === endpoint).length > state.stallConfigAfter)) {
      await new Promise(resolve => state.pending.push(resolve));
      // 这里仅释放已取消的隔离请求，不连接真实服务。
      try { await route.abort(); } catch { /* 页面关闭或 AbortController 已取消此请求。 */ }
      return;
    }
    if (endpoint === '/api/config') return json(route, config);
    if (endpoint === '/api/v1/auths/') return json(route, state.sessionStatus === 200 ? { id: 'fixture-user', name: 'Isolated reviewer', email: 'fixture@example.invalid', role, profile_image_url: '/static/user.png', permissions: { workspace: {}, chat: {}, features: {} } } : { detail: 'Isolated session failure' }, state.sessionStatus);
    if (endpoint === '/api/models') return json(route, state.modelStatus === 200 ? { data: [model] } : { detail: { code: 'main_oauth_authorization_required', message: 'Isolated authorization required' } }, state.modelStatus);
    if (endpoint === '/api/v1/users/user/settings') return json(route, { ui: { showChangelog: false }, keybindings: {} });
    if (endpoint.startsWith('/api/v1/chats/')) return json(route, []);
    if (endpoint === '/api/version') return json(route, { version: '0.12.5', deployment_id: 'fixture' });
    if (endpoint === '/api/v1/auths/admin/config') return json(route, {});
    if (endpoint === '/api/v1/models/model') return json(route, { id: model.id, name: model.name, params: {}, meta: model.info.meta, access_grants: [] });
    if (endpoint === '/openai/config') return json(route, { ENABLE_OPENAI_API: true, OPENAI_API_BASE_URLS: [], OPENAI_API_KEYS: [], OPENAI_API_CONFIGS: {} });
    if (endpoint === '/ollama/config') return json(route, { ENABLE_OLLAMA_API: false, OLLAMA_BASE_URLS: [], OLLAMA_API_CONFIGS: {} });
    if (['/api/v1/notes/search', '/api/v1/knowledge/search', '/api/v1/knowledge/search/files'].includes(endpoint)) return json(route, { items: [], total: 0 });
    if (endpoint === '/api/v1/users/user/info') return json(route, {});
    if (endpoint.startsWith('/ws/')) return json(route, { code: 1, message: 'Isolated socket unavailable' }, 400);
    return json(route, []);
  });
  await page.goto(base + url, { waitUntil: 'domcontentloaded' });
  return { page, context, state, async close() {
    fs.writeFileSync(path.join(evidence, state.name + '-context.json'), JSON.stringify({ calls: state.calls, errors: state.errors, url: page.url(), body: await page.locator('body').innerText() }, null, 2));
    await page.screenshot({ path: path.join(evidence, state.name + '.png'), fullPage: true });
    state.pending.splice(0).forEach(resolve => resolve()); await context.close();
  } };
}

try {
  if (!extraOnly) {
  const fixture = await open();
  try {
    await fixture.page.locator('#chat-input').waitFor({ state: 'visible', timeout: 30000 });
    assert.equal(fixture.state.calls.filter(value => value === '/api/config').length, 2);
    assert.ok(fixture.state.calls.indexOf('/api/v1/users/user/settings') < fixture.state.calls.indexOf('/api/models'));
    await fixture.page.waitForTimeout(250);
    const initialResources = [...fixture.state.resources].filter(relative => fs.existsSync(path.join(candidate, relative)));
    const initial = { requests: initialResources.length, rawBytes: 0, gzipBytes: 0, files: initialResources };
    for (const relative of initialResources) { const bytes = fs.readFileSync(path.join(candidate, relative)); initial.rawBytes += bytes.length; initial.gzipBytes += zlib.gzipSync(bytes).length; }
    fs.writeFileSync(path.join(evidence, 'initial-resources.json'), JSON.stringify(initial, null, 2));
    if (settingsChunk) assert.equal(fixture.state.resources.has(settingsChunk), false);
    await fixture.page.screenshot({ path: path.join(evidence, 'normal-desktop.png'), fullPage: true });
    await fixture.page.keyboard.press('Control+.');
    await fixture.page.locator('#settings-tabs-container').waitFor({ state: 'visible', timeout: 30000 });
    await fixture.page.screenshot({ path: path.join(evidence, 'settings-desktop.png'), fullPage: true });
    assert.deepEqual(fixture.state.errors, []);
    await fixture.page.keyboard.press('Escape');
    await fixture.page.locator('#settings-tabs-container').waitFor({ state: 'hidden' });
    await fixture.page.keyboard.press('Control+.');
    await fixture.page.locator('#settings-tabs-container').waitFor({ state: 'visible' });
    if (settingsChunk) assert.equal(fixture.state.resourceCalls.filter(value => value === settingsChunk).length, 1);
    results.push({ case: 'normal-startup-and-settings', errors: fixture.state.errors, calls: fixture.state.calls });
  } finally { await fixture.close(); }
  if (!baselineOnly && !normalOnly) {
    const fixture = await open({ name: 'config-timeout', stall: '/api/config' });
    try {
      const start = Date.now();
      await fixture.page.getByText('暂时无法完成初始化。', { exact: true }).waitFor({ state: 'visible', timeout: 18000 });
      const elapsed = Date.now() - start;
      assert.ok(elapsed >= 11000 && elapsed < 18000);
      assert.equal(await fixture.page.evaluate(() => localStorage.getItem('token')), token);
      assert.equal(fixture.state.calls.filter(value => value === '/api/config').length, 1);
      await fixture.page.screenshot({ path: path.join(evidence, 'config-timeout-before-retry.png'), fullPage: true });
      fixture.state.stall = null; fixture.state.pending.splice(0).forEach(resolve => resolve());
      await fixture.page.getByRole('button', { name: '重试', exact: true }).click();
      await fixture.page.locator('#chat-input').waitFor({ state: 'visible' });
      assert.deepEqual(fixture.state.errors, []);
      results.push({ case: 'real-12-second-config-timeout-and-explicit-retry', elapsed, tokenPreserved: true });
    } finally { await fixture.close(); }
    for (const endpoint of ['/api/v1/users/user/settings', '/api/models']) {
      const fixture = await open({ name: endpoint.includes('settings') ? 'settings-timeout' : 'models-timeout', stall: endpoint, scaleTimers: true });
      try {
        await fixture.page.getByText('暂时无法完成初始化。', { exact: true }).waitFor({ state: 'visible' });
        assert.equal(await fixture.page.evaluate(() => localStorage.getItem('token')), token);
        fixture.state.stall = null; fixture.state.pending.splice(0).forEach(resolve => resolve());
        await fixture.page.getByRole('button', { name: '重试', exact: true }).click();
        await fixture.page.locator('#chat-input').waitFor({ state: 'visible' });
        assert.deepEqual(fixture.state.errors, []);
        results.push({ case: endpoint + '-timeout-and-retry', acceleratedTimers: true, tokenPreserved: true });
      } finally { await fixture.close(); }
    }
    for (const status of [503, 401]) {
      const fixture = await open({ name: 'session-' + status, sessionStatus: status });
      try {
        if (status === 503) {
          await fixture.page.getByText('暂时无法验证登录状态。', { exact: true }).waitFor({ state: 'visible' });
          assert.equal(await fixture.page.evaluate(() => localStorage.getItem('token')), token);
        } else {
          await fixture.page.waitForURL('**/auth?**');
          assert.equal(await fixture.page.evaluate(() => localStorage.getItem('token')), null);
        }
        results.push({ case: 'session-' + status, tokenPreserved: status !== 401 });
      } finally { await fixture.close(); }
    }
    const denied = await open({ name: 'model-authorization', modelStatus: 403 });
    try {
      await denied.page.locator('#chat-input').waitFor({ state: 'visible' });
      assert.equal(await denied.page.getByText('暂时无法完成初始化。', { exact: true }).count(), 0);
      assert.equal(await denied.page.evaluate(() => localStorage.getItem('token')), token);
      results.push({ case: 'model-authorization-error-is-not-a-session-logout' });
    } finally { await denied.close(); }
    for (const role of ['user', 'admin']) {
      const fixture = await open({ name: 'deep-link-' + role, role, url: '/?settings=admin:general', mobile: true });
      try {
        await fixture.page.locator('#settings-tabs-container').waitFor({ state: 'visible' });
        const controls = await fixture.page.locator('[role="tab"][aria-selected="true"]').getAttribute('aria-controls');
        assert.equal(controls, role === 'admin' ? 'tab-admin-general' : 'tab-general');
        assert.equal(new URL(fixture.page.url()).searchParams.has('settings'), false);
        assert.deepEqual(fixture.state.errors, []);
        results.push({ case: 'mobile-deep-link-' + role, controls, queryConsumed: true });
      } finally { await fixture.close(); }
    }
    const failed = await open({ name: 'settings-module-recovery', failSettings: true, url: '/?settings=general', mobile: true });
    try {
      await failed.page.getByText('暂时无法加载设置。', { exact: true }).waitFor({ state: 'visible' });
      await failed.page.screenshot({ path: path.join(evidence, 'settings-module-error-mobile.png'), fullPage: true });
      const requests = failed.state.resourceCalls.filter(value => value === settingsChunk).length;
      await failed.page.getByRole('button', { name: '重试', exact: true }).click();
      await failed.page.locator('#settings-tabs-container').waitFor({ state: 'visible' });
      assert.equal(failed.state.resourceCalls.filter(value => value === settingsChunk).length, requests + 1);
      assert.equal(await failed.page.evaluate(() => sessionStorage.getItem('parallellines.settings-retry.v1')), null);
      assert.equal(await failed.page.locator('[role="tab"][aria-selected="true"]').getAttribute('aria-controls'), 'tab-general');
      assert.deepEqual(failed.state.errors, []);
      results.push({ case: 'real-module-network-failure-explicit-reload-recovery', sameRequestRestored: true, recordConsumed: true });
    } finally { await failed.close(); }
  }
  }
  if (extraOnly) {
    if (!objectOnly) {
    const refreshed = await open({ name: 'authenticated-config-timeout', stallConfigAfter: 1, scaleTimers: true });
    try {
      await refreshed.page.getByText('暂时无法完成初始化。', { exact: true }).waitFor({ state: 'visible' });
      assert.equal(refreshed.state.calls.filter(value => value === '/api/config').length, 2);
      assert.equal(refreshed.state.calls.includes('/api/v1/users/user/settings'), false);
      assert.equal(await refreshed.page.evaluate(() => localStorage.getItem('token')), token);
      refreshed.state.stallConfigAfter = null; refreshed.state.pending.splice(0).forEach(resolve => resolve());
      await refreshed.page.getByRole('button', { name: '重试', exact: true }).click();
      await refreshed.page.locator('#chat-input').waitFor({ state: 'visible' });
      assert.deepEqual(refreshed.state.errors, []);
      results.push({ case: 'authenticated-second-config-timeout-and-retry', acceleratedTimers: true, oldConfigNotRendered: true, tokenPreserved: true });
    } finally { await refreshed.close(); }
    }
    const object = await open({ name: 'object-request-recovery', role: 'admin', failSettings: true });
    try {
      await object.page.locator('#chat-input').waitFor({ state: 'visible' });
      await object.page.locator('#model-selector-model-button').click();
      await object.page.getByRole('button', { name: '更多选项', exact: true }).first().hover();
      await object.page.getByRole('button', { name: '更多选项', exact: true }).first().click();
      await object.page.getByText('编辑', { exact: true }).click();
      await object.page.getByText('暂时无法加载设置。', { exact: true }).waitFor({ state: 'visible' });
      await object.page.getByRole('button', { name: '重试', exact: true }).click();
      await object.page.locator('#settings-tabs-container').waitFor({ state: 'visible' });
      assert.equal(await object.page.locator('[role="tab"][aria-selected="true"]').getAttribute('aria-controls'), 'tab-admin-models');
      assert.deepEqual(object.state.retryWrites.map(value => value.show), [{ tab: 'admin:models', state: { id: model.id } }]);
      assert.equal(await object.page.evaluate(() => sessionStorage.getItem('parallellines.settings-retry.v1')), null);
      assert.deepEqual(object.state.errors, []);
      results.push({ case: 'native-model-edit-object-request-restored-after-real-module-failure', preservedModelId: model.id, oneTimeRecordConsumed: true });
    } finally { await object.close(); }
  }
} catch (error) {
  console.error(error);
  const pages = browser.contexts().flatMap(context => context.pages());
  for (const [index, page] of pages.entries()) { await page.screenshot({ path: path.join(evidence, `failure-${index}.png`), fullPage: true }).catch(() => {}); console.error((await page.locator('body').innerText()).slice(0, 3000)); }
  process.exitCode = 1;
} finally {
  fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify(results, null, 2));
  await browser.close(); await new Promise(resolve => server.close(resolve));
}
