import test from 'node:test';
import assert from 'node:assert/strict';

class Storage {
  getItem(key) { return this[key] ?? null; }
  setItem(key, value) { this[key] = String(value); }
  removeItem(key) { delete this[key]; }
}
const central = { available: true, authenticated: true, subject: 'main-A', auth_epoch: 'epoch-A' };
const anonymous = { available: true, authenticated: false, subject: null, auth_epoch: null };
const jwt = claims => 'header.' + Buffer.from(JSON.stringify(claims)).toString('base64url') + '.signature';
const moduleForTest = () => import('./main-unified-browser.mjs?test=' + Math.random());
function fixture() {
  const calls = []; const events = [];
  const environment = { localStorage: new Storage(), sessionStorage: new Storage(), document: { cookie: '' },
    location: { pathname: '/', search: '', hash: '', assign: url => calls.push(['assign', url]), replace: url => calls.push(['replace', url]) },
    atob: value => Buffer.from(value, 'base64').toString('utf8'), dispatchEvent: event => events.push(event.type),
    fetch: async (url, options) => { calls.push(['fetch', url, options]); return { status: 200, ok: true, json: async () => ({ unified: true, subject: 'main-A' }) }; } };
  return { environment, calls, events };
}

test('guest draft stays local, bounded and never sends a model request', async () => {
  const m = await moduleForTest(); const f = fixture();
  m.saveGuestDraft('文本草稿'.repeat(8000), f.environment.sessionStorage);
  assert.equal(m.readGuestDraft(f.environment.sessionStorage).length, 20000);
  m.loginForFeature('中文草稿', f.environment);
  assert.deepEqual(f.calls, [['assign', '/oauth/oidc/login']]);
  assert.equal(m.consumeGuestDraft(f.environment.sessionStorage), '中文草稿');
  assert.equal(m.consumeGuestDraft(f.environment.sessionStorage), '');
  for (const value of ['//evil.invalid', '/%2f%2fevil.invalid', '/auth?token=x', '/oauth/oidc/login', '/\\evil.invalid']) assert.equal(m.safeReturn(value), '/');
  assert.equal(m.safeReturn('/c/123?x=1#last'), '/c/123?x=1#last');
  assert.equal(m.isMainLogout('https://pingxingxian.space/auth/unified/logout?ticket=' + 't'.repeat(43)), true);
  assert.equal(m.isMainLogout('https://pingxingxian.space/auth/unified/logout?ticket=' + 't'.repeat(43) + '&ticket=' + 'u'.repeat(43)), false);
});

test('authority unavailable preserves old credentials; central anonymous does not force a login wall', async () => {
  const m = await moduleForTest(); const f = fixture();
  f.environment.localStorage.setItem('token', jwt({ pl_unified: { subject: 'main-A' } }));
  assert.deepEqual(await m.syncUnifiedSession({ available: false }, f.environment), { available: false });
  assert.equal(f.calls.length, 0); assert.ok(f.environment.localStorage.getItem('token'));
  f.environment.localStorage.removeItem('token');
  assert.deepEqual(await m.syncUnifiedSession(anonymous, f.environment), { authenticated: false });
  assert.equal(f.calls.length, 0);
});

test('A to B clears private UI before redirect and does not revoke another device', async () => {
  const m = await moduleForTest(); const f = fixture();
  f.environment.localStorage.setItem('token', jwt({ pl_unified: { subject: 'main-A' } }));
  f.environment.localStorage.setItem('chat-input-old', 'A private text');
  f.environment.localStorage.setItem('settings', 'A settings');
  f.environment.sessionStorage.setItem('chat-input-old', 'A private session draft');
  f.environment.sessionStorage.setItem('pendingOAuthToolId', 'A private tool');
  f.environment.localStorage.setItem('locale', 'zh-CN');
  assert.deepEqual(await m.syncUnifiedSession({ ...central, subject: 'main-B', auth_epoch: 'epoch-B' }, f.environment), { pending: true });
  assert.deepEqual(f.events, ['pl:unified-private-reset']);
  assert.equal(f.environment.localStorage.getItem('token'), null);
  assert.equal(f.environment.localStorage.getItem('chat-input-old'), null);
  assert.equal(f.environment.localStorage.getItem('settings'), null);
  assert.equal(f.environment.sessionStorage.getItem('chat-input-old'), null);
  assert.equal(f.environment.sessionStorage.getItem('pendingOAuthToolId'), null);
  assert.equal(f.environment.localStorage.getItem('locale'), 'zh-CN');
  assert.equal(f.calls[0][1], '/api/v1/auths/unified/local-signout');
  assert.deepEqual(f.calls.at(-1), ['assign', '/oauth/oidc/login']);
  await m.syncUnifiedSession({ ...central, subject: 'main-B', auth_epoch: 'epoch-B' }, f.environment);
  assert.equal(f.calls.filter(item => item[0] === 'assign').length, 1);
});

test('same subject requires backend liveness; network outage keeps credentials; recovery requires backend confirmation', async () => {
  const m = await moduleForTest(); const f = fixture();
  f.environment.localStorage.setItem('token', jwt({ pl_unified: { subject: 'main-A' } }));
  f.environment.fetch = async () => ({ status: 503, ok: false });
  assert.deepEqual(await m.syncUnifiedSession(central, f.environment), { available: false });
  assert.ok(f.environment.localStorage.getItem('token'));
  f.environment.fetch = async () => ({ status: 200, ok: true, json: async () => ({ unified: true, subject: 'main-A' }) });
  assert.deepEqual(await m.syncUnifiedSession(central, f.environment), { authenticated: true });
  f.environment.localStorage.setItem('token', jwt({ pl_unified_recovery: true }));
  f.environment.fetch = async () => ({ status: 200, ok: true, json: async () => ({ recovery: true }) });
  assert.deepEqual(await m.syncUnifiedSession(anonymous, f.environment), { recovery: true });
  assert.ok(f.environment.localStorage.getItem('token'));
});

test('iframe strictly validates origin/source/nonce and never treats timeout as anonymous', async () => {
  const m = await moduleForTest(); const listeners = new Map(); const timers = new Map(); const frames = []; const states = [];
  const frame = { contentWindow: { postMessage() {} }, addEventListener() {}, remove() { this.removed = true; } };
  const environment = { document: { createElement: () => frame, body: { appendChild: value => frames.push(value) } },
    crypto: { randomUUID: () => '01234567-89ab-cdef-0123-456789abcdef' },
    window: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) },
    setTimeout: (fn, ms) => { timers.set(1, fn); assert.equal(ms, 7000); return 1; }, clearTimeout: id => timers.delete(id) };
  const stop = m.watchUnifiedSession(value => states.push(value), environment);
  const data = { type: m.STATE_TYPE, version: 1, request_id: '0123456789abcdef0123456789abcdef', ...central };
  const message = (extra = {}) => listeners.get('message')({ origin: m.MAIN_ORIGIN, source: frame.contentWindow, data, ...extra });
  message({ origin: 'https://evil.invalid' }); message({ source: {} }); message({ data: { ...data, request_id: 'f'.repeat(32) } });
  assert.equal(states.length, 0);
  timers.get(1)(); assert.deepEqual(states, [{ available: false }]);
  message(); assert.equal(states.at(-1).subject, 'main-A');
  message({ data: { ...data, authenticated: false, subject: 'main-A', auth_epoch: null } });
  assert.equal(states.length, 2);
  stop(); assert.equal(frame.removed, true); assert.equal(listeners.size, 0); assert.equal(frames.length, 1);
});
