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

// Real OP, SQLite adapter and relying-party library; only the Main authority is
// synthetic. This suite does not establish real Main/browser SSO acceptance.
const secret = () => randomBytes(32).toString('base64url');
const reply = (res, value, status = 200) => {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(value));
};
const serve = async callback => {
  const server = http.createServer((req, res) => Promise.resolve(callback(req, res)).catch(() => {
    if (!res.headersSent) res.writeHead(500);
    res.end();
  }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return server;
};
const close = server => {
  server.closeAllConnections();
  return new Promise(resolve => server.close(resolve));
};

class Browser {
  cookies = new Map();
  async request(url, options = {}) {
    const target = new URL(url);
    const cookie = [...this.cookies.values()]
      .filter(item => item.hostname === target.hostname && target.pathname.startsWith(item.path))
      .map(item => item.value).join('; ');
    const response = await fetch(target, {
      ...options, redirect: 'manual',
      headers: { ...(options.headers || {}), ...(cookie ? { Cookie: cookie } : {}) },
    });
    for (const header of response.headers.getSetCookie()) {
      const name = header.slice(0, header.indexOf('='));
      const cookiePath = header.match(/;\s*path=([^;]+)/i)?.[1] || '/';
      const key = `${target.hostname}:${cookiePath}:${name}`;
      if (/;\s*max-age=0\b/i.test(header)) this.cookies.delete(key);
      else this.cookies.set(key, { hostname: target.hostname, path: cookiePath, value: header.split(';')[0] });
    }
    return response;
  }
}

test('three clients: real OIDC protocol and persisted Main binding isolation', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pl-multiclient-test-'));
  const bridgeSecret = secret();
  const approvals = new Map();
  const states = new Map();
  const authorityCalls = [];
  let handler;
  let store;
  let provider;
  let opServer;
  const mainServer = await serve(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const input = JSON.parse(raw || '{}');
    assert.equal(req.headers.authorization, `Bearer ${bridgeSecret}`);
    // Main's service identity is deliberately distinct from the external RP ID.
    assert.equal(input.client_id, CLIENT_ID);
    authorityCalls.push({ path: req.url, interaction: input.interaction_id, binding: input.binding_id });
    if (req.url === '/api/v1/auth/chat-sso/exchange') {
      const approval = approvals.get(input.approval_code);
      if (!approval || approval.interaction !== input.interaction_id) return reply(res, { error: 'denied' }, 403);
      approvals.delete(input.approval_code);
      return reply(res, { data: approval.identity });
    }
    if (req.url === '/api/v1/auth/chat-sso/session-state') {
      return reply(res, { data: states.get(input.binding_id) || { active: false } });
    }
    return reply(res, { error: 'not_found' }, 404);
  });
  try {
    const mainOrigin = `http://127.0.0.1:${mainServer.address().port}`;
    opServer = await serve((req, res) => handler(req, res));
    const issuer = `http://127.0.0.1:${opServer.address().port}/auth/oidc`;
    const clients = [
      { clientId: CLIENT_ID, clientSecret: secret(), callbackUri: `${mainOrigin}/chat/oauth/openid/callback`, refreshEnabled: true },
      { clientId: 'parallellines-openwebui', clientSecret: secret(), callbackUri: `${mainOrigin}/oauth/oidc/login/callback`, refreshEnabled: true },
      { clientId: 'parallellines-newapi', clientSecret: secret(), callbackUri: `${mainOrigin}/oauth/pingxingxian`, refreshEnabled: false },
    ];
    const { privateKey, publicKey } = await generateKeyPair('RS256', { extractable: true });
    const signing = { ...await exportJWK(privateKey), kid: 'synthetic-multiclient-key', use: 'sig', alg: 'RS256' };
    const publicKeys = createLocalJWKSet({ keys: [{ ...await exportJWK(publicKey), kid: signing.kid, use: 'sig', alg: 'RS256' }] });
    const config = {
      issuer, mainOrigin, apiRoot: `${mainOrigin}/api/v1`, clients,
      clientSecret: clients[0].clientSecret, callbackUri: clients[0].callbackUri,
      bridgeSecret, cookieKeys: [secret()], jwks: { keys: [signing] }, secureCookies: false,
    };
    function restart() {
      store?.close();
      store = createSqliteStore({ directory, clientIds: clients.map(client => client.clientId) });
      ({ provider, handler } = createIdentityProvider({ config, store }));
    }
    restart();
    const rps = new Map();
    for (const client of clients) {
      rps.set(client.clientId, await oidc.discovery(new URL(issuer), client.clientId,
        { client_secret: client.clientSecret }, oidc.ClientSecretBasic(client.clientSecret),
        { execute: [oidc.allowInsecureRequests] }));
    }
    const endpoint = rps.get(CLIENT_ID).serverMetadata().token_endpoint;
    const userinfoEndpoint = rps.get(CLIENT_ID).serverMetadata().userinfo_endpoint;
    assert.equal(userinfoEndpoint, `${issuer}/me`);

    async function begin(client, { browser = new Browser(), pkce = 'S256', redirectUri = client.callbackUri,
      scope = client.clientId === 'parallellines-openwebui' ? 'openid profile offline_access' : 'openid profile',
      prompt = client.clientId === 'parallellines-openwebui' ? 'consent' : undefined } = {}) {
      const verifier = oidc.randomPKCECodeVerifier();
      const state = oidc.randomState(), nonce = oidc.randomNonce();
      const parameters = { redirect_uri: redirectUri, scope, state, nonce, ...(prompt ? { prompt } : {}) };
      if (pkce !== 'missing') {
        parameters.code_challenge = pkce === 'plain' ? verifier : await oidc.calculatePKCECodeChallenge(verifier);
        parameters.code_challenge_method = pkce;
      }
      const response = await browser.request(oidc.buildAuthorizationUrl(rps.get(client.clientId), parameters));
      const location = response.headers.get('location');
      return { client, browser, verifier, state, nonce, response,
        interaction: location && new URL(location, issuer).searchParams.get('interaction_id') };
    }
    function assertMainRedirect(flow) {
      assert.equal(flow.response.status, 303);
      const location = new URL(flow.response.headers.get('location'));
      assert.equal(location.origin, mainOrigin);
      assert.equal(location.pathname, '/auth/chat');
      assert.ok(flow.interaction);
      assert.deepEqual([...location.searchParams.keys()], ['interaction_id']);
    }
    function approve(flow, sub) {
      const identity = { active: true, sub, binding_id: secret(), auth_expires_at: Math.floor(Date.now() / 1000) + 900, name: `Synthetic ${sub}` };
      const code = secret();
      approvals.set(code, { interaction: flow.interaction, identity });
      states.set(identity.binding_id, identity);
      return { code, identity };
    }
    function finish(flow, approvalCode, { interaction = flow.interaction, origin = mainOrigin, extra = {} } = {}) {
      return flow.browser.request(`${issuer}/interaction/finish`, {
        method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' },
        body: JSON.stringify({ interaction_id: interaction, approval_code: approvalCode, ...extra }),
      });
    }
    async function callbackAfterFinish(flow, response) {
      for (let step = 0; step < 6; step++) {
        if (response.status === 303) {
          const location = new URL(response.headers.get('location'), issuer);
          if (`${location.origin}${location.pathname}` === flow.client.callbackUri) return location;
          assert.equal(location.origin, new URL(issuer).origin);
          assert.ok(location.pathname.startsWith('/auth/oidc/'));
          response = await flow.browser.request(location);
        } else if (response.status === 200) {
          // oidc-provider's own CSRF-protected account-switch confirmation.
          const html = await response.text();
          const action = html.match(/<form method="post" action="([^"]+)"/)?.[1];
          assert.equal(action, `${issuer}/session/end/confirm`);
          const fields = new URLSearchParams([...html.matchAll(/<input type="hidden" name="([^"]+)" value="([^"]*)"/g)].map(match => [match[1], match[2]]));
          response = await flow.browser.request(action, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: fields });
        } else {
          assert.fail(`Unexpected authorization response status ${response.status}`);
        }
      }
      assert.fail('Authorization did not reach the registered callback');
    }
    async function authorization(client, sub, options) {
      const flow = await begin(client, options);
      assertMainRedirect(flow);
      const approval = approve(flow, sub);
      const response = await finish(flow, approval.code);
      assert.equal(response.status, 303);
      const callback = await callbackAfterFinish(flow, response);
      assert.equal(callback.searchParams.get('state'), flow.state);
      assert.ok(callback.searchParams.get('code'));
      return { ...flow, callback, identity: approval.identity };
    }
    const exchange = (flow, checks = {}) => oidc.authorizationCodeGrant(rps.get(flow.client.clientId), flow.callback,
      { pkceCodeVerifier: flow.verifier, expectedState: flow.state, expectedNonce: flow.nonce, ...checks });
    async function rawExchange(flow, client = flow.client, overrides = {}) {
      const parameters = { grant_type: 'authorization_code', code: flow.callback.searchParams.get('code'), redirect_uri: flow.client.callbackUri, code_verifier: flow.verifier, ...overrides };
      for (const key of Object.keys(parameters)) if (parameters[key] === undefined) delete parameters[key];
      return fetch(endpoint, {
        method: 'POST', headers: { Authorization: `Basic ${Buffer.from(`${client.clientId}:${client.clientSecret}`).toString('base64')}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams(parameters), redirect: 'error',
      });
    }
    async function assertTokenRejected(response) {
      assert.ok(response.status === 400 || response.status === 401, `Expected token rejection, got ${response.status}`);
      const value = await response.json();
      assert.equal(typeof value.error, 'string');
      assert.equal(value.access_token, undefined);
      assert.equal(value.id_token, undefined);
    }
    async function assertClaims(tokens, flow) {
      const { payload } = await jwtVerify(tokens.id_token, publicKeys, { issuer, audience: flow.client.clientId });
      assert.equal(payload.sub, flow.identity.sub);
      assert.equal(payload.pl_binding, flow.identity.binding_id);
      assert.equal(payload.name, flow.identity.name);
      assert.equal(payload.pl_auth_exp, flow.identity.auth_expires_at);
      assert.equal(payload.nonce, flow.nonce);
      assert.ok(payload.exp <= flow.identity.auth_expires_at);
      assert.equal(payload.email, undefined);
      assert.equal(payload.role, undefined);
      assert.equal(payload.roles, undefined);
      const response = await fetch(userinfoEndpoint, { headers: { Authorization: `Bearer ${tokens.access_token}` }, redirect: 'error' });
      assert.equal(response.status, 200);
      const user = await response.json();
      assert.equal(user.sub, flow.identity.sub);
      assert.equal(user.pl_binding, flow.identity.binding_id);
      assert.equal(user.iss, issuer);
      assert.equal(user.aud, flow.client.clientId);
      assert.equal(user.pl_auth_exp, flow.identity.auth_expires_at);
      assert.equal(user.role, undefined);
      return payload;
    }
    function assertAuthorizationRejected(flow) {
      assert.equal(flow.interaction, null, 'Rejected authorization must not reach Main');
      const location = flow.response.headers.get('location');
      if (location) {
        const target = new URL(location, issuer);
        assert.equal(`${target.origin}${target.pathname}`, flow.client.callbackUri);
        assert.ok(target.searchParams.has('error'));
      } else {
        assert.equal(flow.response.status, 400);
      }
    }

    await t.test('each client receives its own UserInfo audience; only legacy and consented Chat receive refresh', async () => {
      assert.deepEqual(rps.get(CLIENT_ID).serverMetadata().code_challenge_methods_supported, ['S256']);
      for (const [index, client] of clients.entries()) {
        const flow = await authorization(client, `success-user-${index}`);
        const tokens = await exchange(flow);
        await assertClaims(tokens, flow);
        assert.equal(typeof tokens.refresh_token, client.refreshEnabled ? 'string' : 'undefined');
        assert.equal(tokens.scope.split(' ').includes('offline_access'), client.clientId === 'parallellines-openwebui');
      }
    });

    await t.test('Chat requires offline consent; NewAPI cannot obtain refresh even when it requests offline consent', async () => {
      for (const [client, options] of [
        [clients[1], { scope: 'openid profile' }],
        [clients[1], { prompt: '' }],
        [clients[2], { scope: 'openid profile offline_access', prompt: 'consent' }],
      ]) {
        const flow = await authorization(client, `no-offline-${secret()}`, options);
        const tokens = await exchange(flow);
        await assertClaims(tokens, flow);
        assert.equal(tokens.refresh_token, undefined);
        assert.equal(tokens.scope.split(' ').includes('offline_access'), false);
      }
    });

    await t.test('UserInfo issuer/audience cannot be replaced by caller headers, query claims, another client or an ID token', async () => {
      for (const client of clients) {
        const flow = await authorization(client, `userinfo-${client.clientId}`);
        const tokens = await exchange(flow);
        const url = new URL(userinfoEndpoint);
        url.searchParams.set('client_id', clients[1].clientId);
        url.searchParams.set('aud', clients[1].clientId);
        url.searchParams.set('iss', 'https://untrusted.invalid');
        const response = await fetch(url, { redirect: 'error', headers: {
          Authorization: `Bearer ${tokens.access_token}`, 'X-Forwarded-Host': 'untrusted.invalid',
          'X-Client-Id': clients[1].clientId, 'X-OpenWebUI-User-Id': 'another-user',
        } });
        assert.equal(response.status, 200);
        const user = await response.json();
        assert.equal(user.iss, issuer);
        assert.equal(user.aud, client.clientId);
        assert.equal(user.sub, flow.identity.sub);
        const wrongKind = await fetch(userinfoEndpoint, { redirect: 'error', headers: { Authorization: `Bearer ${tokens.id_token}` } });
        assert.equal(wrongKind.status, 401);
        assert.equal((await wrongKind.json()).sub, undefined);
      }
    });

    await t.test('Chat refresh rotates, survives restart, retains its client and cannot extend the original Main expiry', async () => {
      const flow = await authorization(clients[1], 'chat-refresh-user');
      const tokens = await exchange(flow);
      const initialRefresh = await provider.RefreshToken.find(tokens.refresh_token);
      assert.equal(initialRefresh.clientId, clients[1].clientId);
      assert.ok(initialRefresh.exp <= flow.identity.auth_expires_at);
      for (const client of [clients[0], clients[2]]) {
        await assertTokenRejected(await fetch(endpoint, {
          method: 'POST', redirect: 'error',
          headers: { Authorization: `Basic ${Buffer.from(`${client.clientId}:${client.clientSecret}`).toString('base64')}`, 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: tokens.refresh_token }),
        }));
      }
      restart();
      states.set(flow.identity.binding_id, { ...flow.identity, auth_expires_at: flow.identity.auth_expires_at + 86400 });
      const refreshed = await oidc.refreshTokenGrant(rps.get(clients[1].clientId), tokens.refresh_token);
      await assertClaims(refreshed, flow);
      assert.notEqual(refreshed.refresh_token, tokens.refresh_token);
      assert.ok((await provider.RefreshToken.find(refreshed.refresh_token)).exp <= flow.identity.auth_expires_at);
      assert.ok((await provider.AccessToken.find(refreshed.access_token)).exp <= flow.identity.auth_expires_at);
      await assert.rejects(oidc.refreshTokenGrant(rps.get(clients[1].clientId), tokens.refresh_token));
      // A replay revokes the whole grant, including its replacement refresh token.
      await assert.rejects(oidc.refreshTokenGrant(rps.get(clients[1].clientId), refreshed.refresh_token));
    });

    await t.test('Chat refresh observes shortened Main expiry, revocation and subject/binding changes without crossing users', async () => {
      const control = await authorization(clients[1], 'chat-unaffected');
      const controlTokens = await exchange(control);
      const shortened = await authorization(clients[1], 'chat-shortened');
      const shortTokens = await exchange(shortened);
      const shorterExpiry = Math.floor(Date.now() / 1000) + 45;
      states.set(shortened.identity.binding_id, { ...shortened.identity, auth_expires_at: shorterExpiry });
      const refreshed = await oidc.refreshTokenGrant(rps.get(clients[1].clientId), shortTokens.refresh_token);
      assert.equal(refreshed.claims().pl_auth_exp, shorterExpiry);
      assert.ok(refreshed.claims().exp <= shorterExpiry);
      assert.ok((await provider.RefreshToken.find(refreshed.refresh_token)).exp <= shorterExpiry);
      assert.ok((await provider.AccessToken.find(refreshed.access_token)).exp <= shorterExpiry);
      for (const [index, mutate] of [
        () => ({ active: false }),
        identity => ({ ...identity, auth_expires_at: Math.floor(Date.now() / 1000) - 1 }),
        identity => ({ ...identity, sub: 'another-main-user' }),
        identity => ({ ...identity, binding_id: secret() }),
      ].entries()) {
        const flow = await authorization(clients[1], `chat-revoked-${index}`);
        const tokens = await exchange(flow);
        states.set(flow.identity.binding_id, mutate(flow.identity));
        await assert.rejects(oidc.refreshTokenGrant(rps.get(clients[1].clientId), tokens.refresh_token));
        const response = await fetch(userinfoEndpoint, { redirect: 'error', headers: { Authorization: `Bearer ${tokens.access_token}` } });
        assert.equal(response.status, 401);
        assert.equal((await response.json()).sub, undefined);
      }
      await assertClaims(await oidc.refreshTokenGrant(rps.get(clients[1].clientId), controlTokens.refresh_token), control);
    });

    await t.test('each client rejects absent and plain PKCE, and another client callback before Main approval', async () => {
      for (const [index, client] of clients.entries()) {
        for (const pkce of ['missing', 'plain']) assertAuthorizationRejected(await begin(client, { pkce }));
        for (const redirectUri of [clients[(index + 1) % clients.length].callbackUri, `${client.callbackUri}/`, `${client.callbackUri}?extra=1`, 'https://untrusted.invalid/callback']) {
          assertAuthorizationRejected(await begin(client, { redirectUri }));
        }
      }
    });

    await t.test('each token endpoint rejects missing/wrong verifier, another client, wrong redirect and replay', async () => {
      for (const [index, client] of clients.entries()) {
        for (const code_verifier of [undefined, secret()]) {
          const flow = await authorization(client, `pkce-user-${index}`);
          await assertTokenRejected(await rawExchange(flow, client, { code_verifier }));
        }
        const crossed = await authorization(client, `crossed-user-${index}`);
        await assertTokenRejected(await rawExchange(crossed, clients[(index + 1) % clients.length]));
        const redirect = await authorization(client, `redirect-user-${index}`);
        await assertTokenRejected(await rawExchange(redirect, client, { redirect_uri: clients[(index + 1) % clients.length].callbackUri }));
        const replay = await authorization(client, `replay-user-${index}`);
        await assertClaims(await exchange(replay), replay);
        await assertTokenRejected(await rawExchange(replay));
      }
    });

    await t.test('RP validates state and nonce instead of trusting any callback carrying a code', async () => {
      for (const client of clients) {
        const stateFlow = await authorization(client, `state-${client.clientId}`);
        await assert.rejects(exchange(stateFlow, { expectedState: oidc.randomState() }));
        const missingState = new URL(stateFlow.callback);
        missingState.searchParams.delete('state');
        await assert.rejects(exchange({ ...stateFlow, callback: missingState }));
        await assertClaims(await exchange(stateFlow), stateFlow);
        const nonceFlow = await authorization(client, `nonce-${client.clientId}`);
        await assert.rejects(exchange(nonceFlow, { expectedNonce: oidc.randomNonce() }));
      }
    });

    await t.test('parallel client interactions cannot swap approval codes, browser cookies, or supplied identities', async () => {
      const left = await begin(clients[1]);
      const right = await begin(clients[2]);
      assertMainRedirect(left); assertMainRedirect(right);
      const a = approve(left, 'parallel-user-a'), b = approve(right, 'parallel-user-b');
      const callsBefore = authorityCalls.length;
      assert.equal((await finish(right, b.code, { interaction: left.interaction })).status, 403);
      assert.equal((await finish(left, a.code, { origin: 'https://untrusted.invalid' })).status, 403);
      assert.equal((await finish(left, a.code, { extra: { client_id: clients[2].clientId, sub: b.identity.sub } })).status, 403);
      assert.equal(authorityCalls.length, callsBefore, 'Malformed browser requests must not reach Main exchange');
      assert.equal((await finish(right, a.code)).status, 403, 'Main approval is bound to its interaction');
      for (const [flow, approval] of [[left, a], [right, b]]) {
        const callback = await callbackAfterFinish(flow, await finish(flow, approval.code));
        const completed = { ...flow, callback, identity: approval.identity };
        await assertClaims(await exchange(completed), completed);
        assert.equal((await finish(flow, approval.code)).status, 403, 'Consumed interaction/approval cannot be reused');
      }
    });

    await t.test('existing OP session cannot silently select another Main user or client', async () => {
      const browser = new Browser();
      const first = await authorization(clients[0], 'browser-user-a', { browser });
      await assertClaims(await exchange(first), first);
      const second = await authorization(clients[1], 'browser-user-b', { browser });
      await assertClaims(await exchange(second), second);
      assert.notEqual(first.identity.binding_id, second.identity.binding_id);
    });

    await t.test('changed Main subject, changed binding and inactive binding fail closed for only their grant', async () => {
      const unaffected = await authorization(clients[0], 'unaffected-legacy');
      const legacyTokens = await exchange(unaffected);
      for (const [index, mutation] of [
        identity => ({ ...identity, sub: 'wrong-main-user' }),
        identity => ({ ...identity, binding_id: secret() }),
        () => ({ active: false }),
        identity => ({ ...identity, auth_expires_at: Math.floor(Date.now() / 1000) - 1 }),
      ].entries()) {
        const flow = await authorization(clients[1 + index % 2], `tampered-user-${index}`);
        const tokens = await exchange(flow);
        states.set(flow.identity.binding_id, mutation(flow.identity));
        const response = await fetch(userinfoEndpoint, { headers: { Authorization: `Bearer ${tokens.access_token}` }, redirect: 'error' });
        assert.ok(response.status === 400 || response.status === 401);
        assert.equal((await response.json()).sub, undefined);
      }
      await assertClaims(legacyTokens, unaffected);
    });

    await t.test('legacy refresh remains bound to its client and survives provider/store restart', async () => {
      const flow = await authorization(clients[0], 'legacy-restart-user');
      const tokens = await exchange(flow);
      for (const client of clients.slice(1)) {
        const response = await fetch(endpoint, {
          method: 'POST', redirect: 'error',
          headers: { Authorization: `Basic ${Buffer.from(`${client.clientId}:${client.clientSecret}`).toString('base64')}`, 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: tokens.refresh_token }),
        });
        await assertTokenRejected(response);
      }
      restart();
      const refreshed = await oidc.refreshTokenGrant(rps.get(CLIENT_ID), tokens.refresh_token);
      assert.equal(refreshed.claims().sub, flow.identity.sub);
      assert.equal(refreshed.claims().pl_binding, flow.identity.binding_id);
      assert.equal(refreshed.claims().aud, CLIENT_ID);
      assert.notEqual(refreshed.refresh_token, tokens.refresh_token);
      await assert.rejects(oidc.refreshTokenGrant(rps.get(CLIENT_ID), tokens.refresh_token));
    });
  } finally {
    if (opServer) await close(opServer);
    await close(mainServer);
    store?.close();
    const resolved = path.resolve(directory);
    assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('pl-multiclient-test-'));
    await rm(resolved, { recursive: true, force: true });
  }
});
