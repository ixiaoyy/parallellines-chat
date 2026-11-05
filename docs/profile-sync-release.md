# Profile and authoritative-role release contract

Release owner executes this in the existing projects; this document does not
authorize creating another issuer or changing service secrets.

1. Build `main-profile-overlay/Dockerfile` using `MAIN_BASE_IMAGE` set to the exact
   unique local base tag whose inspected ID matches the running main API image.
   A bare `sha256:imageID` is not a Dockerfile repository reference. The release
   helper creates and verifies a new local alias without replacing existing tags.
   The build verifies one complete five-file manifest: either the original
   `baseline-sha256.json` or the previous profile
   `baseline-profile-v1-sha256.json`. It then replaces exactly four application
   files and verifies `output-sha256.json` including the unchanged user model. A
   mixed baseline or unknown drift fails before replacement. If the main
   API uses runtime source mounts, verify those separately and update only the
   reviewed four mounts; an image build cannot override a bind mount. Preserve
   environment, volumes, ports and services. Recreate only main API and verify
   health. The profile fields and `role` are additive; stored Main roles and
   balances are not changed. The Main checkout in this workspace has no SSO
   modules: use the complete `main-profile-overlay` release source, not a partial
   copy from that checkout. Runtime/source-mounted applications still require a
   separately approved, exact-file update; this helper does not edit host mounts.
2. Build `identity-bridge/Dockerfile`, including `main-profile.mjs`, under a new
   image tag. Merge `deploy/profile-sync-bridge.overlay.yaml` into the existing
   issuer project. Existing signing, client/cookie secrets and SQLite are reused.
   Only the two registered clients, Chat and NewAPI, receive real profile claims.
   The retired legacy client is not registered. `role` comes from the current Main
   session-state response and becomes UserInfo-only `pl_role` under `profile`.
   Missing `role` from older Main versions remains absent; an invalid present
   value fails authorization. No ID-token role claim is introduced.
3. Build `openwebui-profile-overlay/Dockerfile`; the official fixed image source
   is checked again. Merge `deploy/profile-sync-chat.overlay.yaml` into the
   existing candidate Chat project, preserving its configured API connections and
   all data. Native settings must actually resolve to the new scopes/flags.
   `OAUTH_SCOPES` must resolve to `openid profile email offline_access`, with Chat
   role/group mapping still disabled and `DEFAULT_USER_ROLE=user`. Recreate only
   Chat and verify its health before asking the user to sign in.
4. NewAPI login profile sync is an independent binary patch. Enable
   `MAIN_OAUTH_PROFILE_SYNC_ENABLED=true` and request `openid profile email` only
   after that binary is present. Keep provider ID, client, callback and user
   bindings unchanged. Do not enable the profile flag in a binary that lacks it.
5. First-request account creation and New API role synchronization additionally
   require its separately reviewed `MAIN_OAUTH_ACCOUNT_SYNC_ENABLED=true`
   release. Enable it only after Main and the issuer supply current verified
   profile plus `pl_role` and Chat requests email. With the flag disabled, the
   existing bound-account-only relay contract remains. Native registration,
   disabled/deleted-account, conflict, and credential-revocation controls remain
   the New API release owner's responsibility. This document neither allocates
   quota nor authorizes deployment.

The existing `deploy/deploy-profile-sync.py` applies one phase per approved scope.
For Main, take `desired_source` for its four `MAIN_FILES` from
`main-profile-overlay/output-sha256.json`; keep the fifth user-model hash in the
build manifest. For the bridge, record all four `BRIDGE_FILES` from this release.
For both phases, `expected_source` must be the actual reviewed runtime hashes,
not assumed from the local checkout. Preserve image IDs, names, source roots,
secrets, database mounts and existing Compose inputs. The historical identity-
refresh helper retains its old source lock and is not the role-release entry.

Retain previous images and Compose files for rollback. The Main/issuer upgrade
requires no data migration or new service credential. Rollback an individual component to its prior image and
config; do not discard databases or create replacement identities. Disable the
New API account-sync flag before rolling back its authority claims; retain all
accounts, bindings and later audit/billing writes created after activation.

Validation uses only synthetic offline tests and nonbilling health/login setup.
The release owner can check authenticated profile fields server-side and output
only equality/verification booleans. Full emails, tokens and secret config never
belong in receipts. With New API account sync disabled, profiles retain their
next-Main-OAuth-login timing; the new account-sync release also synchronizes on
verified Chat requests. Existing login sessions are not retroactively upgraded to request additional scopes by a
refresh request. Users missing email/profile claims sign out and select the
main-account login button inside Chat again; they do not need to visit the API
site. Check free model discovery with an ordinary user's own session and the
native model-read grants, without making a paid model request. Conflicts stop
profile synchronization; they never trigger a name/email-based merge.
