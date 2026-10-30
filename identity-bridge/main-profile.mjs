import { MainAuthorityError } from './main-bridge.mjs';

// Only use profile data returned by the authenticated Main session-state endpoint.
// No email, username, or display name is ever an account lookup key here.
export function requireMainProfile(identity) {
  const { username, name, email, email_verified } = identity;
  if (typeof username !== 'string' || !username.trim() || username.length > 32 ||
      typeof name !== 'string' || !name.trim() || name.length > 80 ||
      email_verified !== true || typeof email !== 'string' || email.length > 255 ||
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new MainAuthorityError('main_profile_unverified');
  }
  return { username, name, email: email.toLowerCase(), email_verified: true };
}
