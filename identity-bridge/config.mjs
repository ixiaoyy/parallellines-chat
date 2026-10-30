import { lstatSync, readFileSync } from 'node:fs';
import path from 'node:path';

const loopback = url => ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
function safeUrl(value, name) {
  let url; try { url = new URL(value); } catch { throw new Error(`invalid_${name}`); }
  if (url.username || url.password || url.search || url.hash || !['http:', 'https:'].includes(url.protocol)) throw new Error(`invalid_${name}`);
  return url;
}

/** Reads runtime secrets only in the service process; errors never include their values. */
export function loadConfiguration(env = process.env) {
  const main = safeUrl(env.PUBLIC_MAIN_ORIGIN || 'https://pingxingxian.space', 'main_origin');
  const issuer = safeUrl(env.PUBLIC_ISSUER || main.origin + '/auth/oidc', 'issuer');
  const local = main.protocol === 'http:' && loopback(main) && issuer.protocol === 'http:' && loopback(issuer);
  if (main.pathname !== '/' || issuer.pathname !== '/auth/oidc' ||
      (!local && (main.origin !== 'https://pingxingxian.space' || issuer.origin !== main.origin))) throw new Error('invalid_public_oidc_configuration');
  const apiRoot = safeUrl(env.MAIN_BACKEND_URL || main.origin + '/api/v1', 'main_backend');
  if (apiRoot.pathname.replace(/\/$/, '') !== '/api/v1' ||
      (local ? !(apiRoot.protocol === 'http:' && loopback(apiRoot)) : apiRoot.origin !== main.origin)) throw new Error('invalid_main_backend');
  if (typeof env.CHAT_OIDC_CLIENT_SECRET !== 'string' || env.CHAT_OIDC_CLIENT_SECRET.length < 32 ||
      typeof env.CHAT_SSO_BRIDGE_SECRET !== 'string' || env.CHAT_SSO_BRIDGE_SECRET.length < 32) throw new Error('missing_oidc_service_secret');
  let cookieKeys;
  try { cookieKeys = JSON.parse(env.CHAT_OIDC_COOKIE_KEYS); } catch { throw new Error('invalid_oidc_cookie_keys'); }
  if (!Array.isArray(cookieKeys) || !cookieKeys.length || cookieKeys.some(key => typeof key !== 'string' || key.length < 32)) throw new Error('invalid_oidc_cookie_keys');
  if (!env.CHAT_OIDC_SIGNING_JWKS_FILE) throw new Error('missing_signing_jwks_file');
  const signingFile = lstatSync(env.CHAT_OIDC_SIGNING_JWKS_FILE);
  if (!signingFile.isFile() || signingFile.isSymbolicLink() || signingFile.size > 65536 ||
      (process.platform !== 'win32' && (signingFile.mode & 0o077) !== 0)) throw new Error('unsafe_signing_jwks_file');
  let jwks;
  try { jwks = JSON.parse(readFileSync(env.CHAT_OIDC_SIGNING_JWKS_FILE, 'utf8')); } catch { throw new Error('invalid_signing_jwks'); }
  if (!Array.isArray(jwks.keys) || !jwks.keys.length || jwks.keys.some(key =>
    key.kty !== 'RSA' || key.alg !== 'RS256' || typeof key.d !== 'string' || !key.d || typeof key.kid !== 'string' || !key.kid)) throw new Error('invalid_signing_jwks');
  const databasePath = path.resolve(env.OIDC_DATABASE_PATH || '/data/oidc.sqlite');
  const port = Number(env.PORT || 3002);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error('invalid_port');
  const clients = [{ clientId: 'parallellines-chat', clientSecret: env.CHAT_OIDC_CLIENT_SECRET,
    callbackUri: main.origin + '/chat/oauth/openid/callback', refreshEnabled: true }];
  for (const [key, clientId, callbackUri] of [
    ['OPENWEBUI_OIDC_CLIENT_SECRET', 'parallellines-openwebui', 'https://chat.pingxingxian.space/oauth/oidc/login/callback'],
    ['NEWAPI_OAUTH_CLIENT_SECRET', 'parallellines-newapi', 'https://api.pingxingxian.space/oauth/pingxingxian'],
  ]) {
    if (env[key] === undefined) continue;
    if (typeof env[key] !== 'string' || env[key].length < 32 || clients.some(client => client.clientSecret === env[key])) {
      throw new Error('invalid_independent_client_secret');
    }
    clients.push({ clientId, clientSecret: env[key], callbackUri, refreshEnabled: false });
  }
  return Object.freeze({
    issuer: issuer.href.replace(/\/$/, ''), mainOrigin: main.origin,
    callbackUri: main.origin + '/chat/oauth/openid/callback', apiRoot: apiRoot.href.replace(/\/$/, ''),
    clientSecret: env.CHAT_OIDC_CLIENT_SECRET, clients: Object.freeze(clients.map(Object.freeze)), cookieKeys, jwks,
    bridgeSecret: env.CHAT_SSO_BRIDGE_SECRET,
    databaseDirectory: path.dirname(databasePath), databaseFilename: path.basename(databasePath),
    host: env.HOST || '0.0.0.0', port, secureCookies: !local,
  });
}
