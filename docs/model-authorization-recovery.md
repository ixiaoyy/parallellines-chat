# Model authorization recovery

An authenticated Chat user can have a valid Chat login but no usable upstream
OAuth session. Official v0.12.5 then sends the model-list request without a bearer
token and folds the upstream error into an empty model list. This patch keeps
that failure visible and offers the existing native login flow.

The backend change is confined to `system_oauth` discovery at the exact trusted
`https://api.pingxingxian.space/oauth-relay/v1/models` endpoint. Missing credentials
stop before the network request. That relay's HTTP 401 is mapped to the same
HTTP 403 `main_oauth_authorization_required` detail. The ordinary Chat login is
still valid. Responses do not include token, cookie or upstream error contents.
Relay binding, quota and service errors do not initiate a reauthorization flow.

The model-list client recognizes only that exact status and code. The existing
toast component offers a fixed, relative `/oauth/oidc/login` action. Navigation
requires a click; no user is automatically logged out or redirected. The toast
uses one ID and is dismissed on successful model discovery. All other API errors
and account identity rules retain the upstream behavior.

## Build

1. Fetch the exact upstream commit from `upstream.lock.json` into an ignored build
   directory. Preserve its lockfile and licenses.
2. Apply `openwebui-profile-overlay/frontend-auth-recovery/apply.py` to that source.
   It checks the source SHA and refuses drift or duplicate application.
3. Install the locked frontend dependencies (`npm ci --force --ignore-scripts`)
   and run `node deploy/build-recovery-frontend.mjs /path/to/prepared/source`
   from this repository. It checks the lock and patched API hashes, runs the
   upstream crypto check, and compiles with Vite using the original version.
   This incremental image retains the official image's
   unchanged Pyodide assets; it does not re-download Python packages or replace
   those runtime assets. Record the exact build commands and archive hashes.
4. The builder copies the generated `build/` except its `pyodide/` subtree to ignored
   `openwebui-profile-overlay/frontend-build/`. Generate
   `frontend-build-manifest.json` with the fixed upstream commit and a `files`
   mapping of every included relative path to its SHA256. The upstream source's
   baseline Pyodide lock must not overwrite the official image's complete runtime
   lock. Build products are release
   artifacts, not handwritten application source.
5. Build `Dockerfile.recovery`. Both backend patches check the official source
   hash. The image contains the existing profile patch and the new recovery
   patch. No runtime secrets enter the Docker build context.

## Release and rollback

`deploy/deploy-model-auth-recovery.py` reuses the existing scoped release helper:
runtime image/source preconditions, target-service compare-and-swap, private
Compose backup, no-dependency Chat recreation, readiness, source readback and
rollback on activation failure. Its scope uses only the current Chat service;
main-site API, issuer, New API, databases, balances and credentials are unchanged.
The helper verifies the complete frontend manifest before building the image.

Keep the prior image and private Compose backup. Rollback changes only the Chat
image/build reference; do not restore or replace a database to undo this patch.

## Validation boundary

Backend tests execute the patched upstream request function with synthetic
headers/responses. Frontend tests execute the patched `getModels` body with mocked
fetch and toast. These verify missing authorization, explicit recovery, no
automatic navigation and unchanged unrelated errors without model charges.

Public health and static-asset checks verify release delivery. A real user's
native OAuth callback is still required to create their upstream session, and
only an authenticated free model-list request can verify their relay access.
Static assets or configuration readback alone do not prove browser model
visibility. No paid generation is required for these checks.
