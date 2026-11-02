# Operations

## Identity configuration

The existing main site must provide `/auth/chat`, its authenticated identity
exchange/session API, and the `/auth/oidc` proxy route. This repository does not
replace the main-site application. The bridge retains that service protocol and
registers two exact callbacks; separate client secrets are required:

| Client | Callback | Refresh |
| --- | --- | --- |
| `parallellines-openwebui` | `https://chat.pingxingxian.space/oauth/oidc/login/callback` | enabled with offline consent |
| `parallellines-newapi` | `https://api.pingxingxian.space/oauth/pingxingxian` | disabled |

All clients use `client_secret_basic` and PKCE S256. Do not weaken callback,
state, subject, or client binding checks to work around a failed login. The API
client needs the opt-in PKCE patch from ParallelLines-Api. This is OAuth/UserInfo
for New API; it does not claim New API validates ID-token nonces.

Chat requests `openid profile offline_access` and native
`OAUTH_AUTHORIZE_PARAMS={"prompt":"consent"}`. Both are required for a Chat refresh
token. The retired LibreChat RP is not registered; the New API login client cannot
obtain a refresh token. Every refresh checks the Main binding again, rotates the
token, and remains bounded by the original Main expiry. Revocation, identity
change, expiry, or an unavailable Main authority fails closed. Users with an
older Chat login need to sign out and select the main-account button again to
obtain a refresh token; changing environment values cannot upgrade old tokens.

The JSON UserInfo endpoint is `https://pingxingxian.space/auth/oidc/me`. It adds
`iss` (the fixed issuer) and `aud` (the actual token's registered client ID, a
string) while retaining the existing subject/profile/binding claims. It does not
take audience or identity from caller headers or query parameters. Standard ID
token signing and audiences are unchanged. `pl_auth_exp` is integer epoch seconds;
`pl_binding` is an opaque Main-session binding. The relay must accept only
`aud=parallellines-openwebui`, never legacy or New API login access tokens.

Preserve existing issuer signing keys, cookie keys, database, and shared service secrets.
Give each new relying party its own secret. The example files are placeholders,
not safe runtime secrets. Keep secret files mode 0600 and containing directories
0700; signing JWKS must be a regular non-symlink file and readable by the bridge's
unprivileged process. The bridge Dockerfile uses the `node` user.

Open WebUI uses persistent configuration. For an existing database, review/update
`WEBUI_URL`, login form, signup, and default role through its authenticated
administrator settings; setting environment variables alone may leave old DB
values in effect. Preserve unrelated settings. Never merge accounts by email.
Bind an administrator only after validating their stable main-site subject and
existing admin role. Keep a recovery administrator until handover is verified.

The Main bridge protocol still uses internal `client_id=parallellines-chat`;
that authority field is not a registered OIDC RP and must not be renamed as part
of old-client cleanup. See [legacy retirement](legacy-retirement.md) for the
standalone issuer deployment and archived data boundary.

## Native per-user model connection

Install and enable the separately reviewed ParallelLines-Api OAuth relay before
enabling model access. Use the stock Open WebUI administrator connection settings
or its authenticated `/openai/config` and `/openai/config/update` API. Read the
existing configuration, preserve unrelated connections, and merge the example
in [deploy/openai-system-oauth.example.json](../deploy/openai-system-oauth.example.json)
at the matching connection index. The example leaves model access disabled;
switch `ENABLE_OPENAI_API` to `true` only when the adapter is ready. An existing
database also needs this native settings update; environment values alone do not
replace persisted connection settings.

Use base URL `https://api.pingxingxian.space/oauth-relay/v1`, authentication type
`system_oauth`, an empty API key, no custom Authorization/user headers, and no
cookie forwarding. Keep `ENABLE_BASE_MODELS_CACHE=false`. The standard
`system_oauth` path selects the current user's stored OAuth session and refreshes
it natively; cookie-less tasks use that user's session lookup. Do not configure a
shared fallback key. If there is no valid OAuth token, the adapter must reject
the request. A local recovery-admin login alone supplies no Main OAuth token.
New API may require one main-account login to create the verified subject mapping
before that user can use the relay.

Model listing and text/image-content chat, responses, and title tasks must use
the same connection and user identity. Do not put title/background models on a
second shared-key connection. Image content in chat is distinct from image
generation endpoints; enable only protocols the adapter implements and verifies.
Keep the default role `user`, role/group mapping disabled, and the existing
recovery administrator. This change does not promote accounts or link by email.

Pinned upstream behavior was checked in Open WebUI v0.12.5:
[native authorization and refresh](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/utils/oauth.py),
[system OAuth request headers](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/utils/headers.py),
and [connection configuration](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/routers/openai.py).
Its native refresh window is five minutes, matching this bridge's maximum access
token lifetime; model requests can therefore refresh frequently. Retain native
refresh locking, one Chat worker unless shared locking is configured, and the
existing short lifetime. Real browser/model acceptance is separate from the
local mock-authority protocol tests.

## Native Responses for the three approved models

The shared trusted OAuth connection uses `api_type: responses` for
`gpt-6-luna`, `gpt-6.1-terra`, and `gpt-6.1-sol`. Keep its base URL ending in
`/oauth-relay/v1`; Open WebUI appends `/responses` itself. Do not append the
endpoint to the configured base URL or enable New API's Chat-to-Responses
conversion, which is incompatible with this installation's standard billing.

For an existing enabled connection, `configure-model-access.mjs` accepts
`mode: responses-only` with the existing administrator bearer on stdin. That
mode changes only the connection protocol and reads it back, without reapplying
model access, task, image, cache or default-model settings. It never enables a
disabled connection or calls a model.

The pinned native converter also needs the narrow
`openwebui-profile-overlay/apply_responses.py` overlay. It preserves reasoning
levels, maps legacy `reasoning_effort` to `reasoning.effort`, and omits sampling
parameters unsupported while reasoning is enabled. See
[Responses release and recovery](responses-release.md) for the image and scoped
server-maintenance path when no administrator credential is available.

## Homepage performance and internal routing

For the scoped static CDN policy, on-demand formatted-copy module, weak-network
session restoration and optional same-host HTTPS routing, see
[Chat performance](chat-performance.md). These changes do not cache login/API
responses, weaken TLS/identity checks or change user/model billing. Internal
host mappings are optional and must not be applied to standalone hosts without
the existing trusted Nginx on that host.

## Release and deploy

For the reviewed `sso-recovery-20261030` to native-refresh transition, use
`python3 deploy/deploy-identity-refresh.py /data/.../scope.json` on the deployment
host with PyYAML installed. Prepare the nonsecret scope from
[identity-refresh-scope.example.json](../deploy/identity-refresh-scope.example.json),
using the actual existing paths/projects/containers. `bridge_env_files` must keep
all three existing files in order, including the session-release file;
`candidate_env_files` may be empty when no Compose interpolation file is used.
The script never reads their contents or creates credentials.

If the known main-repository checkout reverted the two host bridge modules while
the approved recovery image is still running, the optional
`restore_from_verified_runtime=true` handles only the two explicitly recorded
stale hashes. It verifies complete source bytes from the running approved image,
backs up stale files, then restores the verified files in place without restarting
a service. An independent receipt records that repair before ordinary deployment
continues. Unknown drift, changed runtime contents, or Compose drift still stops
the release. Keep this option false for normal deployments.

For the separately verified Compose checkout reset, also provide the optional
`restore_compose_backup` path to the existing pre-recovery Compose backup. That
backup must identify the `sso-clients-20261030` image and its one existing
`/data/.../bridge-clients.env` file with mode 0600. The script derives the known
running Compose by changing only its image to `sso-recovery-20261030`. The current
disk Compose must match it semantically except for the exact reviewed release
interpolation and missing env-file entry. Any other difference stops before
reconciliation writes. Source and Compose repair share one backup/rollback
transaction; env-file contents are never read.

The script locks old/new source hashes, checks current images, builds from the
existing bridge image with only two replacement modules, and backs up the live
modules and both changed Compose files. It updates files in place to preserve
bind-mounted inodes. Only the bridge and Chat services are recreated; the New API
service block is preserved. Failure restores source/config and verifies health
and old images. If any restore fails, no further recreation is attempted; the
restricted failure receipt points to the backup for operator recovery. Existing
image tags and backups are retained. This script does not enable model access,
modify native model settings, edit Nginx, or perform browser/model acceptance.

Local script regression tests use mocked subprocess calls only:
`python3 -m unittest discover -s deploy -p test_deploy_identity_refresh.py`.
POSIX permission enforcement requires a Linux deployment host.

1. Commit reviewed source, tests, image lock, and templates. Record the commit and
   built image digest in private deployment records; use Git as release input.
2. Back up the specific Chat and issuer databases consistently by stopping only
   their owning containers or using supported SQLite backup. Keep backup data and
   credentials out of Git. Do not stop unrelated services or prune volumes.
3. Use the current container project/name, data directories, and secret paths in
   an external interpolation file. Confirm `/data` is mounted and has free space.
   Build the identity image from this commit; pull the exact locked Chat image.
4. Replace only the target Chat/identity service definitions in their owning
   deployment. Validate with `docker compose ... config --quiet`; recreate only
   those services. The bridge must remain one writer to its own SQLite DB.
5. Check container health, OIDC discovery, the public login button, and that a
   normal account has no admin controls. The user can validate actual login.
   No completion, title, image, or other billable request is required for this step.

## Reverse proxy and certificates

The vhost file is additive. Nginx must join the explicit edge network while
retaining its existing networks and default gateway. The upstream alias must
resolve to the intended container. Keep the original raw service ports bound to
loopback. Certificates must exist before loading the TLS block. Use the existing
ACME account/webroot; leave unrelated domains, certificates, and Cloudflare TLS
settings unchanged. Validate `nginx -t` before reloading.

For a single-file Nginx bind mount, edit the host file in place (with a backup)
so its inode remains mounted; replacing the host file by rename can leave Nginx
reading the old inode. Do not overwrite other vhosts. Confirm both the new host
and existing hosts after reload. A renewal timer alone does not prove Nginx reload
after renewal: verify the renewal deploy hook separately.

## Rollback

Record previous image references and nonsecret service configuration before
upgrade. If startup or login fails, restore those exact service definitions and
images, retaining current secret files and data mounts. An application upgrade
may migrate its DB: restoring an older image alone is not necessarily safe. Use
the matching pre-upgrade DB backup if the upstream migration is not reversible;
this discards post-backup writes and requires an explicit recovery decision.

Do not delete the old stack until the new route, login, model/identity billing,
and data plan have separately passed acceptance. Deletion of historical chats,
uploads, or unique volumes is outside this repository's deploy procedure.
