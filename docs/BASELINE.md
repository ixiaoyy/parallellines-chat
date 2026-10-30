# Recorded source baseline

Snapshot date: 2026-10-30. This record contains source identity only, no runtime
configuration, account identifiers, credentials, databases, or request logs.

Open WebUI is the unmodified official v0.12.5 image in `upstream.lock.json`.
The separate identity bridge was deployed as
`parallellines-chat-oidc:sso-recovery-20261030`. The deployment owner verified these
SHA-256 hashes, and the repository intake matched the source files byte for byte:

| File | SHA-256 |
| --- | --- |
| `identity-provider.mjs` | `1b85216a508781843e22da25ad5feb3c5602ffa5630280321bd8cae9eb8becfd` |
| `sqlite-adapter.mjs` | `7ff219281cf7b0529a0550598899d47b57c948d8ed0c4f9dd96d9e9857f7d99c` |
| `error-page.mjs` | `6563af6611b4412c6d7340dcc15993156b881981bfe9e9bb90a3098ba1135233` |

The deployment-side focused bridge suite reported 23 passes and one POSIX-only
skip on Windows. The packaged source also passed Node syntax checks. The real
user confirmed Chat main-site login; this does not validate model usage, quota
attribution, or administrator binding.

Templates are a normalized release reference, not a dump of the live Compose or
Nginx configuration. They use explicit external interpolation for existing
service paths/names. Future deployment must build from a reviewed Git commit and
record its image digest rather than relying on the historical local image tag.
