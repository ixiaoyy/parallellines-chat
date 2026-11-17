// 本地实际构建的交互/截图验收；全部接口和供应商请求为夹具，禁止外联和真实凭据。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';

const repository = path.resolve(import.meta.dirname, '../..');
const require = createRequire(path.join(repository, '../ParallelLines/apps/web/package.json'));
const { chromium } = require('@playwright/test');
const built = path.resolve(process.argv[2]);
const evidence = path.resolve(process.argv[3]);
fs.mkdirSync(evidence, { recursive: true });
const mime = { '.html': 'text/html', '.js': 'application/javascript', '.mjs': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.wasm': 'application/wasm' };
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  let file = path.resolve(built, '.' + decodeURIComponent(url.pathname));
  if (!file.startsWith(built + path.sep) && file !== built) { res.writeHead(403).end(); return; }
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(built, 'index.html');
  res.writeHead(200, { 'Content-Type': mime[path.extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' });
  res.end(fs.readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
const token = `eyJhbGciOiJub25lIn0.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.fixture`;
const platform = { id: 'same-model', name: '平台模型', owned_by: 'openai', info: { id: 'same-model', meta: { capabilities: {} } } };
const config = { status: true, name: 'Open WebUI', version: '0.12.5', default_locale: 'zh-CN', default_models: platform.id, default_pinned_models: '',
  features: { auth: true, enable_websocket: false, enable_signup: false, enable_login_form: false, enable_direct_connections: true, enable_tool_servers: false, enable_channels: false, enable_community_sharing: false, enable_version_update_check: false }, oauth: { providers: {} }, permissions: {} };
const results = [];
const json = (route, data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });

async function fixture(width, initialSettings = { ui: { showChangelog: false }, keybindings: {} }, options = {}) {
  const context = await browser.newContext({ viewport: { width, height: width < 600 ? 844 : 1000 } });
  await context.addInitScript(({ token, accelerateBootstrap }) => {
    localStorage.setItem('token', token); localStorage.setItem('locale', 'zh-CN'); localStorage.setItem('theme', 'light'); localStorage.setItem('version', '0.12.5');
    if (accelerateBootstrap) { const nativeTimeout = window.setTimeout; window.setTimeout = (fn, delay, ...args) => nativeTimeout(fn, delay === 12000 ? 500 : delay, ...args); }
  }, { token, accelerateBootstrap: options.accelerateBootstrap });
  const page = await context.newPage(); page.setDefaultTimeout(12000);
  const state = { settings: structuredClone(initialSettings), providerMode: options.providerMode ?? 'success', failSave: false, saveCalls: [], providerCalls: [], errors: [], directoryStatus: 200, pending: [] };
  page.on('pageerror', error => state.errors.push(error.message));
  await context.route('**/*', async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.origin === 'https://provider.example.invalid') {
      state.providerCalls.push({ method: req.method(), path: url.pathname });
      if (url.pathname !== '/v1/models' || req.method() !== 'GET') throw new Error('Unexpected paid/model generation request');
      if (state.providerMode === 'hang') {
        await new Promise(resolve => state.pending.push(resolve));
        try { await route.abort(); } catch { /* 本地夹具关闭时原请求已被取消。 */ }
        return;
      }
      return json(route, state.providerMode === 'success' ? { data: [{ id: 'same-model' }, { id: 'another-model' }] } : state.providerMode === 'empty' ? { data: [] } : { error: { message: 'Fixture failure' } }, state.providerMode === 'failure' ? 401 : 200);
    }
    if (url.origin !== base) { await route.abort(); return; }
    const endpoint = url.pathname;
    if (!endpoint.startsWith('/api/') && !endpoint.startsWith('/ws/') && !endpoint.startsWith('/openai/') && !endpoint.startsWith('/ollama/')) { await route.continue(); return; }
    if (endpoint === '/api/config') return json(route, config);
    if (endpoint === '/api/v1/auths/') return json(route, { id: 'fixture-user-A', name: '本地验收账号', email: 'fixture@example.invalid', role: 'user', profile_image_url: '/static/user.png', permissions: { workspace: {}, chat: {}, features: {}, settings: { interface: true } } });
    if (endpoint === '/api/models') return json(route, state.directoryStatus === 200 ? { data: [platform] } : { detail: { code: 'main_oauth_authorization_required' } }, state.directoryStatus);
    if (endpoint === '/api/v1/users/user/settings') return json(route, state.settings);
    if (endpoint === '/api/v1/users/user/settings/update') {
      const data = req.postDataJSON(); state.saveCalls.push(data);
      if (state.failSave) { state.failSave = false; return json(route, { detail: 'Fixture save failure' }, 503); }
      state.settings = { ...state.settings, ...data, ui: { ...state.settings.ui, ...data.ui } };
      return json(route, state.settings);
    }
    if (endpoint.startsWith('/api/v1/chats/')) return json(route, []);
    if (endpoint === '/api/version') return json(route, { version: '0.12.5', deployment_id: 'fixture' });
    if (endpoint === '/api/v1/auths/admin/config') return json(route, {});
    if (endpoint === '/api/v1/models/model') return json(route, { id: platform.id, name: platform.name, params: {}, meta: platform.info.meta, access_grants: [] });
    if (endpoint === '/openai/config') return json(route, { ENABLE_OPENAI_API: true, OPENAI_API_BASE_URLS: [], OPENAI_API_KEYS: [], OPENAI_API_CONFIGS: {} });
    if (endpoint === '/ollama/config') return json(route, { ENABLE_OLLAMA_API: false, OLLAMA_API_BASE_URLS: [], OLLAMA_API_CONFIGS: {} });
    if (['/api/v1/notes/search', '/api/v1/knowledge/search', '/api/v1/knowledge/search/files'].includes(endpoint)) return json(route, { items: [], total: 0 });
    if (endpoint === '/api/v1/users/user/info') return json(route, {});
    if (endpoint.startsWith('/ws/')) return json(route, { code: 1, message: 'Fixture socket disabled' }, 400);
    return json(route, []);
  });
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.locator('#chat-input').waitFor({ state: 'visible', timeout: 30000 });
  await page.keyboard.press('Control+.');
  await page.locator('#settings-tabs-container').waitFor({ state: 'visible', timeout: 30000 });
  await page.getByRole('tab', { name: '外部连接', exact: true }).click();
  await page.locator('#tab-connections').waitFor({ state: 'visible' });
  return { page, context, state };
}

async function capture(page, name) {
  await page.screenshot({ path: path.join(evidence, name + '.png'), fullPage: true, animations: 'disabled' });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
  assert.equal(overflow, false, name + ': horizontal overflow');
}

try {
  let savedSettings;
  for (const width of [1440, 390]) {
    const { page, context, state } = await fixture(width);
    try {
      await capture(page, `connections-empty-${width}`);
      await page.getByRole('button', { name: '添加连接', exact: true }).click();
      await page.locator('#personal-connection-name').fill('我的测试服务商');
      await page.locator('#personal-connection-key').fill('fixture-personal-key-not-real');
      await page.locator('#url-input').fill('https://provider.example.invalid/v1');
      await page.getByText('https://provider.example.invalid/v1/chat/completions', { exact: true }).waitFor();
      await capture(page, `connection-form-${width}`);
      assert.ok((await page.locator('#personal-connection-key').getAttribute('class')).includes('password'), 'native Key masking enabled');
      await page.getByRole('button', { name: '获取模型列表', exact: true }).click();
      await page.getByRole('status').filter({ hasText: '已读取 2 个模型' }).waitFor();
      await capture(page, `connection-models-${width}`);
      state.providerMode = 'empty';
      await page.getByRole('button', { name: '获取模型列表', exact: true }).click();
      await page.getByRole('status').filter({ hasText: '空模型列表' }).waitFor();
      state.providerMode = 'failure';
      await page.getByRole('button', { name: '获取模型列表', exact: true }).click();
      await page.getByRole('status').filter({ hasText: '无法读取模型列表' }).waitFor();
      await capture(page, `connection-error-${width}`);
      const modelInput = page.locator('#add-model-id-input');
      const editor = page.locator('form').filter({ has: page.locator('#personal-connection-name') });
      await modelInput.fill('same-model');
      await editor.getByRole('button', { name: '添加', exact: true }).click();
      state.failSave = true;
      await editor.getByRole('button', { name: '保存', exact: true }).click();
      await page.getByText('连接保存失败，已保留填写内容，请重试。', { exact: true }).waitFor();
      assert.equal(await page.locator('#personal-connection-name').inputValue(), '我的测试服务商');
      state.providerMode = 'success';
      await editor.getByRole('button', { name: '保存', exact: true }).click();
      await page.locator('#personal-connection-name').waitFor({ state: 'hidden' });
      assert.equal(state.settings.ui.directConnections.OPENAI_API_BASE_URLS.length, 1);
      assert.deepEqual(state.settings.ui.directConnections.OPENAI_API_CONFIGS[0].model_ids, ['same-model']);
      assert.match(state.settings.ui.directConnections.OPENAI_API_CONFIGS[0].prefix_id, /^pl-personal-[a-f0-9]{32}$/);
      await capture(page, `connections-saved-${width}`);
      assert.ok(await page.evaluate(() => !Object.values(localStorage).some(value => value.includes('fixture-personal-key-not-real'))));
      savedSettings = structuredClone(state.settings);
      assert.equal(state.errors.length, 0, state.errors.join('\n'));
      results.push({ width, providerRequests: state.providerCalls.length, noGenerationRequest: true, saveFailurePreservedForm: true, savedConnections: 1, errors: state.errors });
    } catch (error) {
      fs.writeFileSync(path.join(evidence, `failure-${width}.txt`), await page.locator('body').innerText());
      await capture(page, `failure-${width}`);
      throw error;
    } finally { await context.close(); }
  }
  // 另一个浏览器上下文只从同一账号设置重新加载，验证跨设备 UI 接线而非本机存储。
  const second = await fixture(1440, savedSettings);
  try {
    await second.page.getByText('我的测试服务商', { exact: true }).waitFor();
    await capture(second.page, 'connections-second-device');
    const originalPrefix = second.state.settings.ui.directConnections.OPENAI_API_CONFIGS[0].prefix_id;
    await second.page.getByRole('button', { name: '打开外部连接配置弹窗', exact: true }).click();
    await second.page.locator('#personal-connection-name').fill('测试连接已编辑');
    const editor = second.page.locator('form').filter({ has: second.page.locator('#personal-connection-name') });
    await editor.getByRole('button', { name: '在用户界面中显示密码', exact: true }).click();
    assert.ok(!(await second.page.locator('#personal-connection-key').getAttribute('class')).includes('password'));
    await editor.getByRole('button', { name: '在用户界面中显示密码', exact: true }).click();
    await editor.getByRole('button', { name: '保存', exact: true }).click();
    await second.page.locator('#personal-connection-name').waitFor({ state: 'hidden' });
    assert.equal(second.state.settings.ui.directConnections.OPENAI_API_CONFIGS[0].prefix_id, originalPrefix);
    await second.page.getByText('测试连接已编辑', { exact: true }).waitFor();
    for (const enabled of [false, true]) {
      const saved = second.page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/users/user/settings/update' && response.status() === 200);
      await second.page.locator('#tab-connections').getByRole('switch').click(); await saved;
      assert.equal(second.state.settings.ui.directConnections.OPENAI_API_CONFIGS[0].enable, enabled);
    }
    await second.page.keyboard.press('Escape');
    await second.page.locator('#settings-tabs-container').waitFor({ state: 'hidden' });
    await second.page.locator('#model-selector-model-button').last().click();
    await second.page.getByText('测试连接已编辑 / same-model', { exact: true }).waitFor();
    await capture(second.page, 'model-sources');
    await second.page.keyboard.press('Escape');
    await second.page.keyboard.press('Control+.');
    await second.page.getByRole('tab', { name: '外部连接', exact: true }).click();
    await second.page.getByRole('button', { name: '打开外部连接配置弹窗', exact: true }).click();
    await second.page.getByRole('button', { name: '删除', exact: true }).click();
    const deleted = second.page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/users/user/settings/update' && response.status() === 200);
    await second.page.getByRole('button', { name: '删除', exact: true }).last().click(); await deleted;
    await second.page.locator('#personal-connection-name').waitFor({ state: 'hidden' });
    assert.equal(second.state.settings.ui.directConnections.OPENAI_API_KEYS.length, 0);
    assert.equal(second.state.settings.ui.directConnections.OPENAI_API_BASE_URLS.length, 0);
    assert.deepEqual(second.state.settings.ui.directConnections.OPENAI_API_CONFIGS, {});
    assert.equal(second.state.errors.length, 0);
    results.push({ case: 'second-device-settings-read-edit-toggle-delete-and-model-sources', connectionRestored: true, stableModelId: true });
  } catch (error) {
    fs.writeFileSync(path.join(evidence, 'failure-second.txt'), await second.page.locator('body').innerText());
    await capture(second.page, 'failure-second');
    throw error;
  } finally { await second.context.close(); }
  const slowSettings = { ui: { showChangelog: false, directConnections: {
    OPENAI_API_BASE_URLS: ['https://provider.example.invalid/v1'], OPENAI_API_KEYS: ['fixture-key'],
    OPENAI_API_CONFIGS: { 0: { prefix_id: 'pl-personal-' + 'a'.repeat(32), name: '慢服务商', enable: true, model_ids: [] } }
  } } };
  const slow = await fixture(390, slowSettings, { providerMode: 'hang', accelerateBootstrap: true });
  try {
    // 仅缩短原全局截止；让挂起目录跨过该时点，确认聊天和设置仍可用。
    await slow.page.waitForTimeout(700);
    assert.ok(await slow.page.locator('#tab-connections').isVisible());
    assert.equal(await slow.page.getByText('暂时无法完成初始化。', { exact: true }).count(), 0);
    assert.equal(slow.state.errors.length, 0);
    await capture(slow.page, 'startup-slow-personal-provider');
    results.push({ case: 'hanging-provider-does-not-block-bootstrap', settingsAvailable: true, acceleratedBootstrapDeadline: true });
  } finally { slow.state.pending.splice(0).forEach(resolve => resolve()); await slow.context.close(); }
  fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results));
} catch (error) {
  for (const context of browser.contexts()) for (const page of context.pages()) {
    fs.writeFileSync(path.join(evidence, 'failure.txt'), await page.locator('body').innerText().catch(() => 'No body'));
    await page.screenshot({ path: path.join(evidence, 'failure.png'), fullPage: true }).catch(() => {});
  }
  throw error;
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
