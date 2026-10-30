# Existing-account profile conflict patch

The base is official Open WebUI v0.12.5, commit
`f7ac7ee10962ddec0017f5c29b6740d9b9c5faca`, pinned by image digest in the Dockerfile.
`apply.py` refuses a different OAuth source hash or Users transaction implementation.
No frontend or branding files change.

With `MAIN_OAUTH_PROFILE_SYNC_ENABLED=true`, only the `oidc` callback for an account
already found by its stable provider/subject uses the helper. It validates the
verified email and checks conflicts before writing the name and email together
through the pinned native Users method. That method also updates Auth.email in the
same commit. No role, password, quota, OAuth binding, account merge or new-account
policy is added. Other providers retain their native code path.

Conflicts and write failures become a generic HTTPException(409); the upstream
callback renders that safe message via its existing `/auth?error=` redirect. The
browser receives the standard login error page, not an HTTP 409 response body.
Database errors and email values are not logged by the new helper.
If saved runtime settings enable email merging or OAuth role/group management,
the selected Main callback fails before those actions instead of changing identity
or permissions. The release must still verify the intended native settings.

Chat's visible name follows `preferred_username`. Open WebUI's optional custom
handle field is unchanged; it is not an identity or lookup key. Existing sessions
must sign out and sign in through Main to apply profile updates. Token refresh
does not itself update the local profile.

Build from this directory with `docker build --pull=false -t <reviewed-image> .`.
Apply `../deploy/profile-sync-chat.overlay.yaml` only to the existing Chat service.
Keep the existing data directory, web secret, OAuth secret, token encryption key,
connections and native roles. Before rollout confirm the native runtime OAuth
configuration reads the named scopes/flags; a saved config may override env.

Offline tests: `python test_profile_sync.py`. They need FastAPI and SQLAlchemy and
the three official source files in `.build/openwebui-profile-baseline` fetched
from the pinned commit. Tests exercise async adapter contracts and source
transaction shape; no live server, billing or browser login is claimed.
