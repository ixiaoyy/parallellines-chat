import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { loadConfiguration } from '../config.mjs';

test('production config fixes issuer/callback and requires private file rather than secret JSON environment', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'pl-oidc-config-'));
  const signingPath = path.join(directory, 'signing.json');
  // Configuration validation only; the protocol test generates a real RSA private key.
  await writeFile(signingPath, JSON.stringify({ keys: [{ kty: 'RSA', alg: 'RS256', kid: 'synthetic', d: 'synthetic-config-only' }] }), { mode: 0o600 });
  const env = {
    CHAT_OIDC_CLIENT_SECRET: 'synthetic-client-secret-'.repeat(3),
    CHAT_SSO_BRIDGE_SECRET: 'synthetic-bridge-secret-'.repeat(3),
    CHAT_OIDC_COOKIE_KEYS: JSON.stringify(['synthetic-cookie-secret-'.repeat(3)]),
    CHAT_OIDC_SIGNING_JWKS_FILE: signingPath,
    OIDC_DATABASE_PATH: path.join(directory, 'data', 'oidc.sqlite'),
  };
  try {
    const config = loadConfiguration(env);
    assert.equal(config.issuer, 'https://pingxingxian.space/auth/oidc');
    assert.equal(config.callbackUri, 'https://pingxingxian.space/chat/oauth/openid/callback');
    assert.equal(config.apiRoot, 'https://pingxingxian.space/api/v1');
    assert.equal(config.secureCookies, true);
    const expanded = loadConfiguration({ ...env,
      OPENWEBUI_OIDC_CLIENT_SECRET: 'synthetic-openwebui-'.repeat(3),
      NEWAPI_OAUTH_CLIENT_SECRET: 'synthetic-newapi-'.repeat(3),
    });
    assert.deepEqual(expanded.clients.map(({ clientId, callbackUri, refreshEnabled }) => ({ clientId, callbackUri, refreshEnabled })), [
      { clientId: 'parallellines-chat', callbackUri: config.callbackUri, refreshEnabled: true },
      { clientId: 'parallellines-openwebui', callbackUri: 'https://chat.pingxingxian.space/oauth/oidc/login/callback', refreshEnabled: false },
      { clientId: 'parallellines-newapi', callbackUri: 'https://api.pingxingxian.space/oauth/pingxingxian', refreshEnabled: false },
    ]);
    assert.throws(() => loadConfiguration({ ...env, OPENWEBUI_OIDC_CLIENT_SECRET: env.CHAT_OIDC_CLIENT_SECRET }));
    assert.throws(() => loadConfiguration({ ...env, NEWAPI_OAUTH_CLIENT_SECRET: 'short' }));
    assert.throws(() => loadConfiguration({ ...env, OPENWEBUI_OIDC_CLIENT_SECRET: 'same-secret-'.repeat(4), NEWAPI_OAUTH_CLIENT_SECRET: 'same-secret-'.repeat(4) }));
    for (const invalid of [
      { PUBLIC_MAIN_ORIGIN: 'https://evil.example' },
      { PUBLIC_ISSUER: 'https://pingxingxian.space/other' },
      { MAIN_BACKEND_URL: 'http://api:8000/api/v1' },
      { MAIN_BACKEND_URL: 'https://pingxingxian.space/api/v1/auth/chat-sso' },
      { CHAT_OIDC_SIGNING_JWKS_FILE: '' },
    ]) assert.throws(() => loadConfiguration({ ...env, ...invalid }));
    const local = loadConfiguration({ ...env, PUBLIC_MAIN_ORIGIN: 'http://127.0.0.1:2001', PUBLIC_ISSUER: 'http://127.0.0.1:2002/auth/oidc', MAIN_BACKEND_URL: 'http://127.0.0.1:2001/api/v1' });
    assert.equal(local.secureCookies, false);
    assert.equal(local.callbackUri, 'http://127.0.0.1:2001/chat/oauth/openid/callback');
  } finally { await rm(directory, { recursive: true, force: true }); }
});
