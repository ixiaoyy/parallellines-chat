import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { generateKeyPair, exportJWK } from 'jose';
import { createIdentityProvider } from '../identity-provider.mjs';

test('real handler language GET stays read-only, restricted and 403; POST JSON rejection is unchanged', async () => {
  const { privateKey } = await generateKeyPair('RS256', { extractable: true });
  const key = await exportJWK(privateKey);
  const store = { Adapter: class {}, getInteractionRecoveryContext() { throw new Error('GET must not access identity state'); } };
  let calls = 0;
  const server = http.createServer((req, res) => Promise.resolve(handler(req, res)).catch(error => { res.writeHead(500).end(error.message); }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  server.unref();
  const base = `http://127.0.0.1:${server.address().port}`;
  const config = { issuer: base + '/auth/oidc', mainOrigin: 'https://pingxingxian.space', apiRoot: 'https://authority.example.invalid',
    bridgeSecret: 'fixture-bridge-secret-not-real-not-real', cookieKeys: ['fixture-cookie-key-not-real-not-real'], secureCookies: false,
    jwks: { keys: [{ ...key, kid: 'fixture', alg: 'RS256', use: 'sig' }] },
    clients: [{ clientId: 'parallellines-openwebui', clientSecret: 'fixture-client-secret-not-real', callbackUri: 'https://chat.pingxingxian.space/oauth/oidc/login/callback', refreshEnabled: true }],
  };
  const { handler } = createIdentityProvider({ config, store, fetchImpl: async () => { calls++; throw new Error('GET must not call Main'); } });
  try {
    const first = await fetch(base + '/auth/oidc/login-failure?target=chat', { headers: { 'Accept-Language': 'en-US' } });
    assert.equal(first.status, 403);
    assert.equal(first.headers.get('cache-control'), 'no-store');
    assert.match(first.headers.get('content-security-policy'), /script-src 'none'/);
    assert.match(await first.text(), /Sign-in did not complete/);
    const selected = await fetch(base + '/auth/oidc/login-failure?target=api&lang=zh-CN&approval_code=private-fixture');
    const cookie = selected.headers.get('set-cookie');
    assert.match(cookie, /^pl_oidc_locale=zh-CN; Path=\/auth\/oidc; Max-Age=31536000; HttpOnly; SameSite=Lax$/);
    const html = await selected.text();
    assert.match(html, /https:\/\/api.pingxingxian.space\/login/);
    assert.doesNotMatch(html, /private-fixture|approval_code/);
    const remembered = await fetch(base + '/auth/oidc/login-failure?target=https://evil.invalid&lang=__proto__', { headers: { Cookie: cookie.split(';')[0], 'Accept-Language': 'en-US' } });
    assert.equal(remembered.headers.get('set-cookie'), null);
    assert.match(await remembered.text(), /聊天登录未完成/);
    const rejected = await fetch(base + '/auth/oidc/interaction/finish', { method: 'POST', headers: { Origin: 'https://untrusted.invalid', 'Content-Type': 'application/json' }, body: '{}' });
    assert.equal(rejected.status, 403);
    assert.deepEqual(await rejected.json(), { error: 'origin_rejected' });
    assert.equal(calls, 0);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
