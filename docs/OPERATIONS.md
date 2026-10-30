# Operations

## Identity configuration

The existing main site must provide `/auth/chat`, its authenticated identity
exchange/session API, and the `/auth/oidc` proxy route. This repository does not
replace the main-site application. The bridge retains that service protocol and
registers three exact callbacks; separate client secrets are required:

| Client | Callback | Refresh |
| --- | --- | --- |
| `parallellines-chat` | `https://pingxingxian.space/chat/oauth/openid/callback` | enabled |
| `parallellines-openwebui` | `https://chat.pingxingxian.space/oauth/oidc/login/callback` | disabled |
| `parallellines-newapi` | `https://api.pingxingxian.space/oauth/pingxingxian` | disabled |

All clients use `client_secret_basic` and PKCE S256. Do not weaken callback,
state, subject, or client binding checks to work around a failed login. The API
client needs the opt-in PKCE patch from ParallelLines-Api. This is OAuth/UserInfo
for New API; it does not claim New API validates ID-token nonces.

Preserve existing issuer signing keys, cookie keys, database, and legacy secrets.
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

## Release and deploy

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
