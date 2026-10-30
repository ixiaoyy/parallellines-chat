# Profile synchronization release contract

Release owner executes this in the existing projects; this document does not
authorize creating another issuer or changing service secrets.

1. Build `main-profile-overlay/Dockerfile` using `MAIN_BASE_IMAGE` set to the exact
   inspected running main API image ID/digest. The build verifies all five
   baseline hashes before replacing exactly four application files. If the main
   API uses runtime source mounts, verify those separately and update only the
   reviewed four mounts; an image build cannot override a bind mount. Preserve
   environment, volumes, ports and services. Recreate only main API and verify
   health. The profile fields are additive and old bridge clients remain valid.
2. Build `identity-bridge/Dockerfile`, including `main-profile.mjs`, under a new
   image tag. Merge `deploy/profile-sync-bridge.overlay.yaml` into the existing
   issuer project. Existing signing, client/cookie secrets and SQLite are reused.
   Only Chat and NewAPI receive real profile claims; the legacy client retains
   its old synthetic username and receives no email.
3. Build `openwebui-profile-overlay/Dockerfile`; the official fixed image source
   is checked again. Merge `deploy/profile-sync-chat.overlay.yaml` into the
   existing candidate Chat project, preserving its configured API connections and
   all data. Native settings must actually resolve to the new scopes/flags.
   Recreate only Chat and verify its health before asking the user to sign in.
4. NewAPI login profile sync is an independent binary patch. Enable
   `MAIN_OAUTH_PROFILE_SYNC_ENABLED=true` and request `openid profile email` only
   after that binary is present. Keep provider ID, client, callback and user
   bindings unchanged. Do not enable the profile flag in a binary that lacks it.

Retain previous images and Compose files for rollback. No data migration or new
credential is required. Rollback an individual component to its prior image and
config; do not discard databases or create replacement identities.

Validation uses only synthetic offline tests and nonbilling health/login setup.
The release owner can check authenticated profile fields server-side and output
only equality/verification booleans. Full emails, tokens and secret config never
belong in receipts. Current profiles update on the next Main OAuth login; existing
login sessions are not retroactively edited by a refresh request. Conflicts stop
profile synchronization; they never trigger a name/email-based merge.
