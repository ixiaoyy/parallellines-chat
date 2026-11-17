// 验收真实构建的游客中英切换/刷新与错误提示；接口均为本地夹具，禁止外联和真实生成。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { renderLoginFailure, loginFailureLanguage, loginFailureTarget } from '../../identity-bridge/error-page.mjs';

const repository = path.resolve(import.meta.dirname, '../..');
const require = createRequire(path.join(repository, '../ParallelLines/apps/web/package.json'));
const { chromium } = require('@playwright/test');
const built = path.resolve(process.argv[2]);
const evidence = path.resolve(process.argv[3]);
fs.mkdirSync(evidence, { recursive: true });
const mime = { '.html': 'text/html', '.js': 'application/javascript', '.mjs': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.wasm': 'application/wasm' };
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://fixture');
  if (url.pathname === '/auth/oidc/login-failure') {
    const selected = url.searchParams.get('lang');
    const language = loginFailureLanguage(req.headers, selected);
    const page = renderLoginFailure(loginFailureTarget(url.searchParams.get('target')), language);
    if (['zh-CN', 'en-US'].includes(selected)) res.setHeader('Set-Cookie', `pl_oidc_locale=${language}; Path=/auth/oidc; HttpOnly; SameSite=Lax`);
    res.writeHead(403, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': page.csp });
    res.end(page.html); return;
  }
  let file = path.resolve(built, '.' + decodeURIComponent(url.pathname));
  if (!file.startsWith(built + path.sep) && file !== built) { res.writeHead(403).end(); return; }
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(built, 'index.html');
  res.writeHead(200, { 'Content-Type': mime[path.extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' });
  res.end(fs.readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
const config = { status: true, name: 'Open WebUI', version: '0.12.5', default_locale: 'zh-CN', default_models: '', default_pinned_models: '', features: { auth: true, main_unified_login: true, enable_websocket: false, enable_signup: false, enable_login_form: false, enable_channels: false, enable_community_sharing: false, enable_version_update_check: false }, oauth: { providers: {} }, permissions: {} };
const results = [];
async function capture(page, name) {
  await page.screenshot({ path: path.join(evidence, name + '.png'), fullPage: true, animations: 'disabled' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false, name + ': horizontal overflow');
}
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: width < 600 ? 844 : 1000 }, locale: 'en-US' });
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    const errors = []; const requests = [];
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== base) { await route.abort(); return; }
      if (url.pathname === '/api/config') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(config) });
      if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/openai/')) {
        requests.push(url.pathname);
        return route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Fixture only"}' });
      }
      await route.continue();
    });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: 'How can I help you?' }).waitFor();
    assert.ok(await page.locator('#guest-language option').count() > 50, 'existing languages retained');
    await page.locator('#guest-message').fill('A draft that must stay unchanged 中文');
    await capture(page, `guest-en-${width}`);
    await page.locator('#guest-language').selectOption('zh-CN');
    await page.getByRole('heading', { name: '有什么可以帮忙的？' }).waitFor();
    await capture(page, `guest-zh-${width}`);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: '有什么可以帮忙的？' }).waitFor();
    assert.equal(await page.locator('#guest-message').inputValue(), 'A draft that must stay unchanged 中文');
    assert.equal(await page.evaluate(() => localStorage.getItem('locale')), 'zh-CN');
    await page.locator('#guest-language').selectOption('en-US');
    await page.goto(base + '/auth?error=' + encodeURIComponent('请先在主站验证邮箱，再重新登录。'));
    await page.getByText('Verify your email on the main site, then sign in again.', { exact: true }).waitFor();
    await capture(page, `login-error-en-${width}`);
    assert.deepEqual(requests, [], 'guest must not load private models, history or user settings');
    await page.goto(base + '/auth/oidc/login-failure?target=chat');
    await page.getByRole('heading', { name: 'Sign-in did not complete' }).waitFor();
    await capture(page, `bridge-error-en-${width}`);
    await page.getByRole('link', { name: '简体中文', exact: true }).click();
    await page.getByRole('heading', { name: '聊天登录未完成' }).waitFor();
    await capture(page, `bridge-error-zh-${width}`);
    await page.goto(base + '/auth/oidc/login-failure?target=chat');
    await page.getByRole('heading', { name: '聊天登录未完成' }).waitFor();
    assert.equal(await page.locator('a.retry').getAttribute('href'), 'https://chat.pingxingxian.space/oauth/oidc/login');
    assert.deepEqual(errors, []);
    results.push({ width, browserLanguage: 'en-US', manualChoiceAndRefresh: true, draftPreserved: true, privateRequests: 0, bridgeLanguageRemembered: true, pageErrors: errors });
    await context.close();
  }
  // 真实原生设置入口验证新增英文连接表单与失败反馈；不发送生成请求。
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: width < 600 ? 844 : 1000 }, locale: 'en-US' });
    const token = `eyJhbGciOiJub25lIn0.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.fixture`;
    await context.addInitScript(token => { localStorage.setItem('token', token); localStorage.setItem('locale', 'en-US'); localStorage.setItem('theme', 'light'); localStorage.setItem('version', '0.12.5'); }, token);
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    const errors = []; let modelReads = 0;
    page.on('pageerror', error => errors.push(error.message));
    const model = { id: 'fixture-model', name: 'Fixture model', owned_by: 'openai', info: { id: 'fixture-model', meta: { capabilities: {} } } };
    await context.route('**/*', async route => {
      const req = route.request(); const url = new URL(req.url());
      const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      if (url.origin === 'https://fixture-provider.example.invalid') {
        assert.equal(req.method(), 'GET'); assert.equal(url.pathname, '/v1/models'); modelReads++;
        return json({ error: { message: 'Fixture failure, not real' } }, 503);
      }
      if (url.origin !== base) { await route.abort(); return; }
      const endpoint = url.pathname;
      if (!endpoint.startsWith('/api/') && !endpoint.startsWith('/ws/') && !endpoint.startsWith('/openai/') && !endpoint.startsWith('/ollama/')) return route.continue();
      if (endpoint === '/api/config') return json({ ...config, features: { ...config.features, main_unified_login: false, enable_direct_connections: true, enable_tool_servers: false } });
      if (endpoint === '/api/v1/auths/') return json({ id: 'fixture-user', name: 'Fixture user', email: 'fixture@example.invalid', role: 'user', profile_image_url: '/static/user.png', permissions: { workspace: {}, chat: {}, features: {}, settings: { interface: true } } });
      if (endpoint === '/api/models') return json({ data: [model] });
      if (endpoint === '/api/v1/users/user/settings') return json({ ui: { showChangelog: false }, keybindings: {} });
      if (endpoint === '/api/version') return json({ version: '0.12.5', deployment_id: 'fixture' });
      if (endpoint === '/api/v1/auths/admin/config' || endpoint === '/api/v1/users/user/info') return json({});
      if (endpoint === '/api/v1/models/model') return json({ id: model.id, name: model.name, params: {}, meta: model.info.meta, access_grants: [] });
      if (endpoint === '/openai/config') return json({ ENABLE_OPENAI_API: true, OPENAI_API_BASE_URLS: [], OPENAI_API_KEYS: [], OPENAI_API_CONFIGS: {} });
      if (endpoint === '/ollama/config') return json({ ENABLE_OLLAMA_API: false, OLLAMA_API_BASE_URLS: [], OLLAMA_API_CONFIGS: {} });
      if (['/api/v1/notes/search', '/api/v1/knowledge/search', '/api/v1/knowledge/search/files'].includes(endpoint)) return json({ items: [], total: 0 });
      if (endpoint.startsWith('/ws/')) return json({ code: 1, message: 'Fixture socket disabled' }, 400);
      return json([]);
    });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.locator('#chat-input').waitFor({ state: 'visible', timeout: 30000 });
    await page.keyboard.press('Control+.');
    await page.locator('#settings-tabs-container').waitFor({ state: 'visible' });
    await page.getByRole('tab', { name: 'Connections', exact: true }).click();
    await page.getByRole('button', { name: 'Add Connection', exact: true }).click();
    await page.locator('#personal-connection-name').fill('A personal connection 用户自定');
    await page.locator('#personal-connection-key').fill('fixture-key-not-real');
    await page.locator('#url-input').fill('https://fixture-provider.example.invalid/v1');
    await page.getByRole('button', { name: 'Get model list', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Could not read the model list.' }).waitFor();
    await capture(page, `personal-connection-error-en-${width}`);
    assert.equal(modelReads, 1); assert.deepEqual(errors, []);
    results.push({ width, personalConnectionEnglishFormAndFailure: true, generationRequests: 0, pageErrors: errors });
    await context.close();
  }
  fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify(results, null, 2) + '\n');
  console.log(JSON.stringify(results));
} finally { await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
