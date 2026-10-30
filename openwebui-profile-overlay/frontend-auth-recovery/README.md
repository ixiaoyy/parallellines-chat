# Missing model authorization recovery

This is a source patch for Open WebUI v0.12.5, commit
`f7ac7ee10962ddec0017f5c29b6740d9b9c5faca`. It does not patch generated JavaScript.
The backend contract is HTTP **403** with
`detail.code = "main_oauth_authorization_required"`. Other errors keep their
original behavior. The original error object is still rejected by `getModels`.

Only the shared `src/lib/apis/index.ts` model-list client and one small `.mjs`
module change. The existing `svelte-sonner` toast displays a fixed safe Chinese
message and an explicit **重新授权** action. Its stable ID prevents stacked copies;
successful model loading dismisses that ID. The action opens the existing
`/oauth/oidc/login` route. Backend-provided text and URLs are never rendered or
followed. There is no automatic navigation, token deletion, session mutation, or
new dependency. No custom login page or component is introduced.

## Apply and verify

`apply.py` verifies the exact SHA256 of the original API source and refuses
unexpected source, an existing module, or a repeated application:

```sh
python apply.py /path/to/pinned-open-webui --check
python apply.py /path/to/pinned-open-webui
OPENWEBUI_ORIGINAL_INDEX=/path/to/original/src/lib/apis/index.ts python -m unittest test_apply.py -v
OPENWEBUI_PATCHED_INDEX=/path/to/pinned-open-webui/src/lib/apis/index.ts node --test test_auth_recovery.mjs
```

The tests require Python's standard library and Node 22.13+ (tested on 22.14.0),
with no package installation. JavaScript tests strip types from and execute the
actual patched upstream `getModels` body in an isolated VM with mocked fetch and
toast; no network, real JWT, or model call is used. The pure helper tests also
cover an attacker-controlled recovery URL, exact error matching, explicit-click
navigation, same-ID deduplication, and rendering failures.

## Build boundary

The existing Python-only profile overlay is not sufficient to activate this
frontend change. Build the full frontend from the fixed commit and its existing
`package-lock.json`, then copy its complete `build/` output into the Chat image,
preserving the profile/backend overlays. Do not edit hashed bundle files by hand.
The upstream Dockerfile uses Node 22, `npm ci --force`, and `npm run build`.
`svelte-sonner` 0.3.28 is already locked and supports the action and fixed-ID update.

The upstream build includes crypto verification, Pyodide resource fetching and
Vite; its Node heap limit is 12 GiB, not a measured memory requirement. Pyodide
fetches include external CDN/PyPI resources. `USE_SLIM=true` changes supplied
resources and must not be silently introduced just to shorten this build.
This patch preparation does not perform a frontend build or deployment.
