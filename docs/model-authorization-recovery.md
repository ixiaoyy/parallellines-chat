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
For the trusted connection, other HTTP failures retain their status with a safe
fixed error message; network or malformed-response failures return 503 instead
of an apparently successful empty directory. Other providers keep native behavior.

The model-list client recognizes only that exact status and code. The existing
toast component offers a fixed, relative `/oauth/oidc/login` action. Navigation
requires a click; no user is automatically logged out or redirected. The toast
uses one ID and is dismissed on successful model discovery. All other API errors
and account identity rules retain the upstream behavior.

## Silent recovery layer

The new `frontend-silent-recovery` layer keeps the Chat page in place. Only the
exact authorization-required response starts a single background recovery. It
uses a hidden, sandboxed iframe for the existing Main confirmation and native
OIDC flow; it does not remove consent, extend a grant, trust an old issuer cookie
instead of Main, or add cross-origin access to Main credentials. Network errors,
iframe restrictions, and timeouts do not prove that Main login has expired.

An authenticated same-origin preparation endpoint binds the existing Chat user
to native Authlib state. The callback rechecks the still-valid Chat login and
verified subject before saving only that user's native OAuth session. It does
not create a user, synchronize roles, sign a new Chat JWT, or enter the ordinary
`/auth` frontend. Its fixed-origin message contains only a result and correlation
ID; the parent checks origin, iframe source, and that ID.

Recovery transactions use the separate native Authlib `pl_model_oidc` state
namespace on a request-local client copy. The global ordinary-login client is
unchanged. This is also a rollback boundary: an older ordinary-login callback
cannot consume a new recovery transaction and turn a hidden callback into an
ordinary sign-in. No recovery state is copied into the legacy namespace.

Recovery must not extend the existing 12-second page-startup contract. Successful
background recovery reloads only the model list; neither successful recovery nor
a retry resends a conversation. Keeping the parent page in place preserves its
current conversation, temporary chat, text, and in-memory attachments. Failed
recovery retains a manual action, while account mismatch requires an explicit
account correction rather than automatically switching Chat users.

Prepare this layer only after the reviewed manual-recovery source, then build
with `node deploy/build-performance-frontend.mjs PREPARED_SOURCE NEW_OUTPUT silent-recovery`.
Its separate manifest preserves the published manual layer and records all four
feature stages. `deploy/deploy-silent-model-recovery.py` and
`Dockerfile.silent-recovery` build on the exact current image, retaining the
previous patches and scoped rollback. They do not modify Main, the identity
bridge, gateway, native defaults, model prices, or shared security headers.

The scoped `silent-recovery-20261112` image was deployed on 2026-11-12 after
authorization; health, source readback, and the complete public-file inventory
passed. Real-browser silent SSO acceptance remains open because the Chrome
automation attempt timed out. Mocked state, identity, message, cancellation, and timeout tests do
not establish real browser storage/cookie availability or successful live SSO.
Do not weaken CSP, X-Frame-Options, CORS, or the Main finish-Origin check to make
an iframe test pass.

## Published manual-recovery baseline: incremental build

An installation already carrying Responses, free-model isolation, and frontend
performance/bootstrap patches must **not** use `Dockerfile.recovery` or the old
recovery deployer: those start from the stock image and would discard installed
changes. Keep the original build procedure below only for its original baseline.

For the reviewed current baseline:

1. Prepare the exact released frontend source, preserving its performance and
   bootstrap changes, then apply `frontend-auth-recovery/apply.py`. The combined
   `frontend-auth-recovery/manifest.json` records the accepted input/output and
   the order `frontend-performance → frontend-bootstrap → frontend-auth-recovery`.
   Reuse only an installation matching the existing package lock.
2. Build once with
   `node deploy/build-performance-frontend.mjs PREPARED_SOURCE NEW_OUTPUT auth-recovery`.
   The existing packaging keeps private source maps separate and preserves Pyodide.
3. Use `deploy/deploy-current-chat-recovery.py` with a newly read current scope.
   It first builds the verified public frontend on the exact current image,
   then applies only `routers/openai.py` and `utils/main_model_auth.py` using
   `Dockerfile.current-recovery`. No default stock image is permitted.
4. Before activation, verify the live image, reviewed source baseline, current
   Compose service and health. The inherited service-level compare-and-swap,
   private backup, readiness and rollback remain mandatory. Native settings,
   identity lifetime, channel configuration and billing are not changed.

This patch explains an expired upstream authorization and gives a recovery path;
it does not extend an expired grant or change the existing OAuth refresh timeout.
Page/network performance and a real authenticated conversation remain separate
acceptance checks. Refreshing the browser is required to load the new frontend.

## Original stock-baseline build

1. Fetch the exact upstream commit from `upstream.lock.json` into an ignored build
   directory. Preserve its lockfile and licenses.
2. Apply `openwebui-profile-overlay/frontend-auth-recovery/apply.py` to that source.
   It checks the source SHA and refuses drift or duplicate application.
3. Install the locked frontend dependencies using the persistent download cache
   (`npm ci --force --ignore-scripts --prefer-offline --cache "$HOME/.cache/parallellines-chat/npm" --no-audit --no-fund`)
   and run `node deploy/build-recovery-frontend.mjs /path/to/prepared/source`
   from this repository. It checks the lock and patched API hashes, runs the
   upstream crypto check, and compiles with Vite using the original version.
   This incremental image retains the official image's
   unchanged Pyodide assets; it does not re-download Python packages or replace
   those runtime assets. Record the exact build commands and archive hashes.
4. The builder uses hidden source maps and splits the generated `build/`, except
   its unchanged `pyodide/` subtree, into ignored public `frontend-build/` and
   private `frontend-debug/` under `openwebui-profile-overlay/`.
   It generates `frontend-build-manifest.json` with the fixed upstream commit and a
   `files` mapping of every public relative path to its SHA256. The private
   `source-map-manifest.json` binds the map hashes to that public manifest's digest.
   Keep private maps for debugging; do not upload them or expose them as static files.
   The overlay's `.dockerignore` excludes them from the build context, and the
   image removes inherited compiled maps without removing old hashed assets or Pyodide.
   Both output types refuse to overwrite existing artifacts. The upstream source's
   baseline Pyodide lock must not overwrite the official image's complete runtime
   lock. Build products are release
   artifacts, not handwritten application source.
5. Build `Dockerfile.recovery`. Both backend patches check the official source
   hash. The image contains the existing profile patch and the new recovery
   patch. No runtime secrets enter the Docker build context.

## Release and rollback

The original `deploy/deploy-model-auth-recovery.py` reuses the existing scoped release helper:
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
