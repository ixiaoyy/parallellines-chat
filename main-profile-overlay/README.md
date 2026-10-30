# Main-site trusted profile overlay

This small overlay extends the existing main-site SSO exchange and session-state
responses with the current `name`, `username`, `email`, and `email_verified`.
The stable `sub`, session validation, binding, code redemption, roles and balances
are unchanged. No new database column, credential, token kind or password copy is
introduced.

The four source files were prepared from the running main-site source snapshot.
`baseline-sha256.json` records the exact original hashes. Before applying an
overlay, compare the target's current source hashes with that manifest. If they
differ, rebase the small changes onto the current source instead of overwriting
unrelated work. `models/user.py` is a reference hash only and is not replaced.

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
python tests/test_profile_channel.py
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
