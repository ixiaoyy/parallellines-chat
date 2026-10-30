# ParallelLines Chat

Deployment source for the official Open WebUI UI and the separate ParallelLines
OIDC identity bridge. Open WebUI UI and branding are unchanged. The optional
profile overlay makes existing-account name/email updates fail clearly on
conflicts and commit together; it does not change account identity or roles.

The pinned application is **v0.12.5**, commit
`f7ac7ee10962ddec0017f5c29b6740d9b9c5faca`; the exact official Linux amd64 image is
recorded in `upstream.lock.json`. Read [NOTICE.md](NOTICE.md) for component terms.

## Scope

- `compose.yaml`: official Chat image, loopback-only port, data-disk bind mount,
  and native OIDC settings.
- `identity-bridge/`: separately built main-site OIDC provider and tests. It is
  shared by Chat, New API, and the existing legacy chat client.
- `compose.bridge.yaml`: integration reference for that existing issuer service.
- `deploy/nginx-chat.conf`: additive vhost, including WebSocket and SSE support.
- `docs/OPERATIONS.md`: account configuration, release, upgrade, and rollback.
- `main-profile-overlay/`, `openwebui-profile-overlay/`: reviewed, hash-checked
  profile synchronization patches. See [release contract](docs/profile-sync-release.md).

Main-site credentials are entered only at the main site. Users select the
**平行线账号** login button in Chat. Ordinary users default to `user`; administrator
binding requires verification of the stable main-site subject and existing role.
Email matching and automatic admin-role claims are disabled.

**Model access stays disabled until the separate New API OAuth relay is ready.**
Chat now requests native offline access, with rotating refresh tokens bounded by
the Main session. The native `system_oauth` connection example sends each user's
access token to the trusted relay; it contains no shared API key. See
[operations](docs/OPERATIONS.md#native-per-user-model-connection) for configuration,
fresh-login requirements, and acceptance. The bridge alone does not establish
per-user model billing.

## Prepare a release

Keep filled environment files, signing keys, runtime databases, and logs outside
the checkout under restricted server-only `/data` paths. Copy the example values
into those files only after choosing the deployment scope. Never run Compose
`config` without `--quiet` in shared logs because it can expand env-file secrets.

```sh
cd identity-bridge
npm ci --ignore-scripts
npm test
cd ..
docker compose --env-file /data/parallellines/chat/deploy.env config --quiet
```

These commands validate/build locally. Deployment is a separate controlled step;
do not run both an existing and replacement issuer against one SQLite database.

## Current limits

The deployment templates are normalized for separate repositories. Existing
server container names, paths, and project names must be preserved in the
server-only interpolation file during adoption. Do not run the examples with
new defaults over an existing service and assume data will follow automatically.
These sources do not switch the main site's `/chat` route or delete LibreChat.
