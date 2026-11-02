import { DatabaseSync } from 'node:sqlite';
import { chmodSync, closeSync, constants, existsSync, lstatSync, mkdirSync, openSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { errors } from 'oidc-provider';

const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 2048;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const nowSeconds = clock => Math.max(1, Math.floor(clock() / 1000));

/**
 * Persistent single-process oidc-provider adapter and main-session grant bindings.
 * The caller must derive identity exclusively from its authenticated main exchange.
 * No identity, approval code, or token is logged. Never share this DB between replicas.
 */
export function createSqliteStore({ directory, filename = 'oidc.sqlite', clock = Date.now,
  clientId = 'parallellines-openwebui', clientIds = [clientId] } = {}) {
  if (!validId(directory) || filename !== basename(filename) || !/^[A-Za-z0-9_.-]+$/.test(filename) ||
      !validId(clientId) || !Array.isArray(clientIds) || !clientIds.length || !clientIds.every(validId) ||
      new Set(clientIds).size !== clientIds.length || typeof clock !== 'function') throw new Error('Invalid persistence configuration');
  const allowedClientIds = new Set(clientIds);
  const root = resolve(directory);
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const dirStat = lstatSync(root);
  if (!dirStat.isDirectory() || dirStat.isSymbolicLink()) throw new Error('Persistence directory must be a real directory');
  chmodSync(root, 0o700);
  const databasePath = join(root, filename);
  if (existsSync(databasePath)) {
    const dbStat = lstatSync(databasePath);
    if (!dbStat.isFile() || dbStat.isSymbolicLink()) throw new Error('Persistence file must be a real file');
  }
  const fd = openSync(databasePath, constants.O_CREAT | constants.O_RDWR | (constants.O_NOFOLLOW || 0), 0o600);
  closeSync(fd);
  chmodSync(databasePath, 0o600);
  const db = new DatabaseSync(databasePath);
  // DELETE journaling avoids independent WAL/SHM lifecycle and makes a stopped backup one file.
  // SQLite rollback journals inherit the database mode; the dedicated 0700 directory also protects them.
  db.exec(`PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;
    PRAGMA foreign_keys=ON; PRAGMA secure_delete=ON;
    CREATE TABLE IF NOT EXISTS oidc_records (
      model TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
      expires_at REAL, consumed_at INTEGER, grant_id TEXT, uid TEXT, user_code TEXT,
      version INTEGER NOT NULL, PRIMARY KEY(model,id)
    );
    CREATE INDEX IF NOT EXISTS oidc_records_grant ON oidc_records(grant_id);
    CREATE INDEX IF NOT EXISTS oidc_records_uid ON oidc_records(model,uid);
    CREATE INDEX IF NOT EXISTS oidc_records_user_code ON oidc_records(model,user_code);
    CREATE INDEX IF NOT EXISTS oidc_records_expiry ON oidc_records(expires_at);
    CREATE TABLE IF NOT EXISTS oidc_store_meta (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL);
    INSERT OR IGNORE INTO oidc_store_meta(id,version) VALUES(1,0);
    CREATE TABLE IF NOT EXISTS oidc_grant_bindings (
      grant_id TEXT PRIMARY KEY, sub TEXT NOT NULL, binding_id TEXT NOT NULL,
      auth_expires_at INTEGER NOT NULL, name TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS oidc_binding_id ON oidc_grant_bindings(binding_id);
    CREATE TABLE IF NOT EXISTS oidc_revoked_grants (grant_id TEXT PRIMARY KEY, revoked_at INTEGER NOT NULL);
  `);
  let closed = false;

  function transaction(work) {
    db.exec('BEGIN IMMEDIATE');
    try { const result = work(); db.exec('COMMIT'); return result; }
    catch (error) { db.exec('ROLLBACK'); throw error; }
  }

  function revoked(grantId) {
    return Boolean(grantId && db.prepare('SELECT 1 FROM oidc_revoked_grants WHERE grant_id=?').get(grantId));
  }

  function deserialize(row) {
    if (!row) return undefined;
    const value = JSON.parse(row.payload);
    if (!object(value)) throw new Error('Invalid persisted OIDC record');
    if (row.consumed_at !== null) value.consumed = row.consumed_at;
    return value;
  }

  const liveSql = `(expires_at IS NULL OR expires_at > ?) AND NOT EXISTS
    (SELECT 1 FROM oidc_revoked_grants AS r WHERE r.grant_id=oidc_records.grant_id)`;

  function revokeGrantWithinTransaction(grantId) {
    db.prepare('INSERT OR IGNORE INTO oidc_revoked_grants(grant_id,revoked_at) VALUES(?,?)').run(grantId, nowSeconds(clock));
    db.prepare('DELETE FROM oidc_records WHERE grant_id=? OR (model=\'Grant\' AND id=?)').run(grantId, grantId);
    db.prepare('DELETE FROM oidc_grant_bindings WHERE grant_id=?').run(grantId);
  }

  class Adapter {
    constructor(model) {
      if (!validId(model)) throw new Error('Invalid OIDC model');
      this.model = model;
    }

    async upsert(id, payload, expiresIn) {
      if (!validId(id) || !object(payload) ||
          (expiresIn !== undefined && (typeof expiresIn !== 'number' || !Number.isFinite(expiresIn)))) {
        throw new Error('Invalid OIDC record');
      }
      const grantId = this.model === 'Grant' ? id : (validId(payload.grantId) ? payload.grantId : null);
      transaction(() => {
        if (revoked(grantId)) throw new errors.InvalidGrant('grant revoked');
        let expiresAt = expiresIn === undefined ? null : clock() + Math.max(0, expiresIn) * 1000;
        if (typeof payload.exp === 'number' && Number.isFinite(payload.exp)) {
          expiresAt = expiresAt === null ? payload.exp * 1000 : Math.min(expiresAt, payload.exp * 1000);
        }
        const binding = grantId && db.prepare('SELECT auth_expires_at FROM oidc_grant_bindings WHERE grant_id=?').get(grantId);
        if (binding) expiresAt = expiresAt === null ? binding.auth_expires_at * 1000 : Math.min(expiresAt, binding.auth_expires_at * 1000);
        const version = db.prepare('UPDATE oidc_store_meta SET version=version+1 WHERE id=1 RETURNING version').get().version;
        const consumed = Number.isInteger(payload.consumed) && payload.consumed > 0 ? payload.consumed : null;
        // Secondary indexes point at the newest upsert, even after it later expires.
        // Do not fall back to an older session/user-code row after reassignment.
        if (validId(payload.uid)) db.prepare('UPDATE oidc_records SET uid=NULL WHERE model=? AND uid=? AND id<>?')
          .run(this.model, payload.uid, id);
        if (validId(payload.userCode)) db.prepare('UPDATE oidc_records SET user_code=NULL WHERE model=? AND user_code=? AND id<>?')
          .run(this.model, payload.userCode, id);
        db.prepare(`INSERT INTO oidc_records(model,id,payload,expires_at,consumed_at,grant_id,uid,user_code,version)
          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(model,id) DO UPDATE SET
          payload=excluded.payload, expires_at=excluded.expires_at,
          consumed_at=COALESCE(oidc_records.consumed_at,excluded.consumed_at),
          grant_id=excluded.grant_id,uid=excluded.uid,user_code=excluded.user_code,version=excluded.version`).run(
          this.model, id, JSON.stringify(payload), expiresAt, consumed, grantId,
          validId(payload.uid) ? payload.uid : null, validId(payload.userCode) ? payload.userCode : null, version);
      });
    }

    async find(id) {
      if (!validId(id)) return undefined;
      // OP 在静态注册未命中后仍会查 Client 表；不得让历史客户端记录恢复已下线的 RP。
      if (this.model === 'Client' && !allowedClientIds.has(id)) return undefined;
      return deserialize(db.prepare(`SELECT payload,consumed_at FROM oidc_records WHERE model=? AND id=? AND ${liveSql}`)
        .get(this.model, id, clock()));
    }

    async findByUid(uid) {
      if (!validId(uid)) return undefined;
      return deserialize(db.prepare(`SELECT payload,consumed_at FROM oidc_records WHERE model=? AND uid=? AND ${liveSql}
        ORDER BY version DESC LIMIT 1`).get(this.model, uid, clock()));
    }

    async findByUserCode(userCode) {
      if (!validId(userCode)) return undefined;
      return deserialize(db.prepare(`SELECT payload,consumed_at FROM oidc_records WHERE model=? AND user_code=? AND ${liveSql}
        ORDER BY version DESC LIMIT 1`).get(this.model, userCode, clock()));
    }

    /** One atomic conditional UPDATE; only one concurrent redeemer can observe success. */
    async consumeOnce(id) {
      if (!validId(id)) return false;
      const result = db.prepare(`UPDATE oidc_records SET consumed_at=? WHERE model=? AND id=?
        AND consumed_at IS NULL AND ${liveSql}`).run(nowSeconds(clock), this.model, id, clock());
      return result.changes === 1;
    }

    async consume(id) {
      if (!await this.consumeOnce(id)) throw new errors.InvalidGrant('grant source already used or unavailable');
    }

    async destroy(id) {
      if (!validId(id)) return;
      if (this.model === 'Grant') return store.revokeGrant(id);
      db.prepare('DELETE FROM oidc_records WHERE model=? AND id=?').run(this.model, id);
    }

    async revokeByGrantId(grantId) { store.revokeGrant(grantId); }
  }

  const store = {
    Adapter,
    databasePath,
    /** Recovery routing only: never exposes credentials or revives an expired interaction. */
    getInteractionRecoveryContext(id) {
      if (!validId(id)) return undefined;
      const row = db.prepare("SELECT payload,expires_at FROM oidc_records WHERE model='Interaction' AND id=?").get(id);
      if (!row) return undefined;
      const payload = JSON.parse(row.payload);
      const client = payload?.params?.client_id;
      if (!allowedClientIds.has(client) || typeof payload?.params?.redirect_uri !== 'string') return undefined;
      return Object.freeze({ clientId: client, redirectUri: payload.params.redirect_uri,
        expired: row.expires_at !== null && row.expires_at <= clock() });
    },
    /** Identity is immutable per grant; shortening its original main expiry is allowed. */
    setGrantBinding(grantId, identity, expectedClientId = clientId) {
      if (!validId(grantId) || !object(identity) || !validId(identity.sub) || !validId(identity.binding_id) ||
          !allowedClientIds.has(expectedClientId) ||
          !Number.isSafeInteger(identity.auth_expires_at) || identity.auth_expires_at <= clock() / 1000 ||
          typeof identity.name !== 'string' || identity.name.length > 512) throw new Error('Invalid main grant binding');
      return transaction(() => {
        if (revoked(grantId)) throw new errors.InvalidGrant('grant revoked');
        const grant = db.prepare(`SELECT payload,consumed_at FROM oidc_records WHERE model='Grant' AND id=? AND ${liveSql}`)
          .get(grantId, clock());
        const payload = deserialize(grant);
        if (!payload || payload.accountId !== identity.sub || payload.clientId !== expectedClientId) {
          throw new Error('Main binding does not match persisted grant');
        }
        const previous = db.prepare('SELECT * FROM oidc_grant_bindings WHERE grant_id=?').get(grantId);
        if (previous && (previous.sub !== identity.sub || previous.binding_id !== identity.binding_id ||
            identity.auth_expires_at > previous.auth_expires_at)) throw new Error('Main grant binding cannot be rebound or extended');
        db.prepare(`INSERT INTO oidc_grant_bindings(grant_id,sub,binding_id,auth_expires_at,name) VALUES(?,?,?,?,?)
          ON CONFLICT(grant_id) DO UPDATE SET auth_expires_at=excluded.auth_expires_at,name=excluded.name`).run(
          grantId, identity.sub, identity.binding_id, identity.auth_expires_at, identity.name);
        db.prepare(`UPDATE oidc_records SET expires_at=CASE WHEN expires_at IS NULL THEN ? ELSE MIN(expires_at,?) END
          WHERE grant_id=?`).run(identity.auth_expires_at * 1000, identity.auth_expires_at * 1000, grantId);
        return store.getGrantBinding(grantId, expectedClientId);
      });
    },
    getGrantBinding(grantId, expectedClientId) {
      if (!validId(grantId) || revoked(grantId)) return undefined;
      const grant = deserialize(db.prepare(`SELECT payload,consumed_at FROM oidc_records WHERE model='Grant' AND id=? AND ${liveSql}`)
        .get(grantId, clock()));
      if (!grant || !allowedClientIds.has(grant.clientId) ||
          (expectedClientId !== undefined && grant.clientId !== expectedClientId)) return undefined;
      const row = db.prepare('SELECT sub,binding_id,auth_expires_at,name FROM oidc_grant_bindings WHERE grant_id=? AND auth_expires_at>?')
        .get(grantId, clock() / 1000);
      return row && row.sub === grant.accountId ? Object.freeze({ ...row }) : undefined;
    },
    revokeGrant(grantId) {
      if (!validId(grantId)) return;
      transaction(() => revokeGrantWithinTransaction(grantId));
    },
    revokeBinding(bindingId) {
      if (!validId(bindingId)) return 0;
      return transaction(() => {
        const grants = db.prepare('SELECT grant_id FROM oidc_grant_bindings WHERE binding_id=?').all(bindingId);
        for (const { grant_id } of grants) revokeGrantWithinTransaction(grant_id);
        return grants.length;
      });
    },
    /** Expired lookup always fails immediately; this removes inactive payloads from storage. */
    pruneExpired() {
      return transaction(() => ({
        records: db.prepare('DELETE FROM oidc_records WHERE expires_at IS NOT NULL AND expires_at<=?').run(clock()).changes,
        bindings: db.prepare('DELETE FROM oidc_grant_bindings WHERE auth_expires_at<=?').run(clock() / 1000).changes,
      }));
    },
    close() { if (!closed) { db.close(); closed = true; } },
  };
  return store;
}
