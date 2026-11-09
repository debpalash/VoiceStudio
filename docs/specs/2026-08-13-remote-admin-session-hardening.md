# Remote Admin Session Hardening

**Status:** Implemented in the working tree; pending review and Linux CI

**Date:** 2026-08-13

**Priority:** High

**Scope:** FastAPI authentication, browser/Tauri credential handling, HTTP and WebSocket authorization

**Out of scope:** Multi-user accounts, cloud identity providers, changing the existing PIN/trusted-network policy

## Executive decision

The first-party UI must stop treating `OMNIVOICE_API_KEY` as a browser session.
The API key is a durable operator secret with access to the server-mode admin
surface; it must not be copied into browser storage, cookies, WebSocket URLs,
response headers, or logs.

Implement a one-time exchange from the master API key to a short-lived,
process-bound admin session:

- Direct API clients keep using `Authorization: Bearer <OMNIVOICE_API_KEY>`.
- The browser/Tauri UI presents the master key once to create an admin session.
- Same-origin browsers receive an `HttpOnly` session cookie.
- Cross-origin/Electron clients retain only the backend-bound, short-lived
  session in `localStorage` so reloads and new tabs remain authenticated.
- Cross-origin WebSockets use a path-bound, single-use ticket with a 30-second
  lifetime. The master key and admin session token never enter a URL.
- Session authorization grants consumption and admin capabilities, but never
  native host-path access.
- Restarting the backend or rotating/removing the API key invalidates every
  outstanding session.

This is a credential-lifecycle hardening change. It must not alter the current
loopback, server-mode bootstrap, PIN, trusted-network, or direct Bearer-client
contracts.

## Verified current state

The following behavior exists on the current `fix/server-mode-admin-auth`
branch:

| Surface                      | Current behavior                                                   | Consequence                                                              |
| ---------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| `frontend/src/api/client.ts` | Reads and writes `ov_api_key` in `localStorage`                    | A same-origin script can extract a durable admin secret                  |
| `wsUrl()`                    | Appends `?api_key=<master>` to every generated WebSocket URL       | The durable secret can reach proxy/access logs and browser tooling       |
| Deep-link bootstrap          | Reads `#api_key=...`, scrubs the fragment, then persists the value | A transient bootstrap secret becomes durable browser state               |
| `RemoteAuthGate`             | Calls `saveApiKey()` and reloads                                   | The login form creates permanent JS-readable state                       |
| `RemoteBackendPanel`         | Initializes and saves the raw key through local storage            | Remote-backend configuration retains the master secret indefinitely      |
| `BearerKeyMiddleware`        | Reflects a valid presented key into `ov_key=<master>`              | The backend copies the master into a non-`HttpOnly`, non-expiring cookie |
| Middleware and dependencies  | Parse and compare credentials independently                        | Authentication precedence and authorization can drift                    |

Runtime reproduction with a non-loopback `TestClient` request and
`OMNIVOICE_API_KEY=s3cret-key`:

```text
GET /v1/audio/voices
Authorization: Bearer s3cret-key

HTTP 200
Set-Cookie: ov_key=s3cret-key; Path=/; SameSite=Lax
```

The client cookie jar then contains the literal master key.

The current access-control baseline is green:

```text
Backend:  99 passed
Frontend: 28 passed
```

The backend baseline covered:

```text
tests/test_bearer_middleware.py
tests/test_loopback_server_mode.py
tests/test_admin_route_policy.py
tests/test_mcp_bindings.py
```

The frontend baseline covered:

```text
frontend/src/api/client.test.ts
frontend/src/components/RemoteAuthGate.test.jsx
frontend/src/components/settings/RemoteBackendPanel.test.jsx
```

Passing baseline tests prove that the current policy works. They do not prove
that the master secret has a safe lifecycle; there are no assertions covering
secret reflection, cookie hardening, expiry, revocation, or first-party URL and
storage leakage.

## Threat model

### Assets

- The operator's `OMNIVOICE_API_KEY`.
- The server-mode admin surface, including RCE/filesystem-capable operations.
- Native-only host-path capabilities.
- Remote HTTP and WebSocket sessions.

### Defended attack paths

- Exfiltration of the master key by same-origin XSS or a compromised frontend
  dependency.
- Durable replay after a browser session ends.
- Disclosure through WebSocket URLs, proxy logs, browser history, diagnostics,
  response headers, or exception messages.
- CSRF and cross-site WebSocket hijacking when browser cookies authenticate the
  request.
- Privilege escalation from PIN, trusted-network, or admin-session credentials
  into native host-path operations.
- Reuse of a session after expiry, logout, backend restart, or API-key rotation.

### Explicit limitations

- `HttpOnly` does not stop active XSS from making same-origin requests while the
  page is compromised. It prevents extraction and later/lateral replay of the
  durable master credential.
- This change does not make plaintext HTTP confidential. Remote operators must
  still use TLS or a secure overlay such as Tailscale.
- This is not an account system and does not introduce users, passwords,
  refresh tokens, OAuth, or cloud dependencies.
- Durable, unattended Tauri reconnect is not implemented. That requires a
  separate cross-platform secure-storage decision; it must never fall back to
  `localStorage`.

## Non-negotiable security invariants

Use these identifiers in tests and review comments.

1. **AUTH-S1 — No master persistence:** First-party production code never writes
   the master API key to `localStorage`, `sessionStorage`, IndexedDB, a cookie,
   or a persisted application setting.
2. **AUTH-S2 — No master reflection:** No response body, response header,
   exception, or log record contains the presented master API key.
3. **AUTH-S3 — No durable credential in URLs:** Neither the master API key nor
   an admin session token appears in HTTP or WebSocket URLs generated by the UI.
4. **AUTH-S4 — Header-only issuance:** Normal session issuance accepts the
   master key only through `Authorization: Bearer`. Query parameters and an
   existing admin session cannot mint or renew sessions.
5. **AUTH-S5 — Opaque random sessions:** Session tokens contain at least 256 bits
   from `secrets`; the store retains only HMAC-SHA-256 indexes keyed by a
   process-local 256-bit pepper.
6. **AUTH-S6 — Absolute expiry:** Admin sessions expire after eight hours. There
   is no sliding expiry, refresh token, or implicit renewal.
7. **AUTH-S7 — Immediate invalidation:** Logout, backend restart, and API-key
   rotation/removal invalidate sessions before the next protected operation.
8. **AUTH-S8 — Capability ceiling:** API-key and session principals have
   `consume + admin`, never `native`. Only genuine loopback has `native`.
9. **AUTH-S9 — Cookie hardening:** Cookie sessions use `HttpOnly`, `Path=/`,
   `SameSite=Strict`, an exact `Max-Age`, and `Secure` when the effective scheme
   is HTTPS. No `Domain` attribute is set.
10. **AUTH-S10 — CSRF enforcement:** Cookie-authenticated unsafe HTTP methods
    require an exact allowed `Origin` and the first-party CSRF marker header.
    Cookie-authenticated side-effectful GET routes require the marker too;
    cookie-authenticated WebSockets require exact origin validation. `null` and
    wildcard origins fail closed.
11. **AUTH-S11 — One-use WebSocket tickets:** A ticket is scoped to one normalized
    WebSocket path, expires after 30 seconds, and succeeds at most once under
    concurrent redemption.
12. **AUTH-S12 — Deterministic precedence:** Credential extraction has one
    canonical implementation and an explicitly tested precedence order.
13. **AUTH-S13 — Compatibility:** Loopback defaults, PIN consumption, trusted
    networks, server-mode read-only bootstrap, direct Bearer clients, and native
    path restrictions retain their existing behavior.

## Authorization model

Authentication identifies a principal. Dependencies authorize capabilities.
Do not infer admin access from HTTP method alone and do not treat network
location as an API key.

| Principal                       |                             Consumption |                    Admin | Native |           May mint admin session |
| ------------------------------- | --------------------------------------: | -----------------------: | -----: | -------------------------------: |
| Genuine loopback                |                                     Yes |                      Yes |    Yes | No; master key is still required |
| Trusted network                 |                                     Yes |                       No |     No |                               No |
| Share PIN                       |                                     Yes |                       No |     No |                               No |
| Master API key                  |                                     Yes |                      Yes |     No |                              Yes |
| Admin session                   |                                     Yes |                      Yes |     No |                               No |
| Server-mode anonymous bootstrap | Existing consumption/read-only behavior | Read-only exception only |     No |                               No |

`ADMIN` is a credential capability, not permission to bypass deployment mode.
Desktop admin routes remain genuine-loopback-only; remote `API_KEY` and
`ADMIN_SESSION` principals can exercise `ADMIN` only under server mode.

The existing `require_admin`, `require_admin_action`, and
`require_native_access` dependencies remain explicit route-level boundaries.
They consume the canonical principal instead of reparsing credentials.

## Target architecture

### New backend modules

```text
backend/core/auth.py
backend/services/admin_sessions.py
backend/api/routers/auth.py
```

`backend/core/auth.py` owns transport-independent types:

```text
Capability: CONSUME | ADMIN | NATIVE
PrincipalKind: ANONYMOUS | LOOPBACK | TRUSTED_NETWORK | PIN | API_KEY | ADMIN_SESSION
AuthPrincipal(kind, capabilities, credential_id, transport)
CredentialTransport: NONE | HEADER | QUERY | COOKIE | WS_TICKET
```

The principal contains no reusable secret. `credential_id` is a short identifier
derived from the credential hash and is safe for diagnostics.

`backend/services/admin_sessions.py` owns a process-local, bounded,
thread-safe `AdminSessionStore`:

```text
issue(current_api_key) -> IssuedSession
resolve(token, current_api_key) -> SessionRecord | None
revoke(token) -> bool
issue_ws_ticket(session, path) -> IssuedTicket
consume_ws_ticket(ticket, path) -> SessionRecord | None
clear() -> None
```

Design constraints:

- Use `secrets.token_bytes(32)` and URL-safe encoding.
- Give admin sessions and WebSocket tickets distinct prefixes/namespaces.
- Store hashes, capabilities, monotonic deadlines, and non-secret identifiers;
  never store raw tokens after returning them.
- Enforce expiry with an injected monotonic clock. Use wall time only to render
  cookie `Expires` metadata.
- Keep at most 256 live sessions and 512 live tickets. Purge expired entries
  before deterministic oldest-first eviction.
- Keep fixed-TTL credentials in expiry order and maintain a hash-only reverse
  ticket index per session. Routine validation must not scan every live
  credential, and every removal path must update both indexes atomically.
- Protect mutation and ticket redemption with one lock. Ticket validation and
  deletion must be atomic.
- Track the current API-key generation with domain-separated HKDF-SHA-256 using
  a random, process-local pepper. If the normalized key changes or disappears,
  clear the store before resolving or issuing another credential.
- Reject malformed or unreasonably long tokens before hashing.
- Do not persist the store. Restart invalidation is intentional, reduces
  attack lifetime, avoids a database verifier for a possibly weak operator key,
  and matches VoiceStudio's single-process backend architecture.

### Canonical authentication resolver

Add one resolver used by HTTP middleware, HTTP dependencies, and WebSocket
guards. It must:

1. Normalize the configured API key exactly once.
2. Parse the `Authorization` header exactly once.
3. Distinguish session tokens by their prefix.
4. Preserve current API-key compatibility for header, query, and legacy cookie
   transports outside session issuance.
5. Prefer a non-empty `Authorization` header, then the legacy query parameter,
   then cookies. A stale cookie must not override a valid explicit header.
6. Attach `AuthPrincipal` to ASGI `scope["state"]` so `Request`, `WebSocket`,
   middleware, and dependencies observe the same decision.
7. Return authentication failure without embedding any presented value in the
   detail, headers, logs, or `repr` output.

The existing middleware ordering must be covered by a test. A refactor is not
accepted if `NetworkAccessMiddleware`, SPA-shell exemptions, CORS preflight, or
backend-marker headers change behavior accidentally.

### HTTP contract

#### `POST /api/auth/session`

Request:

```http
Authorization: Bearer <OMNIVOICE_API_KEY>
Content-Type: application/json

{"transport":"cookie"}
```

Allowed transports:

- `cookie` — default; return `204` and set `ov_session`.
- `bearer` — return `201` with the session token and expiry for Tauri or an
  explicitly cross-origin frontend.

Requirements:

- Require the master key even from loopback.
- Reject API-key query parameters, PINs, trusted-network identity, and existing
  admin sessions.
- Return `Cache-Control: no-store` for every response, including errors.
- Never echo the master key.
- Apply exact CORS origin policy before a browser can submit `Authorization`.
- Set or return the issued session exactly once.

Legacy migration is a narrowly scoped exception: a same-origin request carrying
the old `ov_key` cookie may exchange it once. A successful migration sets
`ov_session` and expires `ov_key` with `Max-Age=0`. It never accepts `api_key`
from the query string.

#### `DELETE /api/auth/session`

- Revoke the current session and expire `ov_session`.
- Be idempotent; a missing or already-expired session still produces `204`.
- Require valid origin when cookie-authenticated.
- Return `Cache-Control: no-store`.

#### `POST /api/auth/ws-ticket`

Request:

```json
{ "path": "/ws/transcribe" }
```

- Require a valid admin session, not a master API key or PIN.
- Normalize and allow-list the WebSocket path.
- Return one opaque ticket with a 30-second expiry.
- Never return an admin session or master key.
- The WebSocket handshake consumes the ticket atomically.

### Cookie and origin policy

For `ov_session`:

```text
HttpOnly; Path=/; SameSite=Strict; Max-Age=28800
```

Add `Secure` when the effective request scheme is HTTPS. Do not trust arbitrary
forwarded scheme or host headers; use only the proxy configuration already
trusted by the application. Plain-HTTP compatibility may use a non-`Secure`
session cookie, but the documentation must state that the transport remains
sniffable.

For cookie-authenticated `POST`, `PUT`, `PATCH`, `DELETE`, and WebSocket
handshakes:

- Compare the parsed origin tuple `(scheme, host, port)` against the exact
  configured allow-list.
- Do not use suffix matching.
- Do not allow `*` with credentials.
- Reject absent or `null` origins for unsafe cookie-authenticated operations.
- Bearer-authenticated non-browser clients do not require `Origin`; their
  credential is not ambient.

In addition, first-party cookie-authenticated HTTP requests that can mutate
state must send a fixed custom header such as `X-VoiceStudio-CSRF: 1`. Requiring
the header forces a cross-origin script through CORS preflight; it is not a
substitute for the exact origin check. `require_admin_action` must require this
header even for the known side-effectful GET routes. For same-origin GET, where
browsers may omit `Origin`, require the marker plus
`Sec-Fetch-Site: same-origin`; never accept a cross-site value. This avoids
relying on `SameSite`, which is site-based rather than origin-based.

### WebSocket transport

Same-origin browser WebSockets use `ov_session`; `wsUrl()` becomes a pure URL
builder and appends no credentials.

Cross-origin/Tauri WebSockets cannot set an `Authorization` header reliably.
They must:

1. Request a one-use ticket over authenticated HTTP.
2. Connect with `?ws_ticket=<ticket>`.
3. Have the backend consume the ticket for the exact `scope["path"]` before
   accepting the socket.

A ticket may appear in access logs, but it is path-bound, single-use, and valid
for no more than 30 seconds. It cannot be exchanged for an HTTP/admin session.

Update both known first-party consumers:

```text
frontend/src/components/CaptureWidget.jsx
frontend/src/hooks/useRealtimeEvents.js
```

### Frontend credential lifecycle

Create:

```text
frontend/src/api/authSession.ts
frontend/src/api/authSession.test.ts
```

Responsibilities:

- Determine same-origin browser versus cross-origin/Tauri transport.
- Exchange an in-memory master key for a session using a direct, non-retrying
  request. Do not route the exchange through `apiFetch`; retry/reload logic must
  never replay a master credential implicitly.
- Use `credentials: "include"` for cookie sessions.
- Keep only bearer sessions in `localStorage` and clear them on logout, expiry,
  backend change, or API-key rotation response.
- Fetch WebSocket tickets for cross-origin/Tauri sessions.
- Redact credentials from all thrown errors and diagnostics.

Modify `RemoteAuthGate` so submission is asynchronous:

1. Hold the master key only in controlled component state.
2. Exchange it once.
3. Clear the input state immediately after completion.
4. Remove legacy `ov_api_key` storage before reload/retry.
5. Display a localized error without serializing the submitted value.
6. Reload only after a successful exchange and storage cleanup.

Modify deep-link handling so `#api_key=...` is scrubbed synchronously before any
await, exchanged directly from memory, and never written to storage. A failed
exchange requires the operator to enter the key again; preserving a failed
master secret is forbidden.

Modify `RemoteBackendPanel` so testing/saving a remote backend creates a session
instead of persisting its master key. A Tauri restart may require re-entry.
Adding a native keychain/Stronghold dependency is a separate decision requiring
Windows/macOS/Linux parity tests and is not hidden inside this change.

## File-level change map

### Create

```text
backend/core/auth.py
backend/services/admin_sessions.py
backend/api/routers/auth.py
tests/test_admin_sessions.py
tests/test_auth_session_api.py
tests/test_auth_secret_hygiene.py
frontend/src/api/authSession.ts
frontend/src/api/authSession.test.ts
frontend/e2e/remote-auth.spec.ts
```

### Modify

```text
backend/main.py
backend/api/dependencies.py
backend/api/routers/capture_ws.py
frontend/src/api/client.ts
frontend/src/api/client.test.ts
frontend/src/components/RemoteAuthGate.jsx
frontend/src/components/RemoteAuthGate.test.jsx
frontend/src/components/settings/RemoteBackendPanel.jsx
frontend/src/components/settings/RemoteBackendPanel.test.jsx
frontend/src/components/CaptureWidget.jsx
frontend/src/components/CaptureWidget.test.jsx
frontend/src/hooks/useRealtimeEvents.js
frontend/src/test/useRealtimeEvents.test.jsx
tests/test_bearer_middleware.py
tests/test_loopback_server_mode.py
tests/test_admin_route_policy.py
tests/test_capture_ws.py
docs/api-auth.md
docs/remote-gpu.md
CHANGELOG.md
frontend/src/i18n/locales/*.json  # only if new user-visible text is introduced
```

No dependency or version change is expected. If a frontend dependency changes,
regenerate the root `bun.lock`; do not bump `frontend/package.json` without the
owner's instruction.

## Test strategy

Tests are part of the design, not a final verification phase. Every task below
starts with a failing regression. No production implementation is accepted
without fail-before/pass-after evidence.

### Layer 1 — `AdminSessionStore` unit tests

File: `tests/test_admin_sessions.py`

Use injected token factories and fake monotonic/wall clocks. Never use `sleep()`
or wall-clock timing assertions.

Required tests:

- `test_issue_returns_a_namespaced_256_bit_token_once`
- `test_store_retains_only_the_token_hash`
- `test_resolve_returns_the_expected_capabilities`
- `test_expiry_boundary_is_closed_at_deadline`
- `test_wall_clock_rollback_does_not_extend_a_session`
- `test_logout_revokes_immediately_and_is_idempotent`
- `test_key_rotation_invalidates_all_sessions`
- `test_key_removal_invalidates_all_sessions`
- `test_process_store_starts_empty`
- `test_expired_sessions_are_purged_before_capacity_eviction`
- `test_capacity_evicts_the_oldest_live_session_deterministically`
- `test_malformed_and_oversized_tokens_fail_without_mutation`
- `test_session_and_worker_token_namespaces_do_not_overlap`
- `test_ticket_is_scoped_to_the_normalized_path`
- `test_ticket_expires_at_thirty_seconds`
- `test_ticket_cannot_be_redeemed_twice`
- `test_concurrent_ticket_redemption_has_exactly_one_winner`
- `test_concurrent_session_issuance_produces_unique_tokens`
- `test_repr_and_debug_snapshot_contain_no_raw_credentials`

Do not add a timing-based "constant-time" test; it will be flaky and will not
prove constant-time behavior. Review must verify use of `secrets.compare_digest`
where secret-derived values are compared.

### Layer 2 — principal resolver tests

Add focused tests to `tests/test_bearer_middleware.py` or a dedicated
`tests/test_auth_principal.py` if the file becomes unwieldy.

Required matrix:

| Input                                                     | Expected principal/capabilities                            |
| --------------------------------------------------------- | ---------------------------------------------------------- |
| Genuine loopback, no credential                           | `LOOPBACK`; consume/admin/native                           |
| Trusted remote network                                    | `TRUSTED_NETWORK`; consume only                            |
| Valid PIN                                                 | `PIN`; consume only                                        |
| Valid master header                                       | `API_KEY`; consume/admin                                   |
| Valid master query, no header                             | `API_KEY`; consume/admin; legacy transport                 |
| Valid legacy `ov_key`, no header/query                    | `API_KEY`; consume/admin; legacy transport                 |
| Valid cookie session                                      | `ADMIN_SESSION`; consume/admin                             |
| Valid Bearer session                                      | `ADMIN_SESSION`; consume/admin                             |
| Empty/whitespace header plus valid fallback               | Preserve the current normalized fallback contract          |
| Non-empty invalid explicit header plus valid stale cookie | Fail according to the documented authoritative-header rule |
| Expired/revoked session                                   | Authentication failure                                     |
| Session from before key rotation                          | Authentication failure                                     |

Also assert:

- HTTP dependencies and WebSocket guards receive the same principal object.
- Credential parsing occurs once per request/scope.
- No principal contains the raw credential.
- `request.state` is isolated between concurrent requests.
- CORS `OPTIONS`, health, and SPA-shell exemptions retain current behavior.

### Layer 3 — session HTTP API tests

File: `tests/test_auth_session_api.py`

Use a sentinel master such as `MASTER_DO_NOT_LEAK_7d29` and inspect the complete
response body, all response headers, cookies, captured logs, and exception text.

#### Issuance

- Correct master header + cookie transport returns `204`.
- Correct master header + bearer transport returns `201` with one session and
  expiry, never the master.
- Missing, wrong, whitespace-only, PIN, trusted-network, query, and existing
  session credentials cannot mint a session.
- Loopback without the master cannot mint a session.
- Unsupported transport and malformed JSON return `422` without issuance.
- Successful issuance sets `Cache-Control: no-store`.
- Failed issuance also sets `Cache-Control: no-store`.
- The response never sets `ov_key`.
- Multiple successful issuances produce distinct tokens.

#### Cookie attributes

Parse attributes instead of comparing one formatting-specific string:

- `HttpOnly` present.
- `SameSite=Strict` present.
- `Path=/` present.
- `Max-Age=28800` present.
- `Domain` absent.
- `Secure` present for HTTPS and absent for explicitly supported HTTP.
- Logout emits an expiry cookie with the same name/path.

#### Legacy migration

- Same-origin valid `ov_key` migrates to `ov_session` exactly once.
- Migration expires `ov_key` and never reflects its value.
- Cross-origin, missing-origin, query-key, and wrong-cookie migration fail.
- A stale `ov_key` cannot override a valid explicit session/header.

#### Revocation and rotation

- Logout makes the next HTTP request fail.
- Logout makes the next WebSocket handshake fail.
- Expiry returns `401` for HTTP and policy close `1008` for WebSocket.
- Changing or removing `OMNIVOICE_API_KEY` rejects the session on the next
  request without restarting the process.

### Layer 4 — authorization regression matrix

Extend `tests/test_loopback_server_mode.py` and
`tests/test_admin_route_policy.py`.

For representative read, write, and side-effectful GET routes across settings,
system, engines, media tools, MCP bindings and pronunciation, test:

| Context                                   |                                            Read-only admin | Write/side-effect admin | Native host-path action |
| ----------------------------------------- | ---------------------------------------------------------: | ----------------------: | ----------------------: |
| Desktop loopback                          |                                                      Allow |                   Allow |                   Allow |
| Desktop remote + master/session           |                                                       Deny |                    Deny |                    Deny |
| Server mode, no key/PIN                   |                         Allow existing read-only bootstrap |                    Deny |                    Deny |
| Server mode + valid master                |                                                      Allow |                   Allow |                    Deny |
| Server mode + valid admin session         |                                                      Allow |                   Allow |                    Deny |
| Server mode + PIN only                    |                                                       Deny |                    Deny |                    Deny |
| Server mode + trusted network, no key/PIN | Allow anonymous bootstrap only; trust grants nothing extra |                    Deny |                    Deny |
| Server mode + expired/revoked session     |                                                       Deny |                    Deny |                    Deny |

The AST policy test must continue proving that every privileged router declares
the intended dependency and that every side-effectful GET uses
`require_admin_action`. A runtime session test does not replace the static route
inventory guard.

### Layer 5 — CSRF and origin tests

Add table-driven tests covering both HTTP and WebSocket:

- Exact allowed origin succeeds.
- Wrong scheme, host, subdomain, or port fails.
- Suffix attacks such as `trusted.example.evil.test` fail.
- `Origin: null`, wildcard, malformed, duplicated, and absent origins fail for
  unsafe cookie-authenticated requests.
- Safe cookie-authenticated `GET` preserves the documented policy.
- Bearer-authenticated CLI requests without `Origin` continue to work.
- Cookie-authenticated unsafe requests without `X-VoiceStudio-CSRF: 1` fail.
- Cross-origin requests with the marker still fail origin/CORS validation.
- Side-effectful GET with marker plus `Sec-Fetch-Site: same-origin` succeeds;
  missing marker or any cross-site fetch metadata fails.
- CORS preflight does not create a session and does not emit credentials.
- Untrusted forwarded scheme/host headers cannot manufacture an allowed origin
  or force/strip the `Secure` decision.

Expected distinction:

- Authentication failure: `401` HTTP / `1008` WebSocket.
- Authenticated cookie with invalid origin: `403` HTTP / `1008` WebSocket.

### Layer 6 — WebSocket ticket tests

Backend tests in `tests/test_capture_ws.py` plus equivalent coverage for
`/ws/events`:

- A valid cookie session connects without a URL credential.
- A valid cross-origin session can mint and redeem one ticket.
- Ticket A cannot open a different path.
- Ticket A cannot be reused after a successful handshake.
- Two simultaneous redemption attempts yield exactly one accepted connection.
- Expired, malformed, revoked-session, pre-rotation, and random tickets close
  with `1008`.
- A failed handshake still consumes a presented valid one-use ticket once it
  reaches redemption.
- A ticket cannot authenticate HTTP, mint another ticket, or reach native
  actions.
- The master and session sentinels do not appear in the WebSocket URL, close
  reason, server logs, or error events.

### Layer 7 — frontend unit/component tests

#### `frontend/src/api/authSession.test.ts`

- Exchanges the master exactly once and never retries automatically.
- Uses cookie transport for same-origin browser execution.
- Uses bearer-session transport for explicit cross-origin/Tauri execution.
- Writes only the backend-bound, short-lived bearer session to `localStorage`.
- Never writes the master to any storage API.
- Clears session state on logout, expiry, backend base-URL change, and rotation
  failure.
- Requests a WebSocket ticket only when cookie transport is unavailable.
- Redacts the master/session from thrown errors and `console` calls.
- Does not include credentials in query strings or referrers.

#### `frontend/src/api/client.test.ts`

- `apiFetch` uses `credentials: "include"` for cookie mode.
- Bearer-session mode adds the session, not the master.
- `wsUrl()` is a pure credential-free URL builder for `ws:` and `wss:`.
- Existing PIN handling remains unchanged.
- Existing transport retry behavior does not retry authentication exchange.
- `401` session expiry emits one `ov:auth-required` event without a reload loop.

#### `RemoteAuthGate.test.jsx`

- API-key submission waits for exchange success before reload.
- Failure keeps the dialog visible, clears the submitted secret, and renders a
  localized generic error.
- Success removes `ov_api_key` before reload.
- The master never reaches local/session storage, event details, or console.
- Repeated submit while pending produces one request.
- PIN mode retains its existing behavior and never mints an admin session.

#### `RemoteBackendPanel.test.jsx`

- Existing `ov_api_key` is migrated and deleted.
- A new master remains only in component state during exchange.
- Switching backend URL clears the old session before probing the new backend.
- A failed probe does not persist either master or session.
- Tauri/cross-origin success stores only the expiring session.

#### WebSocket consumers

Extend `CaptureWidget.test.jsx` and `useRealtimeEvents.test.jsx`:

- Same-origin creates credential-free WebSocket URLs.
- Cross-origin waits for ticket issuance before constructing the socket.
- Ticket failure does not open an unauthenticated socket or leak credentials.
- Reconnect obtains a new ticket; it never reuses the consumed ticket.
- Existing audio/realtime reconnection and teardown behavior remains green.

### Layer 8 — static secret-hygiene guard

File: `tests/test_auth_secret_hygiene.py`

This deterministic guard should scan production frontend/backend sources and
fail if the retired first-party patterns return. Keep a narrow allow-list for
legacy parsing/migration and tests.

Required assertions:

- No production `localStorage.setItem(...ov_api_key...)`.
- No generated `api_key=${...}` or equivalent master-key query construction.
- No middleware `Set-Cookie` value derived from the presented master.
- No session token is interpolated into a WebSocket URL; only `ws_ticket` is
  allowed.
- Credential-bearing error/log templates do not interpolate raw request values.

The static guard supplements runtime sentinel tests; it does not replace them.

### Layer 9 — Playwright end-to-end tests

File: `frontend/e2e/remote-auth.spec.ts`

Run against a disposable backend with a non-loopback test client/proxy and a
known sentinel key.

Scenarios:

1. Open remote UI, enter key, load an admin screen, and connect realtime events.
2. Assert no master in local/session storage, cookies, page URL, WebSocket URL,
   request URLs, response headers, console, or page errors.
3. Assert `ov_session` is `HttpOnly` through browser context cookie inspection.
4. Reload and confirm cookie-session continuity.
5. Log out and confirm protected HTTP and WebSocket access stops immediately.
6. Advance/inject the test clock to expiry and confirm one clean re-auth prompt.
7. Exercise cross-origin mode and verify a one-use `ws_ticket`, never a master or
   admin session, appears in the WebSocket URL.
8. Attempt a hostile-origin POST and WebSocket connection; both must fail.

Do not assert secret absence by screenshot. Inspect browser context storage,
cookies, requests, WebSockets, console messages, and backend-captured logs.

### Layer 10 — compatibility and full-suite gates

Targeted backend gate while iterating:

```bash
HF_HUB_OFFLINE=1 HF_HUB_CACHE="$(mktemp -d)" \
  uv run pytest -q \
  tests/test_admin_sessions.py \
  tests/test_auth_session_api.py \
  tests/test_auth_secret_hygiene.py \
  tests/test_bearer_middleware.py \
  tests/test_loopback_server_mode.py \
  tests/test_admin_route_policy.py \
  tests/test_capture_ws.py \
  tests/test_mcp_bindings.py
```

Targeted frontend gate while iterating:

```bash
cd frontend
bun run test \
  src/api/authSession.test.ts \
  src/api/client.test.ts \
  src/components/RemoteAuthGate.test.jsx \
  src/components/settings/RemoteBackendPanel.test.jsx \
  src/components/CaptureWidget.test.jsx \
  src/test/useRealtimeEvents.test.jsx
```

Pre-review gate:

```bash
HF_HUB_OFFLINE=1 HF_HUB_CACHE="$(mktemp -d)" uv run pytest -q
cd frontend
bun run lint
bun run typecheck:ci
bun run test
bun run build
bun run e2e -- remote-auth.spec.ts
```

Run the existing deterministic repository gates, including changelog style,
locale parity, version lockstep, and hardcoded-CJK checks. Full backend tests
must use `HF_HUB_OFFLINE=1` and a genuinely empty `HF_HUB_CACHE`; a populated
developer cache is not valid evidence.

No wall-clock performance threshold belongs in cross-platform CI. Instead,
exercise 10,000 store resolutions in a diagnostic benchmark and report the
before/after request-auth overhead in the PR. Reject a design that performs
database or filesystem I/O per request or holds the store lock while calling
downstream application code.

## Test-to-invariant traceability

| Invariant | Primary evidence                                                            |
| --------- | --------------------------------------------------------------------------- |
| AUTH-S1   | Frontend storage spies, static hygiene guard, Playwright storage inspection |
| AUTH-S2   | Backend sentinel scan across headers/body/logs/exceptions                   |
| AUTH-S3   | `wsUrl` unit tests, request/WebSocket inspection, static guard              |
| AUTH-S4   | Session issuance negative credential matrix                                 |
| AUTH-S5   | Store token/hash unit tests                                                 |
| AUTH-S6   | Fake-clock boundary and no-renewal tests                                    |
| AUTH-S7   | Logout, rotation, removal, and fresh-store tests                            |
| AUTH-S8   | Route capability matrix, native denial tests                                |
| AUTH-S9   | Parsed cookie-attribute matrix under HTTP/HTTPS                             |
| AUTH-S10  | HTTP + WebSocket exact-origin matrix                                        |
| AUTH-S11  | Ticket path/expiry/concurrency/single-use tests                             |
| AUTH-S12  | Principal precedence and single-parse tests                                 |
| AUTH-S13  | Existing targeted suites plus full backend/frontend CI                      |

## Implementation order

Keep the change in one reviewed PR so the backend contract, first-party client,
and secret-hygiene regression land atomically. Use independently green commits.

### Task 1 — Lock the leak with failing tests

- Add the backend sentinel test proving the master currently appears in
  `Set-Cookie`.
- Add frontend tests proving the master currently reaches `localStorage` and
  WebSocket URLs.
- Record fail-before output in the PR description.
- Do not weaken assertions to match current behavior.

### Task 2 — Implement the bounded session/ticket store

- Write all fake-clock, hash-only, expiry, rotation, capacity, and concurrency
  tests first.
- Implement `AdminSessionStore` without FastAPI imports.
- Run Layer 1 only until green.

### Task 3 — Introduce canonical principals

- Add principal and precedence tests first.
- Refactor middleware/dependencies to consume one decision from ASGI state.
- Keep all existing authorization tests green after every edit.

### Task 4 — Add session HTTP endpoints

- Add issuance, cookie, migration, revocation, rotation, CSRF, and no-leak tests.
- Register the auth router.
- Confirm no master value is emitted before moving to frontend work.

### Task 5 — Add WebSocket tickets

- Write single-use/path/expiry/concurrency tests first.
- Integrate ticket resolution into the canonical WebSocket guard.
- Preserve direct API-key and cookie-session WebSocket compatibility.

### Task 6 — Migrate first-party frontend flows

- Implement `authSession.ts` from its failing unit tests.
- Migrate `RemoteAuthGate`, deep links, `RemoteBackendPanel`, `CaptureWidget`, and
  realtime events.
- Remove all first-party master persistence and URL generation.
- Keep the legacy parser only for one-time migration.

### Task 7 — Add static and browser-level regression coverage

- Land the source hygiene guard.
- Add Playwright remote-auth scenarios.
- Inspect actual browser storage, cookies, requests, WebSockets, console, and
  backend logs using sentinel credentials.

### Task 8 — Documentation, i18n, and full validation

- Update `docs/api-auth.md` and `docs/remote-gpu.md` with session behavior,
  transport limitations, TLS requirements, logout/expiry, and direct-client
  compatibility.
- Add concise `CHANGELOG.md` Unreleased entry.
- Translate any new visible strings in all 21 locale files.
- Run the complete offline backend suite, frontend lint/typecheck/test/build,
  Playwright scenario, and deterministic repository gates.
- Read CodeRabbit/Greptile findings and resolve every Critical/P1 before merge.
- Merge current `main` into a stale branch before trusting CI, require
  `MERGEABLE`, and watch post-merge `main` runs to green.

## Failure and rollback behavior

- Session-store failure must fail closed for remote authenticated requests. It
  must not fall back to the master cookie or silently grant loopback identity.
- A frontend exchange failure leaves the auth gate visible with a generic,
  localized error and no retained master key.
- WebSocket ticket failure does not fall back to putting the session/master in
  the URL.
- Direct Bearer clients remain the operational rollback path; no feature flag
  is required.
- Legacy `ov_key` remains accepted only for controlled one-time migration. The
  backend stops creating it immediately.

## Acceptance criteria

The change is complete only when all conditions hold:

- A sentinel master key is absent from browser durable/transient storage after
  exchange, all cookies, URLs, response data, console output, and backend logs.
- In API-key mode, the only reusable browser credential after exchange is an
  eight-hour admin session; the only URL credential is a 30-second, path-bound,
  one-use WebSocket ticket. The independent PIN flow remains unchanged.
- Same-origin HTTP and WebSocket flows work with the hardened cookie.
- Cross-origin/Tauri HTTP and WebSocket flows work without persisting the master
  or putting the admin session into a URL.
- Logout, expiry, key rotation/removal, and backend restart invalidate access.
- Admin sessions cannot reach native host-path capabilities.
- PIN and trusted networks cannot reach write/side-effect admin operations.
- Direct curl/OpenAI SDK Bearer behavior remains compatible.
- Server-mode no-credential read-only bootstrap remains compatible.
- No new required network call, platform divergence, dependency, or version
  bump is introduced.
- Targeted tests, full offline backend tests, frontend lint/typecheck/test/build,
  Playwright remote-auth coverage, CI, and post-merge `main` are green.

## Implementation and verification status — 2026-08-13

The implementation is complete in the working tree. Merge readiness still
requires the repository's normal PR review and Linux CI gates; no claim below
substitutes for CodeRabbit/Greptile review or post-merge `main` monitoring.

Delivered behavior:

- Process-local, hash-only administrator sessions: 256-bit tokens, eight-hour
  non-renewing lifetime, deterministic capacity, explicit revocation, and
  immediate invalidation on master-key rotation/removal or process restart.
- Thirty-second, path-bound, atomically single-use tickets for `/ws/events` and
  `/ws/transcribe`; the reusable session and master never enter a WebSocket URL.
- One canonical principal decision across middleware, HTTP dependencies, and
  WebSockets, including authoritative rejection of malformed or invalid
  explicit credentials instead of fall-through to ambient trust.
- Exact-origin cookie CSRF enforcement, side-effectful-GET protection, strict
  cookie attributes, `Cache-Control: no-store`, and controlled legacy migration.
- Unicode-safe constant-time credential comparisons and a bounded, per-client
  failed-exchange window that throttles brute force without locking out a
  request carrying the correct master key.
- Same-origin HttpOnly-cookie and cross-origin/Tauri bearer transports. The
  bearer contract prefers bounded `expires_in` values so independent browser
  and server clocks cannot invalidate otherwise-valid credentials.
- CORS is outside both authentication gates, so credentialless preflights and
  gate-generated `401` responses remain readable by an allowed browser origin.
- WebSocket URL construction preserves reverse-proxy base-path prefixes while
  tickets remain bound to the backend's canonical `/ws/events` or
  `/ws/transcribe` route.
- First-party removal of durable master-key persistence, master/session URL
  credentials, accidental auth forwarding to foreign absolute URLs, and
  storage-policy failures that previously could abort credential cleanup.

Verification evidence:

| Gate | Result |
|---|---|
| Session/principal/CSRF/HTTP contract + API/network/ASR compatibility | 324 passed, 1 expected xfail |
| Branch coverage for the four new backend modules | 95% total; 162 dedicated tests passed |
| Isolated `backend/tests/`, empty HF cache and offline | 254 passed |
| Prior Linux CI failure order (`mcp_bindings` → network middleware → principal) | 54 passed after runtime singleton resolution fix |
| Changelog, locale parity, version, CJK, route inventory, and install-doc gates | 243 passed |
| Full Vitest suite | 265 files, 2,084 passed |
| Review-focused auth/client regression | 6 files, 98 passed |
| Session lookup diagnostic | 14.2 μs median over seven 10,000-resolution samples; no database or filesystem I/O |
| Frontend TypeScript | clean |
| Frontend oxlint | zero errors; repository baseline warnings only |
| Production build | passed |
| Production-bundle Playwright | 4 passed in real Chrome, including same-origin cookie and cross-origin bearer credential-hygiene regressions |
| Real backend + cross-origin browser exercise | exchange `201`; protected call `200`; ticket `201`; WebSocket opened; logout `204`; subsequent access `401`; no master in storage, location, or request URLs |
| Session-store diagnostic | 10,000 resolutions in 92.886 ms (9.289 µs/op) on the verification host |

Repository-wide offline `tests/` was also executed, not sampled: 5,591 tests
passed before the run exposed four change-adjacent regressions (route snapshot,
two ASR WebSocket compatibility cases, and an order-sensitive network case).
The three deterministic regressions were corrected; all four are included in
the 324-test compatibility gate above. Re-running the remaining last-failed set
produced 33 failures, three
setup errors, and one pass, all confined to unmodified engine/FFmpeg/IndexTTS,
AppRun/shell, dubbing-export and Windows path/permission
tests. No failing assertion targets the new auth/session behavior, and the
affected route/middleware suites pass in the compatibility gate above. These
existing Windows/offline failures are recorded rather than hidden; the required
Linux CI gate must still be green before merge.

The legacy Node runner passed 70/71. Its only failure is the unmodified
`clearDevPorts.test.mjs` POSIX-path fixture under a Windows host. The general
development E2E suite passed 12/17; its five Gallery cases remained on the
installer splash even with a real offline backend, while the production-bundle
gate and the dedicated real remote-auth flow both passed. No product assertion
in those five Gallery cases covers this change.

## Rejected alternatives

### Put the master key in a hardened `HttpOnly` cookie

Rejected. Cookie flags reduce JavaScript extraction but retain a permanent,
full-power credential as ambient browser authority and do not provide expiry,
scope, or revocation.

### Use JWT/stateless HMAC sessions

Rejected. Stateless sessions complicate individual revocation and key rotation.
Deriving a signing key from a potentially human-chosen API key also creates an
offline verifier if a token is stolen. A small process-local opaque-token store
is simpler and safer for VoiceStudio's single-process architecture.

### Persist browser sessions in SQLite

Rejected for this change. Persistence extends credential lifetime across
backend restarts, adds a verifier and migration surface, and provides little
value for a remote admin session. Re-authentication after restart is an
intentional security boundary.

### Store the master in the Tauri webview until a native keychain is added

Rejected. Platform secure-storage work must be explicit and parity-tested.
Until then, the correct fallback is re-authentication, not durable plaintext
storage.

### Disable remote admin entirely

Rejected. Server-mode remote administration is required behavior. The solution
must harden credential lifecycle without removing a working cross-platform
capability.
