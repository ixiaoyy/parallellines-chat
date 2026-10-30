import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join, resolve, sep } from 'node:path';
import { test } from 'node:test';
import { createSqliteStore } from '../sqlite-adapter.mjs';

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), 'pl-oidc-sqlite-'));
  let now = 1_800_000_000_000;
  const options = { directory, clock: () => now };
  const store = createSqliteStore(options);
  return { directory, options, store, advance(ms) { now += ms; }, cleanup() {
    store.close();
    if (!resolve(directory).startsWith(resolve(tmpdir()) + sep) || !basename(directory).startsWith('pl-oidc-sqlite-')) {
      throw new Error('Unsafe test cleanup path');
    }
    rmSync(directory, { recursive: true, force: true });
  } };
}

async function grant(store, id = 'grant-1', expiresAt = 1_800_000_120) {
  const adapter = new store.Adapter('Grant');
  await adapter.upsert(id, { jti: id, accountId: 'main:1', clientId: 'parallellines-chat' }, 300);
  store.setGrantBinding(id, { sub: 'main:1', binding_id: 'binding-1', auth_expires_at: expiresAt, name: 'Synthetic member' });
}

test('OIDC payloads, secondary indexes, consumed marker and identity survive restart', async () => {
  const f = fixture();
  let restarted;
  try {
    await grant(f.store);
    const sessions = new f.store.Adapter('Session');
    await sessions.upsert('session-1', { uid: 'session-uid', value: 'synthetic' }, 60);
    const codes = new f.store.Adapter('AuthorizationCode');
    await codes.upsert('code-1', { jti: 'code-1', grantId: 'grant-1', userCode: 'ABCD' }, 60);
    await codes.consume('code-1');
    f.store.close();
    restarted = createSqliteStore(f.options);
    assert.equal((await new restarted.Adapter('Session').findByUid('session-uid')).value, 'synthetic');
    const restoredCode = await new restarted.Adapter('AuthorizationCode').findByUserCode('ABCD');
    assert.equal(restoredCode.jti, 'code-1');
    assert.equal(restoredCode.consumed, 1_800_000_000);
    assert.equal(restarted.getGrantBinding('grant-1').binding_id, 'binding-1');
  } finally { restarted?.close(); f.cleanup(); }
});

test('parallel consume has one winner and stale upsert cannot clear consumed state', async () => {
  const f = fixture();
  let second;
  try {
    const tokens = new f.store.Adapter('AuthorizationCode');
    await tokens.upsert('once', { jti: 'once' }, 60);
    second = createSqliteStore(f.options);
    const other = new second.Adapter('AuthorizationCode');
    const wins = await Promise.all(Array.from({ length: 64 }, (_, i) => (i % 2 ? tokens : other).consumeOnce('once')));
    assert.equal(wins.filter(Boolean).length, 1);
    await tokens.upsert('once', { jti: 'once' }, 60);
    assert.equal((await tokens.find('once')).consumed, 1_800_000_000);
    await assert.rejects(tokens.consume('once'), { error: 'invalid_grant' });
  } finally { second?.close(); f.cleanup(); }
});

test('secondary index reassignment never revives an older row when the newest expires', async () => {
  const f = fixture();
  try {
    const adapter = new f.store.Adapter('Session');
    await adapter.upsert('older', { uid: 'shared-uid', userCode: 'shared-code', version: 'old' }, 120);
    await adapter.upsert('newer', { uid: 'shared-uid', userCode: 'shared-code', version: 'new' }, 1);
    assert.equal((await adapter.findByUid('shared-uid')).version, 'new');
    assert.equal((await adapter.findByUserCode('shared-code')).version, 'new');
    f.advance(1000);
    assert.equal(await adapter.findByUid('shared-uid'), undefined);
    assert.equal(await adapter.findByUserCode('shared-code'), undefined);
    assert.equal((await adapter.find('older')).version, 'old');
  } finally { f.cleanup(); }
});

test('TTL rejects expired records/index lookups/consume and main expiry clamps tokens', async () => {
  const f = fixture();
  try {
    await grant(f.store, 'grant-1', 1_800_000_010);
    const tokens = new f.store.Adapter('RefreshToken');
    await tokens.upsert('refresh-1', { jti: 'refresh-1', grantId: 'grant-1', uid: 'ttl-uid', userCode: 'ttl-code' }, 3600);
    f.advance(10_000);
    assert.equal(await tokens.find('refresh-1'), undefined);
    assert.equal(await tokens.findByUid('ttl-uid'), undefined);
    assert.equal(await tokens.findByUserCode('ttl-code'), undefined);
    assert.equal(await tokens.consumeOnce('refresh-1'), false);
    assert.equal(f.store.getGrantBinding('grant-1'), undefined);
    assert.deepEqual(f.store.pruneExpired(), { records: 2, bindings: 1 });
  } finally { f.cleanup(); }
});

test('expired interaction exposes only recovery context and remains unavailable to authentication after restart', async () => {
  const f = fixture();
  let restarted;
  try {
    const id = 'expired-recovery-interaction';
    const clientId = 'parallellines-chat';
    const redirectUri = 'https://pingxingxian.space/chat/oauth/openid/callback';
    const interactions = new f.store.Adapter('Interaction');
    await interactions.upsert(id, {
      uid: 'recovery-uid', userCode: 'recovery-code',
      params: { client_id: clientId, redirect_uri: redirectUri, state: 'private-state', nonce: 'private-nonce' },
      session: { accountId: 'private-main-user', loginTs: 1_800_000_000 },
      result: { login: { accountId: 'private-main-user' } },
    }, 1);
    assert.ok(await interactions.find(id));
    assert.deepEqual(f.store.getInteractionRecoveryContext(id), { clientId, redirectUri, expired: false });

    // Use the exact expiry boundary; reading recovery information must not extend TTL.
    f.advance(1000);
    const context = f.store.getInteractionRecoveryContext(id);
    assert.deepEqual(context, { clientId, redirectUri, expired: true });
    assert.equal(Object.isFrozen(context), true);
    assert.doesNotMatch(JSON.stringify(context), /private-main-user|private-state|private-nonce|recovery-code/);
    assert.equal(await interactions.find(id), undefined);
    assert.equal(await interactions.findByUid('recovery-uid'), undefined);
    assert.equal(await interactions.findByUserCode('recovery-code'), undefined);
    assert.equal(await interactions.consumeOnce(id), false);
    await assert.rejects(interactions.consume(id), { error: 'invalid_grant' });
    assert.deepEqual(f.store.getInteractionRecoveryContext(id), context);

    // No change in behavior when the persistent adapter is reopened.
    f.store.close();
    restarted = createSqliteStore(f.options);
    const reopenedInteractions = new restarted.Adapter('Interaction');
    assert.deepEqual(restarted.getInteractionRecoveryContext(id), context);
    assert.equal(await reopenedInteractions.find(id), undefined);
    assert.equal(await reopenedInteractions.consumeOnce(id), false);

    await new restarted.Adapter('Session').upsert('wrong-model', {
      params: { client_id: clientId, redirect_uri: redirectUri },
    }, 60);
    await reopenedInteractions.upsert('unknown-client', {
      params: { client_id: 'unregistered-client', redirect_uri: redirectUri },
    }, 60);
    await reopenedInteractions.upsert('invalid-redirect', {
      params: { client_id: clientId, redirect_uri: { value: redirectUri } },
    }, 60);
    for (const unavailable of ['missing', 'wrong-model', 'unknown-client', 'invalid-redirect', '', null]) {
      assert.equal(restarted.getInteractionRecoveryContext(unavailable), undefined);
    }
    assert.deepEqual(restarted.pruneExpired(), { records: 1, bindings: 0 });
    assert.equal(restarted.getInteractionRecoveryContext(id), undefined);
    assert.equal(await reopenedInteractions.find(id), undefined);
  } finally { restarted?.close(); f.cleanup(); }
});

test('grant/binding revocation removes all associated models and blocks resurrection after restart', async () => {
  const f = fixture();
  let restarted;
  try {
    await grant(f.store);
    for (const model of ['AccessToken', 'AuthorizationCode', 'RefreshToken']) {
      await new f.store.Adapter(model).upsert(model + '-1', { jti: model + '-1', grantId: 'grant-1' }, 60);
    }
    assert.equal(f.store.revokeBinding('binding-1'), 1);
    assert.equal(f.store.getGrantBinding('grant-1'), undefined);
    for (const model of ['Grant', 'AccessToken', 'AuthorizationCode', 'RefreshToken']) {
      assert.equal(await new f.store.Adapter(model).find(model === 'Grant' ? 'grant-1' : model + '-1'), undefined);
    }
    f.store.close();
    restarted = createSqliteStore(f.options);
    await assert.rejects(new restarted.Adapter('RefreshToken').upsert('late', { grantId: 'grant-1' }, 60), { error: 'invalid_grant' });
    await assert.rejects(new restarted.Adapter('Grant').upsert('grant-1', { accountId: 'main:1', clientId: 'parallellines-chat' }, 60), { error: 'invalid_grant' });
  } finally { restarted?.close(); f.cleanup(); }
});

test('binding requires the persisted client/subject and cannot change identity or extend expiry', async () => {
  const f = fixture();
  try {
    const identity = { sub: 'main:1', binding_id: 'binding-1', auth_expires_at: 1_800_000_120, name: 'Synthetic member' };
    assert.throws(() => f.store.setGrantBinding('missing', identity));
    await grant(f.store);
    assert.throws(() => f.store.setGrantBinding('grant-1', { ...identity, sub: 'main:2' }));
    assert.throws(() => f.store.setGrantBinding('grant-1', { ...identity, binding_id: 'other' }));
    assert.throws(() => f.store.setGrantBinding('grant-1', { ...identity, auth_expires_at: identity.auth_expires_at + 1 }));
    f.store.setGrantBinding('grant-1', { ...identity, auth_expires_at: identity.auth_expires_at - 1 });
    assert.equal(f.store.getGrantBinding('grant-1').auth_expires_at, identity.auth_expires_at - 1);
  } finally { f.cleanup(); }
});

test('dedicated directory/database use 0700/0600 on POSIX with no WAL files', { skip: process.platform === 'win32' }, async () => {
  const f = fixture();
  try {
    await new f.store.Adapter('Session').upsert('permissions', {}, 60);
    assert.equal(statSync(f.directory).mode & 0o777, 0o700);
    assert.equal(statSync(f.store.databasePath).mode & 0o777, 0o600);
    assert.ok(!readdirSync(f.directory).some(name => name.endsWith('-wal') || name.endsWith('-shm')));
  } finally { f.cleanup(); }
});
