# Main-site trusted profile overlay

This small overlay extends the existing main-site SSO exchange and session-state
responses with the current `name`, `username`, `email`, `email_verified`, and
database-backed `role`. The stable `sub`, session validation, binding, code
redemption, stored roles and balances are unchanged. No new database column,
credential, token kind or password copy is introduced.

The four source files were prepared from the running main-site source snapshot.
`baseline-sha256.json` records the exact original hashes;
`baseline-profile-v1-sha256.json` records the prior profile overlay. The image
build accepts either complete reviewed source set, never a mixture. Unknown
drift stops the build and must be rebased instead of overwritten.
`output-sha256.json` verifies the complete resulting source after all four files
are copied. `models/user.py` is a reference hash only and is not replaced.

## Role authority

`SqlChatSsoRepository.get_user` reads the current database `User.role` on exchange
and every session-state check. Only exact `user`, `moderator`, and `admin` values
are accepted; other values fail the existing authorization boundary. No role is
inferred from a name, email, caller header, or prior grant. The bridge tolerates
an older Main response without `role`, but never supplies a default role claim.

The issuer publishes `pl_role` only in UserInfo under the `profile` scope, for
the two registered clients with `OIDC_MAIN_PROFILE_SYNC_ENABLED=true`. It never
adds the role to ID tokens or enables Chat role/group mapping. New API's opt-in
account synchronization is a separate consumer release; the source role
`admin` is distinct from `moderator` and is not a request to create a root user.

## Email trust

- Initial verification requires a consumed `EmailVerificationCode` for the same
  user and the current email, consumed no later than its expiry. The existing main
  service sets this field only after successful verification.
- Email changes require a matching `email_change` token with an explicit JSON
  boolean `email_verified: true`. Only the successfully redeemed token receives
  this marker in the same transaction as the email change.
- Other change tokens are also marked consumed when invalidated, so their
  `consumed_at` alone is never proof. Legacy changes without the marker are not
  retroactively considered verified.
- Unverified email is returned as `null` with `email_verified: false`; active
  status alone is not email verification. Downstream services must use the stable
  subject to find accounts and must not merge by name or email.

The response profile is read fresh from the main site on both exchange and
session-state checks. Revoked/expired/inactive sessions still return no profile.

## Validation

From this directory, in a Python 3.11+ environment with the main site's
SQLAlchemy 2 and Pydantic 2 dependencies:

```sh
python -m unittest discover -s tests -p 'test_*.py'
python -m py_compile app/core/chat_sso.py app/services/chat_sso.py app/services/auth.py app/schemas/chat_sso.py
```

The tests use only synthetic identities and in-memory SQLite, with settings,
security and unrelated service dependencies stubbed. The auth-method test
executes the real `confirm_email_change` method extracted from its AST in
isolation; it is not a full main-site integration test. No network, production
database, secret configuration, email delivery or model request is used.

Deployment and a real authenticated response check remain the release owner's
responsibility. Apply all four source files together so future successful email
changes receive proof that the profile reader understands.
