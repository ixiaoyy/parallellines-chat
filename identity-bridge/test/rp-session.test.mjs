import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createRelyingPartySessions } from '../rp-session.mjs';
import { createMainBridge } from '../main-bridge.mjs';

const chat = { clientId: 'parallellines-openwebui', clientSecret: 'c'.repeat(40), callbackUri: 'https://chat.pingxingxian.space/oauth/oidc/login/callback' };
const api = { clientId: 'parallellines-newapi', clientSecret: 'n'.repeat(40), callbackUri: 'https://api.pingxingxian.space/oauth/pingxingxian' };
const identity = { sub: 'stable-main-user', binding_id: 'b'.repeat(43), auth_expires_at: Math.floor(Date.now() / 1000) + 1200 };
const basic = client => 'Basic ' + Buffer.from(client.clientId + ':' + client.clientSecret).toString('base64');

test('RP Basic endpoints require client-owned unified live binding; outage never becomes anonymous', async () => {
  let live = { active: true, ...identity, unified_browser: true };
  let available = true; let bridgeCalls = 0; let returnedTo; let ticketRace = false;
  const handler = createRelyingPartySessions({ config: { clients: [chat, api], unifiedAuthEnabled: true },
    store: { getClientBinding: (binding, client) => binding === identity.binding_id && client === chat.clientId ? identity : undefined },
    bridge: { bindingState: async () => { bridgeCalls++; if (!available) throw Error('synthetic outage'); return live; },
      logoutTicket: async (_value, returnUri) => { returnedTo = returnUri; if (ticketRace) { live = { active: false }; throw Error('synthetic concurrent logout'); } return { logout_url: 'https://pingxingxian.space/auth/unified/logout?ticket=' + 't'.repeat(43), expires_in: 60 }; } } });
  const server = http.createServer(async (req, res) => { if (!await handler(req, res, new URL(req.url, 'http://127.0.0.1'))) { res.writeHead(404); res.end(); } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const root = `http://127.0.0.1:${server.address().port}/auth/oidc/`;
  const call = async (endpoint = 'main-session-state', client = chat, headers = {}, input = { binding_id: identity.binding_id, subject: identity.sub }) => {
    const response = await fetch(root + endpoint, { method: 'POST', headers: { Authorization: basic(client), 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(input) });
    assert.equal(response.headers.get('cache-control'), 'no-store');
    return { status: response.status, value: await response.json() };
  };
  try {
    assert.deepEqual(await call(), { status: 200, value: { active: true, ...identity, unified_browser: true } });
    assert.equal((await call('unified-logout-ticket')).value.expires_in, 60);
    assert.equal(returnedTo, 'https://chat.pingxingxian.space/');
    const before = bridgeCalls;
    assert.deepEqual((await call('main-session-state', api)).value, { active: false });
    assert.equal((await call('main-session-state', chat, { Origin: 'https://chat.pingxingxian.space' })).status, 403);
    assert.equal((await call('main-session-state', { ...chat, clientSecret: api.clientSecret })).status, 401);
    assert.equal((await call('main-session-state', chat, {}, { binding_id: identity.binding_id, subject: 'different' })).value.active, false);
    assert.equal((await call('main-session-state', chat, {}, { binding_id: identity.binding_id, subject: identity.sub, return_uri: 'https://evil.invalid' })).status, 400);
    assert.equal(bridgeCalls, before);
    for (const unified_browser of [undefined, false]) {
      live = { active: true, ...identity, ...(unified_browser === undefined ? {} : { unified_browser }) };
      assert.deepEqual((await call()).value, { active: false });
      assert.deepEqual((await call('unified-logout-ticket')).value, { active: false });
    }
    live = { active: false };
    assert.deepEqual((await call()).value, { active: false });
    live = { active: true, ...identity, unified_browser: true }; ticketRace = true;
    assert.deepEqual((await call('unified-logout-ticket')).value, { active: false });
    available = false;
    assert.deepEqual(await call(), { status: 503, value: { error: 'main_authority_unavailable' } });
    assert.equal((await call('main-session-state', chat, {}, { binding_id: 'b'.repeat(3000), subject: identity.sub })).status, 400);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});

test('disabled RP route stays absent; logout bridge rejects duplicate and external tickets', async () => {
  const handler = createRelyingPartySessions({ config: { clients: [chat], unifiedAuthEnabled: false } });
  let status;
  await handler({}, { writeHead: value => { status = value; }, end() {} }, new URL('https://pingxingxian.space/auth/oidc/main-session-state'));
  assert.equal(status, 404);
  for (const logout_url of ['https://evil.invalid/auth/unified/logout?ticket=' + 't'.repeat(43),
    'https://pingxingxian.space/auth/unified/logout?ticket=' + 't'.repeat(43) + '&ticket=' + 'u'.repeat(43)]) {
    const bridge = createMainBridge({ apiRoot: 'https://pingxingxian.space/api/v1', bridgeSecret: 's'.repeat(40),
      fetchImpl: async () => new Response(JSON.stringify({ data: { logout_url, expires_in: 60 } })) });
    await assert.rejects(bridge.logoutTicket(identity, 'https://chat.pingxingxian.space/'));
  }
});
