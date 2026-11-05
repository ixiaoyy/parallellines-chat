import test from 'node:test';
import assert from 'node:assert/strict';
import { requireMainProfile } from '../main-profile.mjs';
import { validateIdentity } from '../main-bridge.mjs';

const identity = { active: true, sub: '53', binding_id: 'a'.repeat(40), auth_expires_at: 2000000000,
  username: 'main-user', name: 'Main display', email: 'Example@example.invalid', email_verified: true };

test('trusted profile preserves stable identity and normalizes only email case', () => {
  const value = validateIdentity(identity, { now: 1000000 });
  assert.equal(value.sub, '53');
  assert.deepEqual(requireMainProfile(value), { username: 'main-user', name: 'Main display',
    email: 'example@example.invalid', email_verified: true });
});

test('unverified or incomplete profiles fail closed; no email leaks from unverified data', () => {
  for (const patch of [{ email_verified: false }, { email_verified: 'true' }, { email_verified: undefined },
    { email: '' }, { email: 'invalid' }, { username: '' }, { username: 'a'.repeat(33) }, { name: '' }]) {
    const value = validateIdentity({ ...identity, ...patch }, { now: 1000000 });
    assert.throws(() => requireMainProfile(value), /main_profile_unverified/);
    if (patch.email_verified !== undefined || Object.hasOwn(patch, 'email_verified')) assert.equal(value.email, undefined);
  }
});

test('Main roles retain exact authority values; missing legacy role grants nothing', () => {
  assert.equal(Object.hasOwn(validateIdentity(identity, { now: 1000000 }), 'role'), false);
  for (const role of ['user', 'moderator', 'admin']) {
    assert.equal(validateIdentity({ ...identity, role }, { now: 1000000 }).role, role);
  }
  for (const role of [undefined, null, '', 'root', 'ADMIN', true, 100, ['admin']]) {
    assert.throws(() => validateIdentity({ ...identity, role }, { now: 1000000 }), /main_role_invalid/);
  }
});
