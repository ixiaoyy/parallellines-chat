import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { generateKeyPair, exportJWK, createLocalJWKSet, jwtVerify } from 'jose';
import * as oidc from 'openid-client';
import { createSqliteStore } from '../sqlite-adapter.mjs';
import { createIdentityProvider, CLIENT_ID } from '../identity-provider.mjs';
import { createMainBridge } from '../main-bridge.mjs';

const secret = () => randomBytes(32).toString('base64url');
const serve = async callback => {
  const server = http.createServer((req, res) => Promise.resolve(callback(req, res)).catch(() => {
    if (!res.headersSent) res.writeHead(500); res.end();
  }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return server;
};
const close = server => { server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); };
const reply = (res, value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };

class Browser {
  cookies = new Map();
  observed = [];
  async request(url, options = {}) {
    const target = new URL(url);
    const cookie = [...this.cookies.values()].filter(item => target.pathname.startsWith(item.path)).map(item => item.value).join('; ');
    const response = await fetch(target, { ...options, redirect: 'manual', headers: { ...(options.headers || {}), ...(cookie ? { Cookie: cookie } : {}) } });
    for (const header of response.headers.getSetCookie()) {
      const name = header.slice(0, header.indexOf('='));
      const cookiePath = header.match(/;\s*path=([^;]+)/i)?.[1] || '/';
      this.observed.push({ name, path: cookiePath });
      if (/;\s*max-age=0\b/i.test(header)) this.cookies.delete(name);
      else this.cookies.set(name, { path: cookiePath, value: header.split(';')[0] });
    }
    return response;
  }
}

test('real OIDC: main approval, private claims, refresh binding, restart and fresh Main login despite OP session', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pl-provider-test-'));
  const bridgeSecret = secret();
  const clientSecret = secret();
  const approvals = new Map();
  const states = new Map();
  const bridgeCalls = [];
  let authorityAvailable = true;
  let handler;
  let store;
  const mainServer = await serve(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const input = JSON.parse(raw || '{}');
    bridgeCalls.push({ path: req.url, authCorrect: req.headers.authorization === 'Bearer ' + bridgeSecret, inputKeys: Object.keys(input).sort() });
    assert.equal(req.headers.authorization, 'Bearer ' + bridgeSecret);
    assert.equal(input.client_id, CLIENT_ID);
    if (!authorityAvailable) return reply(res, { error: 'unavailable' }, 503);
    if (req.url === '/api/v1/auth/chat-sso/exchange') {
      const value = approvals.get(input.approval_code);
      approvals.delete(input.approval_code);
      if (!value || value.interaction !== input.interaction_id) return reply(res, { error: 'denied' }, 403);
      return reply(res, { data: value.identity });
    }
    if (req.url === '/api/v1/auth/chat-sso/session-state') return reply(res, { data: states.get(input.binding_id) || { active: false } });
    return reply(res, { error: 'unknown' }, 404);
  });
  const mainOrigin = `http://127.0.0.1:${mainServer.address().port}`;
  const opServer = await serve((req, res) => handler(req, res));
  const issuer = `http://127.0.0.1:${opServer.address().port}/auth/oidc`;
  const { privateKey, publicKey } = await generateKeyPair('RS256', { extractable: true });
  const signing = { ...await exportJWK(privateKey), kid: 'synthetic-test-key', use: 'sig', alg: 'RS256' };
  const publicJwk = { ...await exportJWK(publicKey), kid: signing.kid, use: 'sig', alg: 'RS256' };
  const config = {
    issuer, mainOrigin, callbackUri: mainOrigin + '/chat/oauth/openid/callback', apiRoot: mainOrigin + '/api/v1',
    bridgeSecret, clientSecret, cookieKeys: [secret()], jwks: { keys: [signing] }, secureCookies: false,
  };
  const startProvider = () => {
    store = createSqliteStore({ directory });
    const created = createIdentityProvider({ config, store });
    handler = created.handler;
    created.provider.on('authorization.error', (_ctx, error) => console.error('synthetic authorization failure:', error.error || error.code || error.name));
    created.provider.on('server_error', (_ctx, error) => console.error('synthetic server failure:', error.error || error.code || error.name));
  };
  try {
    startProvider();
    const rp = await oidc.discovery(new URL(issuer), CLIENT_ID, { client_secret: clientSecret }, oidc.ClientSecretBasic(clientSecret), { execute: [oidc.allowInsecureRequests] });
    const browser = new Browser();
    async function authorize(sub, lifetime = 1200) {
      const verifier = oidc.randomPKCECodeVerifier(), state = oidc.randomState(), nonce = oidc.randomNonce();
      const url = oidc.buildAuthorizationUrl(rp, {
        redirect_uri: config.callbackUri, scope: 'openid profile', state, nonce,
        code_challenge: await oidc.calculatePKCECodeChallenge(verifier), code_challenge_method: 'S256',
      });
      const initial = await browser.request(url);
      assert.equal(initial.status, 303);
      const mainLogin = new URL(initial.headers.get('location'));
      assert.equal(mainLogin.origin, mainOrigin);
      assert.equal(mainLogin.pathname, '/auth/chat');
      assert.equal(mainLogin.searchParams.has('redirect'), false);
      const interaction = mainLogin.searchParams.get('interaction_id');
      const approvalCode = secret();
      const identity = { active: true, sub, binding_id: secret(), auth_expires_at: Math.floor(Date.now() / 1000) + lifetime, name: 'Synthetic ' + sub };
      states.set(identity.binding_id, identity);
      approvals.set(approvalCode, { interaction, identity });
      const wrongOrigin = await browser.request(issuer + '/interaction/finish', {
        method: 'POST', headers: { Origin: 'https://untrusted.invalid', 'Content-Type': 'application/json' },
        body: JSON.stringify({ interaction_id: interaction, approval_code: approvalCode }),
      });
      assert.equal(wrongOrigin.status, 403);
      const wrongInteraction = await browser.request(issuer + '/interaction/finish', {
        method: 'POST', headers: { Origin: mainOrigin, 'Content-Type': 'application/json' },
        body: JSON.stringify({ interaction_id: 'wrong-interaction', approval_code: approvalCode }),
      });
      assert.equal(wrongInteraction.status, 403);
      assert.ok(approvals.has(approvalCode), 'rejected browser requests must not consume Main approval');
      const finish = await browser.request(issuer + '/interaction/finish', {
        method: 'POST', headers: { Origin: mainOrigin, 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ interaction_id: interaction, approval_code: approvalCode }),
      });
      assert.equal(finish.status, 303);
      let resumed = await browser.request(finish.headers.get('location'));
      // oidc-provider's standard account-switch flow auto-POSTs its CSRF-protected
      // session confirmation form. Follow only that exact local OP endpoint.
      if (resumed.status === 200) {
        const html = await resumed.text();
        const action = html.match(/<form method="post" action="([^"]+)"/)?.[1];
        assert.equal(action, issuer + '/session/end/confirm');
        const fields = new URLSearchParams([...html.matchAll(/<input type="hidden" name="([^"]+)" value="([^"]*)"/g)].map(match => [match[1], match[2]]));
        resumed = await browser.request(action, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: fields });
        if (resumed.status === 303 && !resumed.headers.get('location').startsWith(config.callbackUri)) {
          resumed = await browser.request(resumed.headers.get('location'));
        }
      }
      assert.equal(resumed.status, 303);
      const callback = new URL(resumed.headers.get('location'));
      assert.equal(callback.origin + callback.pathname, config.callbackUri);
      const tokens = await oidc.authorizationCodeGrant(rp, callback, { pkceCodeVerifier: verifier, expectedState: state, expectedNonce: nonce });
      const verified = await jwtVerify(tokens.id_token, createLocalJWKSet({ keys: [publicJwk] }), { issuer, audience: CLIENT_ID });
      assert.equal(verified.payload.sub, sub);
      assert.equal(verified.payload.preferred_username, 'pl-' + sub);
      assert.equal(verified.payload.name, identity.name);
      assert.equal(verified.payload.email, undefined);
      assert.equal(verified.payload.role, undefined);
      assert.equal(verified.payload.pl_binding, identity.binding_id);
      assert.equal(verified.payload.pl_auth_exp, identity.auth_expires_at);
      assert.ok(verified.payload.exp - verified.payload.iat <= 300);
      assert.ok(verified.payload.exp <= identity.auth_expires_at);
      assert.equal(typeof tokens.refresh_token, 'string');
      return { tokens, identity };
    }
    const a = await authorize('synthetic-a');
    assert.ok(browser.observed.every(cookie => cookie.path === '/auth' || cookie.path.startsWith('/auth/oidc/')));
    assert.ok(!browser.observed.some(cookie => cookie.path === '/'));
    const refreshed = await oidc.refreshTokenGrant(rp, a.tokens.refresh_token);
    assert.equal(refreshed.claims().pl_binding, a.identity.binding_id);
    assert.equal(refreshed.claims().pl_auth_exp, a.identity.auth_expires_at);
    assert.notEqual(refreshed.refresh_token, a.tokens.refresh_token);
    assert.ok(bridgeCalls.some(call => call.path.endsWith('/session-state')));

    // Recreate the OP and its database connection: token binding and client trust survive.
    store.close(); startProvider();
    const afterRestart = await oidc.refreshTokenGrant(rp, refreshed.refresh_token);
    assert.equal(afterRestart.claims().sub, 'synthetic-a');
    states.set(a.identity.binding_id, { ...a.identity, auth_expires_at: a.identity.auth_expires_at + 99999 });
    const extendedAuthority = await oidc.refreshTokenGrant(rp, afterRestart.refresh_token);
    assert.equal(extendedAuthority.claims().pl_auth_exp, a.identity.auth_expires_at, 'refresh cannot extend original Main binding');

    // An existing OP session is insufficient: authorize() asserts another Main redirect.
    const b = await authorize('synthetic-b');
    authorityAvailable = false;
    await assert.rejects(oidc.refreshTokenGrant(rp, b.tokens.refresh_token));
    authorityAvailable = true;
    const bRecovered = await oidc.refreshTokenGrant(rp, b.tokens.refresh_token);
    assert.equal(bRecovered.claims().sub, 'synthetic-b');
    states.set(a.identity.binding_id, { active: false });
    await assert.rejects(oidc.refreshTokenGrant(rp, extendedAuthority.refresh_token));
    states.set(b.identity.binding_id, { active: false });
    await assert.rejects(oidc.refreshTokenGrant(rp, bRecovered.refresh_token));
    const short = await authorize('synthetic-short', 45);
    assert.ok(short.tokens.claims().exp <= short.identity.auth_expires_at, 'Main expiry below 5 minutes wins');
    states.set(short.identity.binding_id, { ...short.identity, auth_expires_at: Math.floor(Date.now() / 1000) - 1 });
    await assert.rejects(oidc.refreshTokenGrant(rp, short.tokens.refresh_token));
    assert.ok(bridgeCalls.every(call => call.authCorrect));
  } finally {
    await close(opServer); await close(mainServer); store?.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test('Main bridge does not follow redirects or accept a bare identity without {data}', async () => {
  for (const response of [new Response(null, { status: 302, headers: { Location: 'https://example.invalid' } }), new Response(JSON.stringify({ active: true, sub: 'x' }), { headers: { 'Content-Type': 'application/json' } })]) {
    const bridge = createMainBridge({ apiRoot: 'http://127.0.0.1:1/api/v1', bridgeSecret: secret(), fetchImpl: async (_url, options) => {
      assert.equal(options.redirect, 'error'); return response;
    } });
    await assert.rejects(bridge.sessionState({ sub: 'synthetic', binding_id: secret() }));
  }
});
