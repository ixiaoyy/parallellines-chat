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
