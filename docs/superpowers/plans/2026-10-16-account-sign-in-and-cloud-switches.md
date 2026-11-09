# Account Sign-in and Cloud Switches (S0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a VoiceStudio user sign in to VoiceStudio Cloud from the desktop app with browser-based OAuth (Authorization Code + PKCE, loopback redirect). The user can turn account features and cloud sync off during first-run setup or at any time, and turning one off stops all Cloud activity at once.

**Architecture:** Every VoiceStudio Cloud call lives in Electron main, under `electron/src/main/account/`. One gate, `CloudActivity`, owns every request and timer and refuses all of them while the persisted switches say off. The refresh token is kept only in main, encrypted with `safeStorage`. The renderer sees a token-free `AccountPublicState` over IPC. The Python backend can get a short-lived access token over a loopback bridge, the same pattern as `llm-agent-bridge.ts`. It gets one only when sync is on and the backend is local. The whole feature is hidden behind a `__CLOUD_ACCOUNTS__` build flag until the `vssaas` production API launches.

**Tech Stack:** Electron main (TypeScript, `node:http`, `node:crypto`, `safeStorage`, global `fetch`), React renderer with i18next and TanStack Query, Vitest via `vp test`, Python 3 FastAPI backend with pytest.

**Spec:** `account_login/HANDOFF.md` plus `account_login/desktop-app-guide.md` §1–2, 10, 11, and `account_login/desktop-account-client.md`. The folder is git-ignored. The canonical copies are in the `vssaas` repo at `b91dd08`.

**Out of scope (later plans):** S1 settings sync (allowlist, change hooks, sync tables, merge, SSE, "Choose what to sync" UI), S2 voice-sync prep (`sync_id` on voices, consent capture, `VSB1`), and every VoiceStudioPro task (S0-11, S0-12, S1-29). Each gets its own plan once this one lands, because they all consume the interfaces defined here.

## Global Constraints

- `client_id` is `voicestudio-desktop-oss`. It is a public client with no secret.
- Environments (guide §1):
  - prod: issuer `https://voicestudio.sh/identity/realms/vssaas`, account `https://appapi.voicestudio.sh/v1/account`, sync `https://appapi.voicestudio.sh/v1/sync`, signed-in page `https://app.voicestudio.sh/app/desktop-signed-in`.
  - dev: `https://dev.voicestudio.sh/identity/realms/vssaas`, `https://dev-appapi.voicestudio.sh/v1/account`, `https://dev-appapi.voicestudio.sh/v1/sync`, `https://dev-app.voicestudio.sh/app/desktop-signed-in`.
  - local: `http://127.0.0.1:3000/identity/realms/vssaas`, `http://127.0.0.1:8080/v1/account`, `http://127.0.0.1:8090/v1/sync`, `http://127.0.0.1:3000/app/desktop-signed-in`.
- Only a non-packaged build may read `VOICESTUDIO_CLOUD_ENV=local|dev|prod`. A packaged build is locked to prod.
- The loopback listener binds `127.0.0.1` on port `0`, never `localhost`, `0.0.0.0` or `::`. It accepts one `/callback` within 5 minutes.
- `state` is base64url(32 random bytes), `code_verifier` is base64url(64 random bytes), and `code_challenge` is base64url(SHA-256(verifier)) with `S256`. The scope is `openid offline_access`, and the token response's `scope` must contain `offline_access`.
- Refresh when `expires_at - now < 60 s` or after a `401`. Refresh is single-flight, and the returned `refresh_token` always replaces the stored one.
- `400 invalid_grant` deletes the token and shows "signed out". A network error or `5xx` keeps the user signed in, with backoff of 1 s, 2 s, 4 s … up to 5 min.
- The refresh token is stored in `userData/account.json`, encrypted with `safeStorage`. With the `basic_text` backend, or when encryption is unavailable, it stays in memory only and is never written as plain text. The access token stays in memory only.
- The renderer only ever sees `{signedIn, email, displayName, avatarUrl, organizations}` plus switch and UI state. It never sees a token.
- Never log a token, code, verifier, or URL with a query string.
- Each `PATCH /v1/account` sends an `Idempotency-Key` of 16–128 characters, which is reused when the same change is retried.
- **Off switches (owner decision 2026-10-16):** two switches, `accountEnabled` and `syncEnabled`. Both are set in first-run setup and in Settings → Account. They are never on by default, and skipping setup means off. `syncEnabled` implies `accountEnabled`. Turning either off takes effect at once, with no restart, and persists. Local data is never deleted. With account features off, the app makes no VoiceStudio Cloud calls. The only exception is the one best-effort revocation of the stored refresh token that the user's own "off" triggers.
- In remote-backend mode, main never hands an account token to any backend.
- Localization: every user-facing string goes through `t()`, and every new key goes into all 21 files in `electron/src/renderer/src/i18n/locales/` (`bun run locale:check`).
- Cross-platform parity: this must behave the same on macOS, Windows and Linux. The one contract-mandated difference: a Linux machine without a keyring (`basic_text`) keeps the session in memory only, and the UI tells the user (`account.not_remembered`).
- Before landing: `cd electron && bun run test && bun run typecheck && bun run lint`, plus `uv run pytest tests/test_cloud_account_bridge.py`. Follow CLAUDE.md's keep-main-green, docs-sync, changelog and attribution rules. Commits must not carry AI trailers.
- **Owner approval gate:** VoiceStudio Cloud joins the sanctioned-outbound-calls list (`.github/CONTRIBUTING.md` and CLAUDE.md, Local-first), which "needs owner approval". Task 11 edits the list. Do not merge until the owner has approved that wording.

## Review Focus

1. **The user turns account features off while a browser sign-in is open.** The loopback listener must close at once, and a later browser callback must get a refused connection, not tokens. Pinned in Task 6: `turning account off mid sign-in closes the listener and stores nothing`.
2. **The access token expires and the account screen and backend bridge ask for it at the same moment.** Exactly one refresh request must go out. Pinned in Task 6: `concurrent getAccessToken calls share one refresh`.
3. **The disk is full or `account.json` is corrupt or truncated on load.** The app must start signed out, with switches as stored, and must not crash. Pinned in Task 2 (corrupt switches file) and Task 5 (corrupt token file).
4. **Repair agents spawn third-party CLIs (Codex, Claude, etc.) that inherit `process.env`.** They must never receive the account bridge URL or token. Pinned in Task 9: `repair agent env strips account bridge vars`.
5. **The backend spawns engine and tool subprocesses that inherit its environment.** The bridge capability must be removed from `os.environ` at backend import. Pinned in Task 9: `test_bridge_env_is_removed_from_os_environ`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `electron/src/main/account/cloud-config.ts` | Environment blocks, `client_id`, the build flag |
| `electron/src/main/account/cloud-switches.ts` | Persisted `{accountEnabled, syncEnabled, prompted}` |
| `electron/src/main/account/cloud-activity.ts` | The one gate: guarded `fetch`, tracked timers, `stopAll()` |
| `electron/src/main/account/oidc.ts` | PKCE, discovery, authorize URL, token/refresh/revoke requests |
| `electron/src/main/account/loopback.ts` | One-shot `127.0.0.1` callback listener |
| `electron/src/main/account/token-store.ts` | `safeStorage` refresh-token file, or memory only |
| `electron/src/main/account/account-session.ts` | Sign-in/out, single-flight refresh, reaction to the switches |
| `electron/src/main/account/account-api.ts` | `GET`/`PATCH /v1/account` with refresh-once on `401` |
| `electron/src/main/account/account-ipc.ts` | IPC channels, `AccountPublicState`, broadcast |
| `electron/src/main/account/account-token-bridge.ts` | Loopback token endpoint for the local backend |
| `electron/src/main/account/index.ts` | `startAccount(...)` that wires everything; the only import used by `index.ts` |
| `backend/services/cloud_account.py` | Backend client of the token bridge |
| `electron/src/renderer/src/components/account-consent.tsx` | First-run Yes/No (setup privacy step) |
| `electron/src/renderer/src/features/settings/account-settings.tsx` | Settings → Account |

Every main module has a colocated `*.test.ts`. Tests stub Electron with `vi.mock('electron', …)`, as `ipc-save.test.ts` does.

---

### Task 1: Cloud environment config and build flag

**Files:**
- Create: `electron/src/main/account/cloud-config.ts`
- Create: `electron/src/main/account/cloud-config.test.ts`
- Modify: `electron/electron.vite.config.ts` (the `define` block, lines 21-30)
- Modify: `electron/src/main/env.d.ts`

**Interfaces:**
- Produces: `type CloudEnvName = 'prod' | 'dev' | 'local'`; `interface CloudEnvironment { name: CloudEnvName; issuer: string; accountApi: string; syncApi: string; signedInPage: string; clientId: string }`; `resolveCloudEnvironment(opts: { packaged: boolean; requested?: string }): CloudEnvironment`; `cloudOrigins(env: CloudEnvironment): Set<string>`; `cloudAccountsAvailable(): boolean`.

- [ ] **Step 1: Write the failing test**

```ts
// electron/src/main/account/cloud-config.test.ts
import { expect, it } from 'vitest';
import { cloudOrigins, resolveCloudEnvironment } from './cloud-config';

it('packaged builds are locked to production whatever the env var says', () => {
  expect(resolveCloudEnvironment({ packaged: true, requested: 'local' }).name).toBe('prod');
});

it('developer builds honour VOICESTUDIO_CLOUD_ENV and default to dev', () => {
  expect(resolveCloudEnvironment({ packaged: false, requested: 'local' }).issuer).toBe(
    'http://127.0.0.1:3000/identity/realms/vssaas',
  );
  expect(resolveCloudEnvironment({ packaged: false }).name).toBe('dev');
  expect(resolveCloudEnvironment({ packaged: false, requested: 'bogus' }).name).toBe('dev');
});

it('uses the open-source public client everywhere', () => {
  for (const requested of ['prod', 'dev', 'local'])
    expect(resolveCloudEnvironment({ packaged: false, requested }).clientId).toBe(
      'voicestudio-desktop-oss',
    );
});

it('lists exactly the origins the app may contact', () => {
  expect([...cloudOrigins(resolveCloudEnvironment({ packaged: true }))].sort()).toEqual([
    'https://appapi.voicestudio.sh',
    'https://voicestudio.sh',
  ]);
});
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd electron && bunx vp test --run src/main/account/cloud-config.test.ts`
Expected: FAIL, "Failed to resolve import ./cloud-config".

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/cloud-config.ts
/** VoiceStudio Cloud endpoints (vssaas desktop-app-guide §1). One block per environment. */
export type CloudEnvName = 'prod' | 'dev' | 'local';

export interface CloudEnvironment {
  name: CloudEnvName;
  issuer: string;
  accountApi: string;
  syncApi: string;
  signedInPage: string;
  clientId: string;
}

const CLIENT_ID = 'voicestudio-desktop-oss';

const ENVIRONMENTS: Record<CloudEnvName, Omit<CloudEnvironment, 'name' | 'clientId'>> = {
  prod: {
    issuer: 'https://voicestudio.sh/identity/realms/vssaas',
    accountApi: 'https://appapi.voicestudio.sh/v1/account',
    syncApi: 'https://appapi.voicestudio.sh/v1/sync',
    signedInPage: 'https://app.voicestudio.sh/app/desktop-signed-in',
  },
  dev: {
    issuer: 'https://dev.voicestudio.sh/identity/realms/vssaas',
    accountApi: 'https://dev-appapi.voicestudio.sh/v1/account',
    syncApi: 'https://dev-appapi.voicestudio.sh/v1/sync',
    signedInPage: 'https://dev-app.voicestudio.sh/app/desktop-signed-in',
  },
  local: {
    issuer: 'http://127.0.0.1:3000/identity/realms/vssaas',
    accountApi: 'http://127.0.0.1:8080/v1/account',
    syncApi: 'http://127.0.0.1:8090/v1/sync',
    signedInPage: 'http://127.0.0.1:3000/app/desktop-signed-in',
  },
};

export function resolveCloudEnvironment(opts: {
  packaged: boolean;
  requested?: string;
}): CloudEnvironment {
  const requested = opts.requested as CloudEnvName | undefined;
  const name: CloudEnvName = opts.packaged
    ? 'prod'
    : requested && requested in ENVIRONMENTS
      ? requested
      : 'dev';
  return { name, clientId: CLIENT_ID, ...ENVIRONMENTS[name] };
}

/** Sync origins join in S1; S0 talks only to the issuer and the account API. */
export function cloudOrigins(env: CloudEnvironment): Set<string> {
  return new Set([new URL(env.issuer).origin, new URL(env.accountApi).origin]);
}

/** Off in release builds until the vssaas production API launches. */
export function cloudAccountsAvailable(): boolean {
  return typeof __CLOUD_ACCOUNTS__ === 'boolean' && __CLOUD_ACCOUNTS__;
}
```

Add `declare const __CLOUD_ACCOUNTS__: boolean;` to `electron/src/main/env.d.ts`. In the `define` block of `electron/electron.vite.config.ts`, add:

```ts
  // VoiceStudio Cloud sign-in ships dark until the vssaas production API is live.
  __CLOUD_ACCOUNTS__: JSON.stringify(
    process.env.VOICESTUDIO_CLOUD_ACCOUNTS === '1' || process.env.NODE_ENV === 'development',
  ),
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd electron && bunx vp test --run src/main/account/cloud-config.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add electron/src/main/account/cloud-config.ts electron/src/main/account/cloud-config.test.ts electron/electron.vite.config.ts electron/src/main/env.d.ts
git commit -m "feat(account): cloud environment config and build flag"
```

---

### Task 2: Persisted switches and the CloudActivity gate

**Files:**
- Create: `electron/src/main/account/cloud-switches.ts`, `cloud-switches.test.ts`
- Create: `electron/src/main/account/cloud-activity.ts`, `cloud-activity.test.ts`

**Interfaces:**
- Produces:
  - `interface CloudSwitches { accountEnabled: boolean; syncEnabled: boolean; prompted: boolean }`
  - `class CloudSwitchStore { constructor(path: string); get(): CloudSwitches; set(patch: Partial<CloudSwitches>): CloudSwitches; subscribe(cb: (s: CloudSwitches, prev: CloudSwitches) => void): () => void }`
  - `class CloudDisabledError extends Error`
  - `class CloudActivity { constructor(opts: { isAllowed: () => boolean; origins: Set<string>; fetchImpl?: typeof fetch }); fetch(url: string, init?: RequestInit, opts?: { final?: boolean; timeoutMs?: number }): Promise<Response>; setTimeout(fn: () => void, ms: number): void; onStop(fn: () => void): () => void; stopAll(): void }`

- [ ] **Step 1: Write the failing tests**

```ts
// electron/src/main/account/cloud-switches.test.ts
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import { CloudSwitchStore } from './cloud-switches';

const file = () => join(mkdtempSync(join(tmpdir(), 'vs-switch-')), 'cloud-switches.json');

it('starts with everything off and unprompted', () => {
  expect(new CloudSwitchStore(file()).get()).toEqual({
    accountEnabled: false,
    syncEnabled: false,
    prompted: false,
  });
});

it('a corrupt file reads as all off', () => {
  const path = file();
  writeFileSync(path, '{"accountEnabled": tr');
  expect(new CloudSwitchStore(path).get().accountEnabled).toBe(false);
});

it('persists across instances (restart)', () => {
  const path = file();
  new CloudSwitchStore(path).set({ accountEnabled: true, syncEnabled: true, prompted: true });
  expect(new CloudSwitchStore(path).get()).toEqual({
    accountEnabled: true,
    syncEnabled: true,
    prompted: true,
  });
  expect(JSON.parse(readFileSync(path, 'utf8')).syncEnabled).toBe(true);
});

it('sync cannot be on while account features are off', () => {
  const store = new CloudSwitchStore(file());
  expect(store.set({ syncEnabled: true }).syncEnabled).toBe(false);
  store.set({ accountEnabled: true, syncEnabled: true });
  expect(store.set({ accountEnabled: false })).toMatchObject({
    accountEnabled: false,
    syncEnabled: false,
  });
});

it('notifies subscribers with the previous value', () => {
  const store = new CloudSwitchStore(file());
  const cb = vi.fn();
  store.subscribe(cb);
  store.set({ accountEnabled: true });
  expect(cb).toHaveBeenCalledWith(
    expect.objectContaining({ accountEnabled: true }),
    expect.objectContaining({ accountEnabled: false }),
  );
});
```

```ts
// electron/src/main/account/cloud-activity.test.ts
import { expect, it, vi } from 'vitest';
import { CloudActivity, CloudDisabledError } from './cloud-activity';

const origins = new Set(['https://voicestudio.sh']);

it('refuses every request while switched off, without touching the network', async () => {
  const fetchImpl = vi.fn();
  const activity = new CloudActivity({ isAllowed: () => false, origins, fetchImpl });
  await expect(activity.fetch('https://voicestudio.sh/x')).rejects.toBeInstanceOf(
    CloudDisabledError,
  );
  expect(fetchImpl).not.toHaveBeenCalled();
});

it('refuses origins outside the configured environment', async () => {
  const fetchImpl = vi.fn();
  const activity = new CloudActivity({ isAllowed: () => true, origins, fetchImpl });
  await expect(activity.fetch('https://evil.example/x')).rejects.toThrow(/origin/);
  expect(fetchImpl).not.toHaveBeenCalled();
});

it('stopAll aborts in-flight requests, cancels timers and runs stop hooks', async () => {
  let seen: AbortSignal | undefined;
  const fetchImpl = vi.fn(
    (_url: string, init: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        seen = init.signal!;
        init.signal!.addEventListener('abort', () => reject(init.signal!.reason));
      }),
  );
  const activity = new CloudActivity({ isAllowed: () => true, origins, fetchImpl });
  const timer = vi.fn();
  const hook = vi.fn();
  vi.useFakeTimers();
  activity.setTimeout(timer, 1000);
  activity.onStop(hook);
  const pending = activity.fetch('https://voicestudio.sh/x');
  activity.stopAll();
  await expect(pending).rejects.toBeTruthy();
  vi.advanceTimersByTime(5000);
  vi.useRealTimers();
  expect(seen?.aborted).toBe(true);
  expect(timer).not.toHaveBeenCalled();
  expect(hook).toHaveBeenCalledOnce();
});

it('allows exactly one final call after stopAll (the revocation)', async () => {
  const fetchImpl = vi.fn(async () => new Response('{}'));
  let allowed = true;
  const activity = new CloudActivity({ isAllowed: () => allowed, origins, fetchImpl });
  allowed = false;
  activity.stopAll();
  await activity.fetch('https://voicestudio.sh/revoke', {}, { final: true });
  await expect(
    activity.fetch('https://voicestudio.sh/revoke', {}, { final: true }),
  ).rejects.toBeInstanceOf(CloudDisabledError);
  expect(fetchImpl).toHaveBeenCalledOnce();
});

it('never follows redirects', async () => {
  const fetchImpl = vi.fn(async () => new Response('{}'));
  const activity = new CloudActivity({ isAllowed: () => true, origins, fetchImpl });
  await activity.fetch('https://voicestudio.sh/x');
  expect(fetchImpl.mock.calls[0][1]).toMatchObject({ redirect: 'error' });
});
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd electron && bunx vp test --run src/main/account/cloud-switches.test.ts src/main/account/cloud-activity.test.ts`
Expected: FAIL, unresolved imports.

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/cloud-switches.ts
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

/** Owner rule 2026-10-16: off by default; skipping setup means off; sync implies account. */
export interface CloudSwitches {
  accountEnabled: boolean;
  syncEnabled: boolean;
  prompted: boolean;
}

const OFF: CloudSwitches = { accountEnabled: false, syncEnabled: false, prompted: false };

function normalize(raw: Partial<CloudSwitches>): CloudSwitches {
  const accountEnabled = raw.accountEnabled === true;
  return {
    accountEnabled,
    syncEnabled: accountEnabled && raw.syncEnabled === true,
    prompted: raw.prompted === true,
  };
}

export class CloudSwitchStore {
  private value: CloudSwitches;
  private readonly listeners = new Set<(s: CloudSwitches, prev: CloudSwitches) => void>();

  constructor(private readonly path: string) {
    let raw: Partial<CloudSwitches> = OFF;
    try {
      raw = JSON.parse(readFileSync(path, 'utf8'));
    } catch {
      /* first run or corrupt: everything off */
    }
    this.value = normalize(raw ?? OFF);
  }

  get(): CloudSwitches {
    return { ...this.value };
  }

  set(patch: Partial<CloudSwitches>): CloudSwitches {
    const prev = this.value;
    this.value = normalize({ ...prev, ...patch });
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${process.pid}.tmp`;
    writeFileSync(temporary, JSON.stringify(this.value), { encoding: 'utf8', mode: 0o600 });
    renameSync(temporary, this.path);
    for (const cb of this.listeners) cb(this.get(), { ...prev });
    return this.get();
  }

  subscribe(cb: (s: CloudSwitches, prev: CloudSwitches) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
}
```

The listener fires after the write. If `writeFileSync` throws (disk full), the in-memory value has still changed, so "off" takes effect for this run. The UI then shows the error from the IPC call (Task 8).

```ts
// electron/src/main/account/cloud-activity.ts
/**
 * The only door to VoiceStudio Cloud. Every request and timer goes through here,
 * so turning a switch off can stop all of them at once (owner rule 2026-10-16).
 */
export class CloudDisabledError extends Error {
  constructor() {
    super('VoiceStudio Cloud is switched off');
    this.name = 'CloudDisabledError';
  }
}

export class CloudActivity {
  private readonly controllers = new Set<AbortController>();
  private readonly timers = new Set<NodeJS.Timeout>();
  private readonly stopHooks = new Set<() => void>();
  private finalAvailable = false;
  private readonly fetchImpl: typeof fetch;

  constructor(
    private readonly opts: { isAllowed: () => boolean; origins: Set<string>; fetchImpl?: typeof fetch },
  ) {
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  async fetch(
    url: string,
    init: RequestInit = {},
    opts: { final?: boolean; timeoutMs?: number } = {},
  ): Promise<Response> {
    if (!this.opts.isAllowed()) {
      if (!(opts.final && this.finalAvailable)) throw new CloudDisabledError();
      this.finalAvailable = false;
    }
    if (!this.opts.origins.has(new URL(url).origin))
      throw new Error('Refusing a request outside the VoiceStudio Cloud origin');
    const controller = new AbortController();
    this.controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(new Error('timeout')), opts.timeoutMs ?? 30_000);
    try {
      return await this.fetchImpl(url, { ...init, redirect: 'error', signal: controller.signal });
    } finally {
      clearTimeout(timeout);
      this.controllers.delete(controller);
    }
  }

  setTimeout(fn: () => void, ms: number): void {
    if (!this.opts.isAllowed()) return;
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      if (this.opts.isAllowed()) fn();
    }, ms);
    this.timers.add(timer);
  }

  onStop(fn: () => void): () => void {
    this.stopHooks.add(fn);
    return () => this.stopHooks.delete(fn);
  }

  /** Abort everything now. One `final` request (the revocation) stays possible. */
  stopAll(): void {
    for (const c of this.controllers) c.abort(new CloudDisabledError());
    this.controllers.clear();
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
    for (const hook of [...this.stopHooks]) hook();
    this.finalAvailable = true;
  }
}
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd electron && bunx vp test --run src/main/account/cloud-switches.test.ts src/main/account/cloud-activity.test.ts`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add electron/src/main/account/cloud-switches.* electron/src/main/account/cloud-activity.*
git commit -m "feat(account): persisted cloud switches and a single activity gate"
```

---

### Task 3: OIDC client (PKCE, discovery, token requests)

**Files:**
- Create: `electron/src/main/account/oidc.ts`, `oidc.test.ts`

**Interfaces:**
- Consumes: `CloudEnvironment` (Task 1), `CloudActivity` (Task 2).
- Produces:
  - `base64url(buf: Buffer): string`
  - `createPkce(): { state: string; verifier: string; challenge: string }`
  - `challengeFor(verifier: string): string`
  - `interface OidcEndpoints { authorization_endpoint: string; token_endpoint: string; revocation_endpoint: string }`
  - `interface TokenResponse { access_token: string; expires_in: number; refresh_token: string; scope: string }`
  - `class InvalidGrantError extends Error`, `class TransientAuthError extends Error`
  - `class OidcClient { constructor(env, activity, now?: () => number); discover(): Promise<OidcEndpoints>; authorizeUrl(e: OidcEndpoints, redirectUri: string, p: { state: string; challenge: string }): string; exchangeCode(code: string, redirectUri: string, verifier: string): Promise<TokenResponse>; refresh(refreshToken: string): Promise<TokenResponse>; revoke(refreshToken: string): Promise<void> }`

- [ ] **Step 1: Write the failing test**

```ts
// electron/src/main/account/oidc.test.ts
import { createHash } from 'node:crypto';
import { expect, it, vi } from 'vitest';
import { CloudActivity } from './cloud-activity';
import { cloudOrigins, resolveCloudEnvironment } from './cloud-config';
import { InvalidGrantError, OidcClient, TransientAuthError, base64url, createPkce } from './oidc';

const env = resolveCloudEnvironment({ packaged: true });
const discovery = {
  issuer: env.issuer,
  authorization_endpoint: `${env.issuer}/protocol/openid-connect/auth`,
  token_endpoint: `${env.issuer}/protocol/openid-connect/token`,
  revocation_endpoint: `${env.issuer}/protocol/openid-connect/revoke`,
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const client = (fetchImpl: typeof fetch, now = () => 0) =>
  new OidcClient(
    env,
    new CloudActivity({ isAllowed: () => true, origins: cloudOrigins(env), fetchImpl }),
    now,
  );

it('PKCE challenge is base64url(SHA-256(verifier)) with spec lengths', () => {
  const p = createPkce();
  expect(p.challenge).toBe(base64url(createHash('sha256').update(p.verifier).digest()));
  expect(Buffer.from(p.verifier, 'base64url')).toHaveLength(64);
  expect(Buffer.from(p.state, 'base64url')).toHaveLength(32);
  expect(p.verifier).not.toMatch(/[+/=]/);
});

it('refuses a discovery document for another issuer', async () => {
  const c = client(vi.fn(async () => json({ ...discovery, issuer: 'https://evil.example' })));
  await expect(c.discover()).rejects.toThrow(/issuer/);
});

it('caches discovery for 24 hours', async () => {
  let t = 0;
  const fetchImpl = vi.fn(async () => json(discovery));
  const c = client(fetchImpl, () => t);
  await c.discover();
  t = 23 * 3600_000;
  await c.discover();
  expect(fetchImpl).toHaveBeenCalledTimes(1);
  t = 25 * 3600_000;
  await c.discover();
  expect(fetchImpl).toHaveBeenCalledTimes(2);
});

it('builds the authorize URL from the contract', () => {
  const url = new URL(
    client(vi.fn()).authorizeUrl(discovery, 'http://127.0.0.1:5000/callback', {
      state: 's',
      challenge: 'c',
    }),
  );
  expect(Object.fromEntries(url.searchParams)).toEqual({
    client_id: 'voicestudio-desktop-oss',
    response_type: 'code',
    redirect_uri: 'http://127.0.0.1:5000/callback',
    scope: 'openid offline_access',
    state: 's',
    code_challenge: 'c',
    code_challenge_method: 'S256',
  });
});

it('rejects a token response without offline_access', async () => {
  const fetchImpl = vi.fn(async (u: string) =>
    u.endsWith('openid-configuration')
      ? json(discovery)
      : json({ access_token: 'a', refresh_token: 'r', expires_in: 300, scope: 'openid' }),
  );
  await expect(client(fetchImpl).exchangeCode('c', 'http://127.0.0.1:1/callback', 'v')).rejects.toThrow(
    /offline_access/,
  );
});

it('maps refresh errors: invalid_grant vs transient', async () => {
  const respond = (r: Response) =>
    vi.fn(async (u: string) => (u.endsWith('openid-configuration') ? json(discovery) : r));
  await expect(client(respond(json({ error: 'invalid_grant' }, 400))).refresh('r')).rejects.toBeInstanceOf(
    InvalidGrantError,
  );
  await expect(client(respond(json({}, 503))).refresh('r')).rejects.toBeInstanceOf(TransientAuthError);
});

it('sends form-encoded refresh with the public client id and no secret', async () => {
  const fetchImpl = vi.fn(async (u: string, _init?: RequestInit) =>
    u.endsWith('openid-configuration')
      ? json(discovery)
      : json({ access_token: 'a', refresh_token: 'r2', expires_in: 300, scope: 'openid offline_access' }),
  );
  await client(fetchImpl).refresh('r1');
  const body = new URLSearchParams(String(fetchImpl.mock.calls[1][1]!.body));
  expect(Object.fromEntries(body)).toEqual({
    grant_type: 'refresh_token',
    client_id: 'voicestudio-desktop-oss',
    refresh_token: 'r1',
  });
});
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd electron && bunx vp test --run src/main/account/oidc.test.ts`
Expected: FAIL, unresolved import.

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/oidc.ts
import { createHash, randomBytes } from 'node:crypto';
import type { CloudActivity } from './cloud-activity';
import type { CloudEnvironment } from './cloud-config';

export interface OidcEndpoints {
  authorization_endpoint: string;
  token_endpoint: string;
  revocation_endpoint: string;
}

export interface TokenResponse {
  access_token: string;
  expires_in: number;
  refresh_token: string;
  scope: string;
}

export class InvalidGrantError extends Error {
  constructor() {
    super('The VoiceStudio Cloud session ended');
    this.name = 'InvalidGrantError';
  }
}

export class TransientAuthError extends Error {
  constructor(status?: number) {
    super(`VoiceStudio Cloud sign-in is unavailable${status ? ` (${status})` : ''}`);
    this.name = 'TransientAuthError';
  }
}

export const base64url = (buf: Buffer): string => buf.toString('base64url');
export const challengeFor = (verifier: string): string =>
  base64url(createHash('sha256').update(verifier).digest());

export function createPkce(): { state: string; verifier: string; challenge: string } {
  const verifier = base64url(randomBytes(64));
  return { state: base64url(randomBytes(32)), verifier, challenge: challengeFor(verifier) };
}

const DISCOVERY_TTL_MS = 24 * 3600_000;

export class OidcClient {
  private cached: { at: number; endpoints: OidcEndpoints } | null = null;

  constructor(
    private readonly env: CloudEnvironment,
    private readonly activity: CloudActivity,
    private readonly now: () => number = Date.now,
  ) {}

  async discover(): Promise<OidcEndpoints> {
    if (this.cached && this.now() - this.cached.at < DISCOVERY_TTL_MS) return this.cached.endpoints;
    const res = await this.activity.fetch(`${this.env.issuer}/.well-known/openid-configuration`);
    if (!res.ok) throw new TransientAuthError(res.status);
    const doc = (await res.json()) as Partial<OidcEndpoints> & { issuer?: string };
    if (doc.issuer !== this.env.issuer) throw new Error('Discovery issuer does not match');
    const endpoints = {
      authorization_endpoint: String(doc.authorization_endpoint),
      token_endpoint: String(doc.token_endpoint),
      revocation_endpoint: String(doc.revocation_endpoint),
    };
    this.cached = { at: this.now(), endpoints };
    return endpoints;
  }

  authorizeUrl(e: OidcEndpoints, redirectUri: string, p: { state: string; challenge: string }): string {
    const url = new URL(e.authorization_endpoint);
    url.search = new URLSearchParams({
      client_id: this.env.clientId,
      response_type: 'code',
      redirect_uri: redirectUri,
      scope: 'openid offline_access',
      state: p.state,
      code_challenge: p.challenge,
      code_challenge_method: 'S256',
    }).toString();
    return url.toString();
  }

  exchangeCode(code: string, redirectUri: string, verifier: string): Promise<TokenResponse> {
    return this.token({
      grant_type: 'authorization_code',
      client_id: this.env.clientId,
      code,
      redirect_uri: redirectUri,
      code_verifier: verifier,
    });
  }

  refresh(refreshToken: string): Promise<TokenResponse> {
    return this.token({
      grant_type: 'refresh_token',
      client_id: this.env.clientId,
      refresh_token: refreshToken,
    });
  }

  /** Best effort; the caller deletes the stored token whatever happens. */
  async revoke(refreshToken: string): Promise<void> {
    const { revocation_endpoint } = await this.discover();
    await this.activity.fetch(
      revocation_endpoint,
      this.form({ client_id: this.env.clientId, token: refreshToken, token_type_hint: 'refresh_token' }),
      { final: true, timeoutMs: 5_000 },
    );
  }

  private form(fields: Record<string, string>): RequestInit {
    return {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(fields).toString(),
    };
  }

  private async token(fields: Record<string, string>): Promise<TokenResponse> {
    const { token_endpoint } = await this.discover();
    let res: Response;
    try {
      res = await this.activity.fetch(token_endpoint, this.form(fields));
    } catch (error) {
      if ((error as Error).name === 'CloudDisabledError') throw error;
      throw new TransientAuthError();
    }
    const body = (await res.json().catch(() => ({}))) as Partial<TokenResponse> & { error?: string };
    if (res.status === 400 && body.error === 'invalid_grant') throw new InvalidGrantError();
    if (!res.ok) throw new TransientAuthError(res.status);
    if (!String(body.scope ?? '').split(' ').includes('offline_access'))
      throw new Error('Token response lacks offline_access');
    return body as TokenResponse;
  }
}
```

Revocation note: `revoke()` calls `discover()` first, and the discovery fetch is not a `final` call. This is safe because sign-in always populates the 24 h discovery cache before a token can exist. If the cache has expired by the time of an "off", `discover()` throws `CloudDisabledError`. The revoke is skipped, and the token is still deleted locally (Task 6). Keycloak then ends the session after 30 days unused.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd electron && bunx vp test --run src/main/account/oidc.test.ts`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add electron/src/main/account/oidc.*
git commit -m "feat(account): OIDC discovery, PKCE and token requests"
```

---

### Task 4: Loopback callback listener

**Files:**
- Create: `electron/src/main/account/loopback.ts`, `loopback.test.ts`

**Interfaces:**
- Produces: `class LoopbackError extends Error { reason: 'denied' | 'timeout' | 'closed' }`; `startLoopback(opts: { expectedState: string; signedInPage: string; timeoutMs?: number }): Promise<{ redirectUri: string; code: Promise<string>; close(): void }>`.

The contract docs disagree on a wrong `state`. The guide (§2 step 3) says answer `400` and keep waiting. `desktop-account-client.md` step 4 says refuse and stop listening. This plan follows the guide, because `HANDOFF.md` names it the main document. Task 11 records the disagreement for `vssaas`.

- [ ] **Step 1: Write the failing test**

```ts
// electron/src/main/account/loopback.test.ts
import { expect, it } from 'vitest';
import { LoopbackError, startLoopback } from './loopback';

const page = 'https://app.voicestudio.sh/app/desktop-signed-in';
const hit = (uri: string, query: string) =>
  fetch(uri + query, { redirect: 'manual' }).then((r) => ({ status: r.status, location: r.headers.get('location') }));

it('binds only 127.0.0.1 on a random port', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page });
  expect(lb.redirectUri).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/callback$/);
  lb.close();
  await expect(lb.code).rejects.toBeInstanceOf(LoopbackError);
});

it('refuses a wrong state and keeps waiting for the right one', async () => {
  const lb = await startLoopback({ expectedState: 'good', signedInPage: page });
  expect((await hit(lb.redirectUri, '?code=x&state=bad')).status).toBe(400);
  const ok = await hit(lb.redirectUri, '?code=real&state=good');
  expect(ok).toEqual({ status: 302, location: page });
  await expect(lb.code).resolves.toBe('real');
});

it('answers 404 off the callback path', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page });
  expect((await hit(lb.redirectUri.replace('/callback', '/other'), '?state=s&code=c')).status).toBe(404);
  lb.close();
  await lb.code.catch(() => {});
});

it('reports a denied sign-in', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page });
  await hit(lb.redirectUri, '?error=access_denied&state=s');
  await expect(lb.code).rejects.toMatchObject({ reason: 'denied' });
});

it('times out', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page, timeoutMs: 50 });
  await expect(lb.code).rejects.toMatchObject({ reason: 'timeout' });
});

it('refuses connections once closed', async () => {
  const lb = await startLoopback({ expectedState: 's', signedInPage: page });
  lb.close();
  await lb.code.catch(() => {});
  await expect(hit(lb.redirectUri, '?code=c&state=s')).rejects.toBeTruthy();
});
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd electron && bunx vp test --run src/main/account/loopback.test.ts`
Expected: FAIL, unresolved import.

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/loopback.ts
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { timingSafeEqual } from 'node:crypto';

export class LoopbackError extends Error {
  constructor(readonly reason: 'denied' | 'timeout' | 'closed') {
    super(`Sign-in ${reason}`);
    this.name = 'LoopbackError';
  }
}

const sameState = (a: string, b: string) =>
  a.length === b.length && timingSafeEqual(Buffer.from(a), Buffer.from(b));

/** RFC 8252 loopback receiver: one /callback within the timeout, then gone. */
export async function startLoopback(opts: {
  expectedState: string;
  signedInPage: string;
  timeoutMs?: number;
}): Promise<{ redirectUri: string; code: Promise<string>; close(): void }> {
  let settle!: (result: { code?: string; error?: LoopbackError }) => void;
  const outcome = new Promise<{ code?: string; error?: LoopbackError }>((r) => (settle = r));
  let done = false;
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1');
    if (done || req.method !== 'GET' || url.pathname !== '/callback') {
      res.writeHead(404).end();
      return;
    }
    if (!sameState(url.searchParams.get('state') ?? '', opts.expectedState)) {
      res.writeHead(400).end();
      return;
    }
    const code = url.searchParams.get('code');
    finish(url.searchParams.has('error') || !code ? { error: new LoopbackError('denied') } : { code });
    res.writeHead(302, { location: opts.signedInPage, connection: 'close' }).end();
  });
  const timer = setTimeout(() => finish({ error: new LoopbackError('timeout') }), opts.timeoutMs ?? 300_000);
  function finish(result: { code?: string; error?: LoopbackError }) {
    if (done) return;
    done = true;
    clearTimeout(timer);
    server.close();
    server.closeAllConnections?.();
    settle(result);
  }
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });
  const { port } = server.address() as AddressInfo;
  return {
    redirectUri: `http://127.0.0.1:${port}/callback`,
    code: outcome.then((r) => (r.error ? Promise.reject(r.error) : (r.code as string))),
    close: () => finish({ error: new LoopbackError('closed') }),
  };
}
```

Check while reviewing: the `302` must reach the browser before `server.close()` drops the socket. `finish` runs before `res.end()`, but `server.close()` only stops new connections, and `closeAllConnections` closes idle ones. If the 302 test flakes, move `finish(...)` into `res.on('finish', ...)`.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd electron && bunx vp test --run src/main/account/loopback.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add electron/src/main/account/loopback.*
git commit -m "feat(account): one-shot 127.0.0.1 OAuth callback listener"
```

---

### Task 5: Refresh-token store

**Files:**
- Create: `electron/src/main/account/token-store.ts`, `token-store.test.ts`

**Interfaces:**
- Produces: `interface SafeStorageLike { isEncryptionAvailable(): boolean; getSelectedStorageBackend?(): string; encryptString(s: string): Buffer; decryptString(b: Buffer): string }`; `class RefreshTokenStore { constructor(path: string, safe: SafeStorageLike, platform?: NodeJS.Platform); readonly persistent: boolean; load(): string | null; save(token: string): void; clear(): void }`.

- [ ] **Step 1: Write the failing test**

```ts
// electron/src/main/account/token-store.test.ts
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { RefreshTokenStore, type SafeStorageLike } from './token-store';

const fakeSafe = (over: Partial<SafeStorageLike> = {}): SafeStorageLike => ({
  isEncryptionAvailable: () => true,
  getSelectedStorageBackend: () => 'gnome_libsecret',
  encryptString: (s) => Buffer.from(`enc:${s}`),
  decryptString: (b) => b.toString().replace(/^enc:/, ''),
  ...over,
});
const path = () => join(mkdtempSync(join(tmpdir(), 'vs-token-')), 'account.json');

it('round-trips through safeStorage and never writes the token in clear', () => {
  const p = path();
  new RefreshTokenStore(p, fakeSafe(), 'darwin').save('secret-refresh');
  expect(readFileSync(p, 'utf8')).not.toContain('secret-refresh');
  expect(new RefreshTokenStore(p, fakeSafe(), 'darwin').load()).toBe('secret-refresh');
});

it('basic_text on Linux keeps the token in memory only', () => {
  const p = path();
  const store = new RefreshTokenStore(p, fakeSafe({ getSelectedStorageBackend: () => 'basic_text' }), 'linux');
  store.save('t');
  expect(store.persistent).toBe(false);
  expect(existsSync(p)).toBe(false);
  expect(store.load()).toBe('t');
});

it('no encryption available keeps it in memory only', () => {
  const store = new RefreshTokenStore(path(), fakeSafe({ isEncryptionAvailable: () => false }), 'win32');
  expect(store.persistent).toBe(false);
});

it('a corrupt or undecryptable file loads as signed out', () => {
  const p = path();
  writeFileSync(p, '{"v":1,"token":"@@@');
  expect(new RefreshTokenStore(p, fakeSafe(), 'darwin').load()).toBeNull();
  writeFileSync(p, JSON.stringify({ v: 1, token: 'AAAA' }));
  const throwing = fakeSafe({ decryptString: () => { throw new Error('keychain changed'); } });
  expect(new RefreshTokenStore(p, throwing, 'darwin').load()).toBeNull();
});

it('clear removes the file and the memory copy', () => {
  const p = path();
  const store = new RefreshTokenStore(p, fakeSafe(), 'darwin');
  store.save('t');
  store.clear();
  expect(existsSync(p)).toBe(false);
  expect(store.load()).toBeNull();
});
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd electron && bunx vp test --run src/main/account/token-store.test.ts`
Expected: FAIL, unresolved import.

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/token-store.ts
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  getSelectedStorageBackend?(): string;
  encryptString(plain: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

/** The refresh token lives only in Electron main; never as plain text on disk. */
export class RefreshTokenStore {
  readonly persistent: boolean;
  private memory: string | null = null;

  constructor(
    private readonly path: string,
    private readonly safe: SafeStorageLike,
    platform: NodeJS.Platform = process.platform,
  ) {
    this.persistent =
      safe.isEncryptionAvailable() &&
      !(platform === 'linux' && safe.getSelectedStorageBackend?.() === 'basic_text');
  }

  load(): string | null {
    if (this.memory || !this.persistent) return this.memory;
    try {
      const { v, token } = JSON.parse(readFileSync(this.path, 'utf8'));
      if (v !== 1 || typeof token !== 'string') return null;
      this.memory = this.safe.decryptString(Buffer.from(token, 'base64'));
    } catch {
      this.memory = null;
    }
    return this.memory;
  }

  save(token: string): void {
    this.memory = token;
    if (!this.persistent) return;
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${process.pid}.tmp`;
    const body = { v: 1, token: this.safe.encryptString(token).toString('base64') };
    writeFileSync(temporary, JSON.stringify(body), { encoding: 'utf8', mode: 0o600 });
    renameSync(temporary, this.path);
  }

  clear(): void {
    this.memory = null;
    rmSync(this.path, { force: true });
  }
}
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd electron && bunx vp test --run src/main/account/token-store.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add electron/src/main/account/token-store.*
git commit -m "feat(account): safeStorage refresh-token store with memory-only fallback"
```

---

### Task 6: Account session (sign-in, refresh, sign-out, switch reaction)

**Files:**
- Create: `electron/src/main/account/account-session.ts`, `account-session.test.ts`

**Interfaces:**
- Consumes: `OidcClient`, `InvalidGrantError`, `TransientAuthError`, `createPkce` (Task 3); `startLoopback`, `LoopbackError` (Task 4); `RefreshTokenStore` (Task 5); `CloudActivity`, `CloudSwitchStore` (Task 2).
- Produces:
  - `type SessionNotice = 'signed_out_expired' | 'sign_in_failed' | 'offline' | null`
  - `interface SessionState { signedIn: boolean; signingIn: boolean; notice: SessionNotice }`
  - `interface AccessToken { access_token: string; expires_at: number }`
  - `backoffDelay(attempt: number): number`
  - `class AccountSession { constructor(deps: SessionDeps); state(): SessionState; onChange(cb: (s: SessionState) => void): () => void; signIn(): Promise<void>; cancelSignIn(): void; signOut(): Promise<void>; getAccessToken(): Promise<AccessToken | null>; invalidateAccessToken(): void; endSession(notice: SessionNotice): void }`
  - `interface SessionDeps { oidc: OidcClient; store: RefreshTokenStore; activity: CloudActivity; switches: CloudSwitchStore; openExternal: (url: string) => Promise<void>; startLoopback: typeof startLoopback; signedInPage: string; now?: () => number }`

- [ ] **Step 1: Write the failing test**

```ts
// electron/src/main/account/account-session.test.ts
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import { AccountSession, backoffDelay } from './account-session';
import { CloudActivity } from './cloud-activity';
import { CloudSwitchStore } from './cloud-switches';
import { InvalidGrantError, TransientAuthError } from './oidc';
import { RefreshTokenStore } from './token-store';

const memorySafe = { isEncryptionAvailable: () => false, encryptString: Buffer.from, decryptString: String } as never;

function harness(over: Partial<Record<string, unknown>> = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'vs-session-'));
  const switches = new CloudSwitchStore(join(dir, 'switches.json'));
  switches.set({ accountEnabled: true, prompted: true });
  const store = new RefreshTokenStore(join(dir, 'account.json'), memorySafe);
  const activity = new CloudActivity({
    isAllowed: () => switches.get().accountEnabled,
    origins: new Set(['https://voicestudio.sh']),
    fetchImpl: vi.fn(),
  });
  const tokens = (refresh: string) => ({
    access_token: `a-${refresh}`, refresh_token: refresh, expires_in: 300, scope: 'openid offline_access',
  });
  const oidc = {
    discover: vi.fn(async () => ({ authorization_endpoint: 'https://voicestudio.sh/auth', token_endpoint: '', revocation_endpoint: '' })),
    authorizeUrl: vi.fn(() => 'https://voicestudio.sh/auth?x'),
    exchangeCode: vi.fn(async () => tokens('r1')),
    refresh: vi.fn(async () => tokens('r2')),
    revoke: vi.fn(async () => {}),
  };
  let resolveCode!: (c: string) => void;
  const loopback = { redirectUri: 'http://127.0.0.1:1/callback', code: new Promise<string>((r) => (resolveCode = r)), close: vi.fn() };
  let t = 0;
  const openExternal = vi.fn(async () => {});
  const session = new AccountSession({
    oidc: oidc as never, store, activity, switches,
    openExternal,
    startLoopback: vi.fn(async () => loopback) as never,
    signedInPage: 'https://app.voicestudio.sh/app/desktop-signed-in',
    now: () => t,
    ...over,
  });
  return { session, oidc, store, switches, loopback, openExternal, resolveCode, advance: (ms: number) => (t += ms) };
}

it('signs in through the browser and stores the refresh token', async () => {
  const h = harness();
  const done = h.session.signIn();
  await vi.waitFor(() => expect(h.session.state().signingIn).toBe(true));
  h.resolveCode('code');
  await done;
  expect(h.store.load()).toBe('r1');
  expect(h.session.state()).toMatchObject({ signedIn: true, signingIn: false });
});

it('refuses to start sign-in while account features are off', async () => {
  const h = harness();
  h.switches.set({ accountEnabled: false });
  await expect(h.session.signIn()).rejects.toThrow(/switched off/);
  expect(h.oidc.discover).not.toHaveBeenCalled();
});

it('turning account off mid sign-in closes the listener and stores nothing', async () => {
  const h = harness();
  const done = h.session.signIn().catch((e) => e);
  // openExternal runs only after the listener exists, so close() is wired by now.
  await vi.waitFor(() => expect(h.openExternal).toHaveBeenCalled());
  h.switches.set({ accountEnabled: false });
  expect(h.loopback.close).toHaveBeenCalled();
  h.resolveCode('late-code');
  await done;
  expect(h.oidc.exchangeCode).not.toHaveBeenCalled();
  expect(h.store.load()).toBeNull();
});

it('concurrent getAccessToken calls share one refresh, and the token rotates', async () => {
  const h = harness();
  h.store.save('r1');
  const [a, b, c] = await Promise.all([h.session.getAccessToken(), h.session.getAccessToken(), h.session.getAccessToken()]);
  expect(h.oidc.refresh).toHaveBeenCalledOnce();
  expect(a).toEqual(b);
  expect(c?.access_token).toBe('a-r2');
  expect(h.store.load()).toBe('r2');
});

it('reuses a fresh access token and refreshes inside the 60 s window', async () => {
  const h = harness();
  h.store.save('r1');
  await h.session.getAccessToken();
  h.advance(200_000);
  await h.session.getAccessToken();
  expect(h.oidc.refresh).toHaveBeenCalledTimes(1);
  h.advance(50_000); // 250 s of 300 s used: less than 60 s left
  await h.session.getAccessToken();
  expect(h.oidc.refresh).toHaveBeenCalledTimes(2);
});

it('invalid_grant signs out with the expired notice', async () => {
  const h = harness();
  h.store.save('r1');
  h.oidc.refresh.mockRejectedValueOnce(new InvalidGrantError());
  expect(await h.session.getAccessToken()).toBeNull();
  expect(h.store.load()).toBeNull();
  expect(h.session.state()).toMatchObject({ signedIn: false, notice: 'signed_out_expired' });
});

it('a network error keeps the user signed in', async () => {
  const h = harness();
  h.store.save('r1');
  h.oidc.refresh.mockRejectedValueOnce(new TransientAuthError(503));
  await expect(h.session.getAccessToken()).rejects.toBeInstanceOf(TransientAuthError);
  expect(h.store.load()).toBe('r1');
  expect(h.session.state()).toMatchObject({ signedIn: true, notice: 'offline' });
});

it('turning account features off revokes once, deletes the token and hands out nothing', async () => {
  const h = harness();
  h.store.save('r1');
  h.switches.set({ accountEnabled: false });
  await vi.waitFor(() => expect(h.oidc.revoke).toHaveBeenCalledWith('r1'));
  expect(h.store.load()).toBeNull();
  expect(await h.session.getAccessToken()).toBeNull();
  expect(h.oidc.refresh).not.toHaveBeenCalled();
});

it('sign-out deletes the token even when revocation fails', async () => {
  const h = harness();
  h.store.save('r1');
  h.oidc.revoke.mockRejectedValueOnce(new Error('offline'));
  await h.session.signOut();
  expect(h.store.load()).toBeNull();
  expect(h.session.state().signedIn).toBe(false);
});

it('backoff doubles from 1 s and caps at 5 min', () => {
  expect([0, 1, 2, 3].map(backoffDelay)).toEqual([1000, 2000, 4000, 8000]);
  expect(backoffDelay(20)).toBe(300_000);
});
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd electron && bunx vp test --run src/main/account/account-session.test.ts`
Expected: FAIL, unresolved import.

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/account-session.ts
import { CloudDisabledError, type CloudActivity } from './cloud-activity';
import type { CloudSwitchStore } from './cloud-switches';
import type { startLoopback as StartLoopback } from './loopback';
import { InvalidGrantError, TransientAuthError, createPkce, type OidcClient, type TokenResponse } from './oidc';
import type { RefreshTokenStore } from './token-store';

export type SessionNotice = 'signed_out_expired' | 'sign_in_failed' | 'offline' | null;
export interface SessionState { signedIn: boolean; signingIn: boolean; notice: SessionNotice }
export interface AccessToken { access_token: string; expires_at: number }

export interface SessionDeps {
  oidc: OidcClient;
  store: RefreshTokenStore;
  activity: CloudActivity;
  switches: CloudSwitchStore;
  openExternal: (url: string) => Promise<void>;
  startLoopback: typeof StartLoopback;
  signedInPage: string;
  now?: () => number;
}

export const backoffDelay = (attempt: number): number => Math.min(300_000, 1000 * 2 ** attempt);

const REFRESH_MARGIN_MS = 60_000;

export class AccountSession {
  private access: AccessToken | null = null;
  private inflight: Promise<AccessToken | null> | null = null;
  private closeLoopback: (() => void) | null = null;
  private signingIn = false;
  private notice: SessionNotice = null;
  private readonly listeners = new Set<(s: SessionState) => void>();
  private readonly now: () => number;

  constructor(private readonly deps: SessionDeps) {
    this.now = deps.now ?? Date.now;
    deps.activity.onStop(() => this.closeLoopback?.());
    deps.switches.subscribe((next, prev) => {
      if (prev.accountEnabled && !next.accountEnabled) void this.turnedOff();
    });
  }

  state(): SessionState {
    return { signedIn: this.deps.store.load() !== null, signingIn: this.signingIn, notice: this.notice };
  }

  onChange(cb: (s: SessionState) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  async signIn(): Promise<void> {
    if (!this.deps.switches.get().accountEnabled) throw new CloudDisabledError();
    if (this.signingIn) return;
    const pkce = createPkce();
    this.signingIn = true;
    this.notice = null;
    this.emit();
    try {
      const endpoints = await this.deps.oidc.discover();
      const lb = await this.deps.startLoopback({ expectedState: pkce.state, signedInPage: this.deps.signedInPage });
      this.closeLoopback = lb.close;
      await this.deps.openExternal(this.deps.oidc.authorizeUrl(endpoints, lb.redirectUri, pkce));
      const code = await lb.code;
      if (!this.deps.switches.get().accountEnabled) throw new CloudDisabledError();
      this.accept(await this.deps.oidc.exchangeCode(code, lb.redirectUri, pkce.verifier));
    } catch (error) {
      if (!(error instanceof CloudDisabledError)) this.notice = 'sign_in_failed';
      throw error;
    } finally {
      this.closeLoopback?.();
      this.closeLoopback = null;
      this.signingIn = false;
      this.emit();
    }
  }

  cancelSignIn(): void {
    this.closeLoopback?.();
  }

  async signOut(): Promise<void> {
    const refresh = this.deps.store.load();
    this.endSession(null);
    if (refresh) await this.deps.oidc.revoke(refresh).catch(() => {});
  }

  /** Single-flight; null means signed out or switched off. */
  getAccessToken(): Promise<AccessToken | null> {
    if (!this.deps.switches.get().accountEnabled) return Promise.resolve(null);
    if (this.access && this.access.expires_at - this.now() >= REFRESH_MARGIN_MS)
      return Promise.resolve(this.access);
    this.inflight ??= this.refresh().finally(() => (this.inflight = null));
    return this.inflight;
  }

  invalidateAccessToken(): void {
    this.access = null;
  }

  endSession(notice: SessionNotice): void {
    this.access = null;
    this.deps.store.clear();
    this.notice = notice;
    this.emit();
  }

  private async refresh(): Promise<AccessToken | null> {
    const refresh = this.deps.store.load();
    if (!refresh) return null;
    try {
      this.accept(await this.deps.oidc.refresh(refresh));
      if (this.notice === 'offline') { this.notice = null; this.emit(); }
      return this.access;
    } catch (error) {
      if (error instanceof InvalidGrantError) { this.endSession('signed_out_expired'); return null; }
      if (error instanceof CloudDisabledError) return null;
      if (error instanceof TransientAuthError) { this.notice = 'offline'; this.emit(); }
      throw error;
    }
  }

  private accept(tokens: TokenResponse): void {
    this.deps.store.save(tokens.refresh_token);
    this.access = { access_token: tokens.access_token, expires_at: this.now() + tokens.expires_in * 1000 };
  }

  private async turnedOff(): Promise<void> {
    const refresh = this.deps.store.load();
    this.deps.activity.stopAll();
    this.endSession(null);
    if (refresh) await this.deps.oidc.revoke(refresh).catch(() => {});
  }

  private emit(): void {
    const s = this.state();
    for (const cb of this.listeners) cb(s);
  }
}
```

Backoff: `getAccessToken()` rethrows `TransientAuthError`, so callers decide when to retry. The Account screen retries when the user opens it. S1's sync loop schedules retries with `activity.setTimeout(retry, backoffDelay(n))`, so a switch-off cancels them. S0 needs no background refresh timer, because nothing in S0 runs in the background.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd electron && bunx vp test --run src/main/account/account-session.test.ts`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add electron/src/main/account/account-session.*
git commit -m "feat(account): browser sign-in, single-flight refresh, switch-off teardown"
```

---

### Task 7: Account API client

**Files:**
- Create: `electron/src/main/account/account-api.ts`, `account-api.test.ts`

**Interfaces:**
- Consumes: `AccountSession` (Task 6), `CloudActivity`, `CloudEnvironment`.
- Produces: `interface AccountProfile { email: string | null; displayName: string | null; avatarUrl: string | null; organizations: Array<{ id: string; slug: string; displayName: string; role: string; kind: string }> }`; `class AccountApi { constructor(env: CloudEnvironment, session: AccountSession, activity: CloudActivity); get(): Promise<AccountProfile | null>; patch(changes: { display_name?: string; theme?: 'system' | 'light' | 'dark'; language?: string }): Promise<AccountProfile | null> }`.

- [ ] **Step 1: Write the failing test**

```ts
// electron/src/main/account/account-api.test.ts
import { expect, it, vi } from 'vitest';
import { AccountApi } from './account-api';
import { CloudActivity } from './cloud-activity';
import { cloudOrigins, resolveCloudEnvironment } from './cloud-config';

const env = resolveCloudEnvironment({ packaged: true });
const account = {
  user_id: 'Us3nM8qWe2RtY6uI', email: 'ana@example.com', email_verified: true, display_name: 'Ana',
  avatar_url: null, personal_organization_id: 'Or5tG7hJ9kL1zX3c',
  organizations: [{ id: 'Or5tG7hJ9kL1zX3c', slug: 'ana', display_name: 'Ana', role: 'owner', kind: 'personal' }],
};
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status });
function setup(responses: Response[]) {
  const fetchImpl = vi.fn(async () => responses.shift()!);
  const session = {
    getAccessToken: vi.fn(async () => ({ access_token: 'tok', expires_at: 9e15 })),
    invalidateAccessToken: vi.fn(),
    endSession: vi.fn(),
  };
  const activity = new CloudActivity({ isAllowed: () => true, origins: cloudOrigins(env), fetchImpl });
  return { api: new AccountApi(env, session as never, activity), fetchImpl, session };
}

it('maps the account to the renderer-safe profile', async () => {
  const { api, fetchImpl } = setup([json(account)]);
  expect(await api.get()).toEqual({
    email: 'ana@example.com', displayName: 'Ana', avatarUrl: null,
    organizations: [{ id: 'Or5tG7hJ9kL1zX3c', slug: 'ana', displayName: 'Ana', role: 'owner', kind: 'personal' }],
  });
  expect(fetchImpl.mock.calls[0][0]).toBe('https://appapi.voicestudio.sh/v1/account');
  expect((fetchImpl.mock.calls[0][1] as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok' });
});

it('refreshes once on 401, then signs out on a second 401', async () => {
  const { api, session } = setup([json({}, 401), json({}, 401)]);
  expect(await api.get()).toBeNull();
  expect(session.invalidateAccessToken).toHaveBeenCalledOnce();
  expect(session.endSession).toHaveBeenCalledWith('signed_out_expired');
});

it('PATCH reuses its Idempotency-Key when retrying after 401', async () => {
  const { api, fetchImpl } = setup([json({}, 401), json(account)]);
  await api.patch({ display_name: 'Ana B' });
  const keys = fetchImpl.mock.calls.map((c) => ((c[1] as RequestInit).headers as Record<string, string>)['idempotency-key']);
  expect(keys[0]).toMatch(/^[0-9a-f]{32}$/);
  expect(keys[1]).toBe(keys[0]);
});

it('returns null without a request when signed out', async () => {
  const { api, fetchImpl, session } = setup([]);
  session.getAccessToken.mockResolvedValueOnce(null as never);
  expect(await api.get()).toBeNull();
  expect(fetchImpl).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd electron && bunx vp test --run src/main/account/account-api.test.ts`
Expected: FAIL, unresolved import.

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/account-api.ts
import { randomBytes } from 'node:crypto';
import type { AccountSession } from './account-session';
import type { CloudActivity } from './cloud-activity';
import type { CloudEnvironment } from './cloud-config';

export interface AccountProfile {
  email: string | null;
  displayName: string | null;
  avatarUrl: string | null;
  organizations: Array<{ id: string; slug: string; displayName: string; role: string; kind: string }>;
}

interface RawAccount {
  email?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
  organizations?: Array<{ id: string; slug: string; display_name: string; role: string; kind: string }>;
}

const toProfile = (a: RawAccount): AccountProfile => ({
  email: a.email ?? null,
  displayName: a.display_name ?? null,
  avatarUrl: a.avatar_url ?? null,
  organizations: (a.organizations ?? []).map((o) => ({
    id: o.id, slug: o.slug, displayName: o.display_name, role: o.role, kind: o.kind,
  })),
});

export class AccountApi {
  constructor(
    private readonly env: CloudEnvironment,
    private readonly session: AccountSession,
    private readonly activity: CloudActivity,
  ) {}

  get(): Promise<AccountProfile | null> {
    return this.call({ method: 'GET' });
  }

  patch(changes: { display_name?: string; theme?: 'system' | 'light' | 'dark'; language?: string }) {
    return this.call({
      method: 'PATCH',
      body: JSON.stringify(changes),
      headers: { 'content-type': 'application/json', 'idempotency-key': randomBytes(16).toString('hex') },
    });
  }

  private async call(init: { method: string; body?: string; headers?: Record<string, string> }) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const token = await this.session.getAccessToken();
      if (!token) return null;
      const res = await this.activity.fetch(this.env.accountApi, {
        ...init,
        headers: { ...init.headers, authorization: `Bearer ${token.access_token}` },
      });
      if (res.status === 401) {
        this.session.invalidateAccessToken();
        continue;
      }
      if (!res.ok) throw new Error(`Account request failed (${res.status})`);
      return toProfile((await res.json()) as RawAccount);
    }
    this.session.endSession('signed_out_expired');
    return null;
  }
}
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd electron && bunx vp test --run src/main/account/account-api.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add electron/src/main/account/account-api.*
git commit -m "feat(account): GET/PATCH /v1/account with refresh-once on 401"
```

---

### Task 8: IPC, preload bridge and main wiring

**Files:**
- Create: `electron/src/main/account/account-ipc.ts`, `account-ipc.test.ts`
- Create: `electron/src/main/account/index.ts`
- Modify: `electron/src/main/ipc.ts:167` (export `assertTrustedMainFrame`)
- Modify: `electron/src/main/index.ts` (after `registerIpc(...)` at about line 255; before `backend.start()` at line 319)
- Modify: `electron/src/preload/index.ts` (add an `account` object next to `backend` at about line 86)
- Modify: `electron/src/preload/index.d.ts` (types next to `BackendConnection`, line 89)

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces:
  - `interface AccountPublicState { available: boolean; accountEnabled: boolean; syncEnabled: boolean; prompted: boolean; remembered: boolean; signedIn: boolean; signingIn: boolean; notice: SessionNotice; profile: AccountProfile | null }`. This type is also exported from `preload/index.d.ts` for the renderer.
  - `ACCOUNT_CHANNELS = { get: 'account:get', signIn: 'account:sign-in', cancelSignIn: 'account:cancel-sign-in', signOut: 'account:sign-out', setSwitches: 'account:set-switches', updateProfile: 'account:update-profile', state: 'account:state' }`
  - `registerAccountIpc(ctl: AccountController, session: AccountSession, getMainWindow: () => BrowserWindow | null): () => void`
  - `class AccountController { constructor(parts: { available: boolean; switches: CloudSwitchStore; session: AccountSession; api: AccountApi; remembered: boolean }); publicState(refreshProfile?: boolean): Promise<AccountPublicState>; setSwitches(raw: unknown): Promise<AccountPublicState>; updateProfile(raw: unknown): Promise<AccountPublicState> }`. It lives in `account-ipc.ts`.
  - `startAccount(opts: { getMainWindow: () => BrowserWindow | null; isRemoteBackend: () => boolean }): Promise<() => void>` (the returned function closes everything). It lives in `account/index.ts`.
  - Renderer bridge: `window.voicestudio.account = { get(): Promise<AccountPublicState>; signIn(): Promise<void>; cancelSignIn(): Promise<void>; signOut(): Promise<void>; setSwitches(p: { accountEnabled?: boolean; syncEnabled?: boolean }): Promise<AccountPublicState>; updateProfile(p: { display_name?: string }): Promise<AccountPublicState>; onState(cb): () => void }`

- [ ] **Step 1: Write the failing test** (no token ever reaches the renderer, and an unavailable build answers inert)

```ts
// electron/src/main/account/account-ipc.test.ts
import { expect, it, vi } from 'vitest';
import { AccountController } from './account-ipc';

const TOKEN = 'eyJ-super-secret-token';
function ctl(over: Partial<ConstructorParameters<typeof AccountController>[0]> = {}) {
  const switches = { accountEnabled: true, syncEnabled: false, prompted: true };
  return new AccountController({
    available: true,
    switches: { get: () => ({ ...switches }), set: vi.fn((p) => Object.assign(switches, p)) } as never,
    session: {
      state: () => ({ signedIn: true, signingIn: false, notice: null }),
      getAccessToken: async () => ({ access_token: TOKEN, expires_at: 1 }),
    } as never,
    api: { get: async () => ({ email: 'ana@example.com', displayName: 'Ana', avatarUrl: null, organizations: [] }) } as never,
    remembered: true,
    ...over,
  });
}

it('the public state never carries a token', async () => {
  const s = await ctl().publicState();
  expect(JSON.stringify(s)).not.toContain(TOKEN);
  expect(Object.keys(s).sort()).toEqual(
    ['accountEnabled', 'available', 'notice', 'profile', 'prompted', 'remembered', 'signedIn', 'signingIn', 'syncEnabled'],
  );
});

it('an unavailable build reports off and makes no account call', async () => {
  const get = vi.fn();
  const s = await ctl({ available: false, api: { get } as never }).publicState();
  expect(s).toMatchObject({ available: false, accountEnabled: false, signedIn: false });
  expect(get).not.toHaveBeenCalled();
});

it('does not fetch the profile while account features are off', async () => {
  const get = vi.fn();
  const c = ctl({ api: { get } as never });
  await c.setSwitches({ accountEnabled: false });
  await c.publicState();
  expect(get).not.toHaveBeenCalled();
});

it('setSwitches validates input and always marks the prompt as answered', async () => {
  const c = ctl();
  await expect(c.setSwitches({ accountEnabled: 'yes' } as never)).rejects.toThrow(/Invalid/);
  const s = await c.setSwitches({ accountEnabled: false });
  expect(s.prompted).toBe(true);
});
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd electron && bunx vp test --run src/main/account/account-ipc.test.ts`
Expected: FAIL, unresolved import.

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/account-ipc.ts
import { BrowserWindow, ipcMain } from 'electron';
import { assertTrustedMainFrame } from '../ipc';
import { sendToLiveWindow } from '../window-safety';
import type { AccountApi, AccountProfile } from './account-api';
import type { AccountSession, SessionNotice } from './account-session';
import type { CloudSwitchStore, CloudSwitches } from './cloud-switches';

export const ACCOUNT_CHANNELS = {
  get: 'account:get',
  signIn: 'account:sign-in',
  cancelSignIn: 'account:cancel-sign-in',
  signOut: 'account:sign-out',
  setSwitches: 'account:set-switches',
  updateProfile: 'account:update-profile',
  state: 'account:state',
} as const;

export interface AccountPublicState {
  available: boolean;
  accountEnabled: boolean;
  syncEnabled: boolean;
  prompted: boolean;
  remembered: boolean;
  signedIn: boolean;
  signingIn: boolean;
  notice: SessionNotice;
  profile: AccountProfile | null;
}

export class AccountController {
  private profile: AccountProfile | null = null;

  constructor(
    private readonly parts: {
      available: boolean;
      switches: CloudSwitchStore;
      session: AccountSession;
      api: AccountApi;
      remembered: boolean;
    },
  ) {}

  async publicState(refreshProfile = false): Promise<AccountPublicState> {
    const { available, switches, session, remembered } = this.parts;
    const sw: CloudSwitches = available ? switches.get() : { accountEnabled: false, syncEnabled: false, prompted: true };
    const s = available && sw.accountEnabled ? session.state() : { signedIn: false, signingIn: false, notice: null };
    if (!s.signedIn) this.profile = null;
    else if (refreshProfile || !this.profile) this.profile = await this.parts.api.get().catch(() => this.profile);
    return { available, ...sw, remembered, ...s, profile: this.profile };
  }

  async setSwitches(raw: unknown): Promise<AccountPublicState> {
    const p = raw as Partial<CloudSwitches>;
    for (const key of ['accountEnabled', 'syncEnabled'] as const)
      if (p?.[key] !== undefined && typeof p[key] !== 'boolean') throw new Error('Invalid switches');
    if (this.parts.available)
      this.parts.switches.set({
        ...(p.accountEnabled !== undefined ? { accountEnabled: p.accountEnabled } : {}),
        ...(p.syncEnabled !== undefined ? { syncEnabled: p.syncEnabled } : {}),
        prompted: true,
      });
    return this.publicState();
  }

  async updateProfile(raw: unknown): Promise<AccountPublicState> {
    const name = (raw as { display_name?: unknown })?.display_name;
    if (typeof name !== 'string' || name.length > 200) throw new Error('Invalid display name');
    this.profile = await this.parts.api.patch({ display_name: name });
    return this.publicState();
  }
}

export function registerAccountIpc(
  ctl: AccountController,
  session: AccountSession,
  getMainWindow: () => BrowserWindow | null,
): () => void {
  // A declared function, not an arrow const: TS2775 forbids calling an assertion through an unannotated const.
  function trusted(event: Electron.IpcMainInvokeEvent): void {
    assertTrustedMainFrame(event, getMainWindow());
  }
  const broadcast = async () => {
    const s = await ctl.publicState();
    for (const win of BrowserWindow.getAllWindows()) sendToLiveWindow(win, ACCOUNT_CHANNELS.state, s);
  };
  const unsubscribe = session.onChange(() => void broadcast());
  ipcMain.handle(ACCOUNT_CHANNELS.get, (e) => (trusted(e), ctl.publicState(true)));
  ipcMain.handle(ACCOUNT_CHANNELS.signIn, (e) => (trusted(e), session.signIn().catch(() => {})));
  ipcMain.handle(ACCOUNT_CHANNELS.cancelSignIn, (e) => (trusted(e), session.cancelSignIn()));
  ipcMain.handle(ACCOUNT_CHANNELS.signOut, async (e) => (trusted(e), await session.signOut()));
  ipcMain.handle(ACCOUNT_CHANNELS.setSwitches, async (e, raw) => {
    trusted(e);
    const s = await ctl.setSwitches(raw);
    void broadcast();
    return s;
  });
  ipcMain.handle(ACCOUNT_CHANNELS.updateProfile, (e, raw) => (trusted(e), ctl.updateProfile(raw)));
  return () => {
    unsubscribe();
    for (const channel of Object.values(ACCOUNT_CHANNELS)) ipcMain.removeHandler(channel);
  };
}
```

In `electron/src/main/ipc.ts`, change `function assertTrustedMainFrame(` to `export function assertTrustedMainFrame(`. Nothing else in that file changes.

The sign-in IPC handler resolves once sign-in finishes or fails. The failure reaches the renderer as `notice: 'sign_in_failed'` in the broadcast state, so the raw error never crosses IPC.

```ts
// electron/src/main/account/index.ts
import { join } from 'node:path';
import { app, safeStorage, shell, type BrowserWindow } from 'electron';
import { AccountApi } from './account-api';
import { AccountController, registerAccountIpc } from './account-ipc';
import { AccountSession } from './account-session';
import { startAccountTokenBridge } from './account-token-bridge';
import { CloudActivity } from './cloud-activity';
import { cloudAccountsAvailable, cloudOrigins, resolveCloudEnvironment } from './cloud-config';
import { CloudSwitchStore } from './cloud-switches';
import { startLoopback } from './loopback';
import { OidcClient } from './oidc';
import { RefreshTokenStore } from './token-store';

/** Wires VoiceStudio Cloud sign-in. Must run before the backend starts (bridge env). */
export async function startAccount(opts: {
  getMainWindow: () => BrowserWindow | null;
  isRemoteBackend: () => boolean;
}): Promise<() => void> {
  const available = cloudAccountsAvailable();
  const env = resolveCloudEnvironment({ packaged: app.isPackaged, requested: process.env.VOICESTUDIO_CLOUD_ENV });
  const switches = new CloudSwitchStore(join(app.getPath('userData'), 'cloud-switches.json'));
  const allowed = () => available && switches.get().accountEnabled;
  const activity = new CloudActivity({ isAllowed: allowed, origins: cloudOrigins(env) });
  const store = new RefreshTokenStore(join(app.getPath('userData'), 'account.json'), safeStorage);
  const oidc = new OidcClient(env, activity);
  const session = new AccountSession({
    oidc, store, activity, switches, startLoopback,
    openExternal: (url) => shell.openExternal(url),
    signedInPage: env.signedInPage,
  });
  const api = new AccountApi(env, session, activity);
  const ctl = new AccountController({ available, switches, session, api, remembered: store.persistent });
  const closeIpc = registerAccountIpc(ctl, session, opts.getMainWindow);
  const bridge = await startAccountTokenBridge({
    getToken: () => session.getAccessToken(),
    isSyncAllowed: () => available && switches.get().syncEnabled,
    isRemoteBackend: opts.isRemoteBackend,
  });
  process.env.VOICESTUDIO_ACCOUNT_BRIDGE_URL = bridge.url;
  process.env.VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN = bridge.token;
  return () => {
    closeIpc();
    activity.stopAll();
    bridge.close();
    delete process.env.VOICESTUDIO_ACCOUNT_BRIDGE_URL;
    delete process.env.VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN;
  };
}
```

`account-token-bridge.ts` is written in Task 9. Implement Task 8 and Task 9 back to back, and run typecheck at the end of Task 9.

In `electron/src/main/index.ts`, right after the `registerIpc(...)` call (about line 255), add:

```ts
      closeAccount = await startAccount({
        getMainWindow: () => mainWindow,
        isRemoteBackend: () => backend.connection.remote,
      });
```

Declare `let closeAccount: (() => void) | undefined;` next to the other `close*` variables (`closeRepairAgents`, `closeDevProxy`). Call `closeAccount?.()` wherever `closeRepairAgents` is called during teardown (grep `closeRepairAgents?.(`). Import `startAccount` from `./account`.

In `electron/src/preload/index.ts`, add next to `backend:` (about line 86):

```ts
  account: {
    get: () => ipcRenderer.invoke('account:get'),
    signIn: () => ipcRenderer.invoke('account:sign-in'),
    cancelSignIn: () => ipcRenderer.invoke('account:cancel-sign-in'),
    signOut: () => ipcRenderer.invoke('account:sign-out'),
    setSwitches: (patch) => ipcRenderer.invoke('account:set-switches', patch),
    updateProfile: (patch) => ipcRenderer.invoke('account:update-profile', patch),
    onState: (cb) => subscribe<AccountPublicState>('account:state', cb),
  },
```

In `electron/src/preload/index.d.ts`, add after `BackendConnection`:

```ts
export interface AccountPublicState {
  available: boolean;
  accountEnabled: boolean;
  syncEnabled: boolean;
  prompted: boolean;
  /** False when the OS keyring is unavailable: sign-in lasts until the app quits. */
  remembered: boolean;
  signedIn: boolean;
  signingIn: boolean;
  notice: 'signed_out_expired' | 'sign_in_failed' | 'offline' | null;
  profile: {
    email: string | null;
    displayName: string | null;
    avatarUrl: string | null;
    organizations: Array<{ id: string; slug: string; displayName: string; role: string; kind: string }>;
  } | null;
}
```

Then add an `account` member with the bridge signature above to `VoiceStudioBridge`.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd electron && bunx vp test --run src/main/account/account-ipc.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit** (after Task 9 Step 4, so typecheck passes)

```bash
git add electron/src/main/account/account-ipc.* electron/src/main/account/index.ts electron/src/main/ipc.ts electron/src/main/index.ts electron/src/preload/index.ts electron/src/preload/index.d.ts
git commit -m "feat(account): token-free IPC state and main-process wiring"
```

---

### Task 9: Backend token bridge

**Files:**
- Create: `electron/src/main/account/account-token-bridge.ts`, `account-token-bridge.test.ts`
- Modify: `electron/src/main/repair-agents.ts:868-869` and `:1021-1022` (strip the new vars too)
- Modify: `electron/src/main/repair-agents.test.ts` (or the closest existing env test; grep `VOICESTUDIO_LLM_AGENT_TOKEN: undefined` in tests)
- Create: `backend/services/cloud_account.py`
- Modify: `backend/main.py` (import `backend.services.cloud_account` before any engine or tool subprocess is spawned, next to the earliest `backend.services` import)
- Test: `tests/test_cloud_account_bridge.py`

**Interfaces:**
- Consumes: `AccessToken` (Task 6).
- Produces:
  - TS: `startAccountTokenBridge(deps: { getToken: () => Promise<AccessToken | null>; isSyncAllowed: () => boolean; isRemoteBackend: () => boolean }): Promise<{ url: string; token: string; close(): void }>`. `GET /token` returns `200 {signed_in: true, access_token, expires_at}` or `200 {signed_in: false}`.
  - Python: `cloud_account.access_token() -> Optional[str]` and `cloud_account.invalidate() -> None`. These are for S1's sync engine.

- [ ] **Step 1: Write the failing tests**

```ts
// electron/src/main/account/account-token-bridge.test.ts
import { expect, it, vi } from 'vitest';
import { startAccountTokenBridge } from './account-token-bridge';

const token = { access_token: 'acc', expires_at: 123 };
async function bridge(over: Partial<Parameters<typeof startAccountTokenBridge>[0]> = {}) {
  return startAccountTokenBridge({
    getToken: vi.fn(async () => token),
    isSyncAllowed: () => true,
    isRemoteBackend: () => false,
    ...over,
  });
}
const get = (b: { url: string; token: string }, headers: Record<string, string> = {}) =>
  fetch(`${b.url}/token`, { headers: { authorization: `Bearer ${b.token}`, ...headers } });

it('hands the backend an access token when sync is on and local', async () => {
  const b = await bridge();
  expect(b.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
  expect(await (await get(b)).json()).toEqual({ signed_in: true, access_token: 'acc', expires_at: 123 });
  b.close();
});

it('answers signed_in:false when sync is off, without asking the session', async () => {
  const getToken = vi.fn();
  const b = await bridge({ isSyncAllowed: () => false, getToken });
  expect(await (await get(b)).json()).toEqual({ signed_in: false });
  expect(getToken).not.toHaveBeenCalled();
  b.close();
});

it('remote mode never sends a token', async () => {
  const getToken = vi.fn();
  const b = await bridge({ isRemoteBackend: () => true, getToken });
  expect(await (await get(b)).json()).toEqual({ signed_in: false });
  expect(getToken).not.toHaveBeenCalled();
  b.close();
});

it('refuses a wrong bearer and any browser Origin', async () => {
  const b = await bridge();
  expect((await fetch(`${b.url}/token`, { headers: { authorization: 'Bearer nope' } })).status).toBe(403);
  expect((await get(b, { origin: 'https://evil.example' })).status).toBe(403);
  b.close();
});

it('a transient refresh failure reads as temporarily signed out, not an error page', async () => {
  const b = await bridge({ getToken: vi.fn(async () => { throw new Error('offline'); }) });
  const res = await get(b);
  expect(res.status).toBe(503);
  expect(await res.json()).toEqual({ signed_in: false, retry: true });
  b.close();
});
```

Add to the repair-agents env test (find the test that asserts `VOICESTUDIO_LLM_AGENT_TOKEN` is stripped, and extend it). If none exists, add a test named `repair agent env strips account bridge vars`. It should spawn through the module's existing test seam with `process.env.VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN = 'x'` and assert that the captured `spawn` env has `VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN` and `VOICESTUDIO_ACCOUNT_BRIDGE_URL` set to `undefined`.

```python
# tests/test_cloud_account_bridge.py
"""The backend's view of the Electron account bridge (account sign-in S0-10)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.services import cloud_account


@pytest.fixture
def bridge():
    calls = []
    answer = {"body": {"signed_in": True, "access_token": "acc", "expires_at": 4_102_444_800_000}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            calls.append(self.headers.get("Authorization"))
            payload = json.dumps(answer["body"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cloud_account._configure(f"http://127.0.0.1:{server.server_port}", "cap")
    yield calls, answer
    server.shutdown()
    cloud_account._configure("", "")


def test_bridge_env_is_removed_from_os_environ(monkeypatch):
    import importlib
    import os

    monkeypatch.setenv("VOICESTUDIO_ACCOUNT_BRIDGE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN", "cap")
    importlib.reload(cloud_account)
    assert "VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN" not in os.environ
    assert "VOICESTUDIO_ACCOUNT_BRIDGE_URL" not in os.environ
    cloud_account._configure("", "")


def test_returns_and_caches_the_token(bridge):
    calls, _ = bridge
    assert cloud_account.access_token() == "acc"
    assert cloud_account.access_token() == "acc"
    assert calls == ["Bearer cap"]


def test_invalidate_forces_a_new_ask(bridge):
    calls, _ = bridge
    cloud_account.access_token()
    cloud_account.invalidate()
    cloud_account.access_token()
    assert len(calls) == 2


def test_signed_out_or_switched_off_is_none(bridge):
    _, answer = bridge
    answer["body"] = {"signed_in": False}
    assert cloud_account.access_token() is None


def test_without_electron_there_is_no_token():
    cloud_account._configure("", "")
    assert cloud_account.access_token() is None


def test_refuses_a_non_loopback_bridge():
    cloud_account._configure("http://example.com:80", "cap")
    assert cloud_account.access_token() is None
    cloud_account._configure("", "")
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd electron && bunx vp test --run src/main/account/account-token-bridge.test.ts` and `uv run pytest tests/test_cloud_account_bridge.py -q`
Expected: both FAIL on missing modules.

- [ ] **Step 3: Implement**

```ts
// electron/src/main/account/account-token-bridge.ts
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import type { AccessToken } from './account-session';

/**
 * Backend-only capability (guide §2 "Giving tokens to the Python backend").
 * The refresh token never leaves main; the backend gets a short-lived access
 * token only while sync is on and the backend is the local one.
 */
export async function startAccountTokenBridge(deps: {
  getToken: () => Promise<AccessToken | null>;
  isSyncAllowed: () => boolean;
  isRemoteBackend: () => boolean;
}): Promise<{ url: string; token: string; close(): void }> {
  const token = randomBytes(32).toString('hex');
  const expected = Buffer.from('Bearer ' + token);
  const server = createServer(async (req, res) => {
    const send = (status: number, data: unknown) => {
      res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
      res.end(JSON.stringify(data));
    };
    const supplied = Buffer.from(req.headers.authorization || '');
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected) || req.headers.origin)
      return send(403, { error: 'Forbidden' });
    if (req.method !== 'GET' || req.url !== '/token') return send(404, { error: 'Not found' });
    if (!deps.isSyncAllowed() || deps.isRemoteBackend()) return send(200, { signed_in: false });
    try {
      const access = await deps.getToken();
      return send(200, access ? { signed_in: true, ...access } : { signed_in: false });
    } catch {
      return send(503, { signed_in: false, retry: true });
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });
  const { port } = server.address() as AddressInfo;
  return { url: `http://127.0.0.1:${port}`, token, close: () => server.close() };
}
```

In `repair-agents.ts`, at both env blocks (lines 868-869 and 1021-1022), add next to the LLM vars:

```ts
          VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN: undefined,
          VOICESTUDIO_ACCOUNT_BRIDGE_URL: undefined,
```

```python
# backend/services/cloud_account.py
"""Short-lived VoiceStudio Cloud access tokens from Electron main.

The refresh token never leaves Electron main. Main answers {"signed_in": false}
whenever account features or sync are switched off, or the backend is remote,
so "no token" here means "make no Cloud call". The capability is removed from
os.environ at import so engine and tool subprocesses never inherit it.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Optional
from urllib.parse import urlsplit

_lock = threading.Lock()
_url = os.environ.pop("VOICESTUDIO_ACCOUNT_BRIDGE_URL", "")
_capability = os.environ.pop("VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN", "")
_cached: Optional[tuple[str, float]] = None


def _configure(url: str, capability: str) -> None:
    """Test seam."""
    global _url, _capability, _cached
    with _lock:
        _url, _capability, _cached = url, capability, None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def access_token() -> Optional[str]:
    global _cached
    with _lock:
        if _cached and _cached[1] - time.time() * 1000 > 30_000:
            return _cached[0]
        target = urlsplit(_url)
        if target.scheme != "http" or target.hostname != "127.0.0.1" or not _capability:
            return None
        request = urllib.request.Request(
            _url + "/token", headers={"Authorization": "Bearer " + _capability}
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=10) as response:
                body = json.loads(response.read(65_536))
        except (OSError, ValueError):
            return None
        if not body.get("signed_in") or not isinstance(body.get("access_token"), str):
            _cached = None
            return None
        _cached = (body["access_token"], float(body.get("expires_at", 0)))
        return _cached[0]


def invalidate() -> None:
    global _cached
    with _lock:
        _cached = None
```

In `backend/main.py`, add `import backend.services.cloud_account  # noqa: F401  (strip bridge env before subprocesses)` beside the earliest `backend.services` import. It must come before the first engine or tool spawn.

- [ ] **Step 4: Run the tests to confirm they pass, then typecheck**

Run: `cd electron && bunx vp test --run src/main/account && bun run typecheck` and `uv run pytest tests/test_cloud_account_bridge.py -q`
Expected: all PASS, and typecheck exits 0.

- [ ] **Step 5: Commit** (commit Task 8 first, then this)

```bash
git add electron/src/main/account/account-token-bridge.* electron/src/main/repair-agents.ts electron/src/main/repair-agents.test.ts backend/services/cloud_account.py backend/main.py tests/test_cloud_account_bridge.py
git commit -m "feat(account): loopback token bridge for the local backend"
```

---

### Task 10: Renderer, first-run choice and Settings → Account

**Files:**
- Create: `electron/src/renderer/src/features/account/use-account.ts`
- Create: `electron/src/renderer/src/components/account-consent.tsx`, `account-consent.test.tsx`
- Create: `electron/src/renderer/src/features/settings/account-settings.tsx`, `account-settings.test.tsx`
- Modify: `electron/src/renderer/src/components/setup-gate.tsx:94-102` (`canContinue`) and `:203-208` (step 2 block)
- Modify: `electron/src/renderer/src/features/settings/settings-page.tsx:95` (`extraSettings`)
- Modify: `electron/src/renderer/src/routes/index.ts` (next to `privacyRoute`, line 216, and in `routeTree`, line 262)
- Modify: all 21 `electron/src/renderer/src/i18n/locales/*.json`

**Interfaces:**
- Consumes: `window.voicestudio.account` and `AccountPublicState` (Task 8), via `getBridge()` from `components/bridge`.
- Produces: `useAccount(): { state: AccountPublicState | undefined; setSwitches; signIn; cancelSignIn; signOut }`; `<AccountConsent onRequirementChange?: (required: boolean) => void />`; `<AccountSettings />`.

English strings. Add them to `en.json` as a top-level `"account"` object. Translate them properly into the other 20 locales (not copied English), then run `bun run locale:check`.

```json
"account": {
  "title": "VoiceStudio account",
  "setup_body": "Optional. Sign in to a free VoiceStudio account to sync settings between your devices. Everything works without one, and nothing is sent unless you turn this on. You can change this anytime in Settings → Account.",
  "use_yes": "Turn on account features",
  "use_no": "Keep everything local",
  "features": "Account features",
  "features_desc": "Off means the app never contacts VoiceStudio Cloud.",
  "sync": "Cloud sync",
  "sync_desc": "Off stops all syncing at once. Nothing on this device is deleted.",
  "sign_in": "Sign in",
  "continue_in_browser": "Continue in your browser…",
  "cancel": "Cancel",
  "sign_out": "Sign out",
  "signed_in_as": "Signed in as {{name}}",
  "signed_out_expired": "You were signed out. Sign in to keep syncing.",
  "sign_in_failed": "Sign-in was cancelled or failed",
  "offline": "Offline: changes will sync later",
  "not_remembered": "Your system keyring is unavailable, so you will need to sign in again after VoiceStudio restarts."
}
```

- [ ] **Step 1: Write the failing tests**

Both tests use the mocking style of `credential-settings.test.tsx`: `t` returns the key, and `getBridge` is hoisted-mocked.

```tsx
// electron/src/renderer/src/components/account-consent.test.tsx
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ bridge: null as unknown }));
vi.mock('./bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { AccountConsent } from './account-consent';

const base = {
  available: true, accountEnabled: false, syncEnabled: false, prompted: false, remembered: true,
  signedIn: false, signingIn: false, notice: null, profile: null,
};
function bridgeWith(state: Partial<typeof base>) {
  const account = {
    get: vi.fn(async () => ({ ...base, ...state })),
    onState: vi.fn(() => () => {}),
    setSwitches: vi.fn(async (p: object) => ({ ...base, ...state, ...p, prompted: true })),
    signIn: vi.fn(), cancelSignIn: vi.fn(), signOut: vi.fn(),
  };
  mock.bridge = { account };
  return account;
}
afterEach(() => { cleanup(); mock.bridge = null; });

it('asks with two equal Yes/No buttons when unprompted', async () => {
  bridgeWith({});
  const required = vi.fn();
  render(<AccountConsent onRequirementChange={required} />);
  const yes = await screen.findByText('account.use_yes');
  const no = screen.getByText('account.use_no');
  expect(yes.className).toBe(no.className);
  await waitFor(() => expect(required).toHaveBeenLastCalledWith(true));
});

it('No turns everything off', async () => {
  const account = bridgeWith({});
  render(<AccountConsent />);
  fireEvent.click(await screen.findByText('account.use_no'));
  expect(account.setSwitches).toHaveBeenCalledWith({ accountEnabled: false, syncEnabled: false });
});

it('Yes turns account features on and leaves sync off', async () => {
  const account = bridgeWith({});
  render(<AccountConsent />);
  fireEvent.click(await screen.findByText('account.use_yes'));
  expect(account.setSwitches).toHaveBeenCalledWith({ accountEnabled: true });
});

it('requires nothing when unavailable or already answered', async () => {
  for (const state of [{ available: false }, { prompted: true }]) {
    bridgeWith(state);
    const required = vi.fn();
    const { container } = render(<AccountConsent onRequirementChange={required} />);
    await waitFor(() => expect(required).toHaveBeenLastCalledWith(false));
    expect(container).toBeEmptyDOMElement();
    cleanup();
  }
});

it('requires nothing in the browser build (no bridge)', async () => {
  const required = vi.fn();
  render(<AccountConsent onRequirementChange={required} />);
  await waitFor(() => expect(required).toHaveBeenLastCalledWith(false));
});
```

```tsx
// electron/src/renderer/src/features/settings/account-settings.test.tsx
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ bridge: null as unknown }));
vi.mock('@/components/bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { AccountSettings } from './account-settings';

const base = {
  available: true, accountEnabled: true, syncEnabled: false, prompted: true, remembered: true,
  signedIn: false, signingIn: false, notice: null as string | null, profile: null as unknown,
};
function setup(state: Partial<typeof base>) {
  let push: (s: unknown) => void = () => {};
  const account = {
    get: vi.fn(async () => ({ ...base, ...state })),
    onState: vi.fn((cb: (s: unknown) => void) => ((push = cb), () => {})),
    setSwitches: vi.fn(async (p: object) => ({ ...base, ...state, ...p })),
    signIn: vi.fn(async () => {}), cancelSignIn: vi.fn(async () => {}), signOut: vi.fn(async () => {}),
  };
  mock.bridge = { account };
  render(<AccountSettings />);
  return { account, push: (s: Partial<typeof base>) => act(() => push({ ...base, ...s })) };
}
afterEach(() => { cleanup(); mock.bridge = null; });

it('turning account features off applies at once, with no confirm step', async () => {
  const { account } = setup({});
  fireEvent.click(await screen.findByRole('switch', { name: 'account.features' }));
  expect(account.setSwitches).toHaveBeenCalledWith({ accountEnabled: false });
});

it('the sync switch is disabled until signed in', async () => {
  setup({});
  expect(await screen.findByRole('switch', { name: 'account.sync' })).toBeDisabled();
});

it('signing in shows the browser hint with a working Cancel', async () => {
  const { account, push } = setup({});
  fireEvent.click(await screen.findByText('account.sign_in'));
  expect(account.signIn).toHaveBeenCalled();
  push({ signingIn: true });
  expect(screen.getByText('account.continue_in_browser')).toBeInTheDocument();
  fireEvent.click(screen.getByText('account.cancel'));
  expect(account.cancelSignIn).toHaveBeenCalled();
});

it.each(['signed_out_expired', 'sign_in_failed', 'offline'])('shows the %s notice', async (notice) => {
  setup({ notice });
  expect(await screen.findByText(`account.${notice}`)).toBeInTheDocument();
});

it('explains a session that will not survive a restart', async () => {
  setup({ remembered: false });
  expect(await screen.findByText('account.not_remembered')).toBeInTheDocument();
});

it('signed in: shows the name and Sign out, and never any token-like text', async () => {
  const { account } = setup({
    signedIn: true,
    profile: { email: 'ana@example.com', displayName: 'Ana', avatarUrl: null, organizations: [] },
  });
  fireEvent.click(await screen.findByText('account.sign_out'));
  expect(account.signOut).toHaveBeenCalled();
  expect(document.body.textContent).not.toMatch(/Bearer|eyJ/);
});

it('renders nothing when the build has accounts unavailable', async () => {
  setup({ available: false });
  await act(async () => {});
  expect(screen.queryByRole('switch')).toBeNull();
});
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd electron && bunx vp test --run src/renderer/src/components/account-consent.test.tsx src/renderer/src/features/settings/account-settings.test.tsx`
Expected: FAIL, unresolved imports.

- [ ] **Step 3: Implement**

```ts
// electron/src/renderer/src/features/account/use-account.ts
import { useEffect, useState } from 'react';
import { getBridge } from '@/components/bridge';
import type { AccountPublicState } from '../../../../preload/index.d';

export function useAccount() {
  const account = getBridge()?.account;
  const [state, setState] = useState<AccountPublicState | undefined>();
  useEffect(() => {
    if (!account) return;
    let live = true;
    void account.get().then((s) => live && setState(s)).catch(() => {});
    const off = account.onState((s) => live && setState(s));
    return () => { live = false; off(); };
  }, [account]);
  return {
    supported: Boolean(account),
    state,
    setSwitches: async (p: { accountEnabled?: boolean; syncEnabled?: boolean }) =>
      account && setState(await account.setSwitches(p)),
    signIn: () => account?.signIn(),
    cancelSignIn: () => account?.cancelSignIn(),
    signOut: () => account?.signOut(),
  };
}
```

```tsx
// electron/src/renderer/src/components/account-consent.tsx
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useAccount } from '@/features/account/use-account';

/** First-run choice (owner rule 2026-10-16): equal-weight Yes/No, skipping = off. */
export function AccountConsent({ onRequirementChange }: { onRequirementChange?: (required: boolean) => void }) {
  const { t } = useTranslation();
  const { supported, state, setSwitches } = useAccount();
  const required = Boolean(supported && state?.available && !state.prompted);
  useEffect(() => {
    if (!supported || state) onRequirementChange?.(required);
  }, [required, supported, state, onRequirementChange]);
  if (!required) return null;
  return (
    <section className="space-y-3">
      <h2 className="text-base font-medium">{t('account.title')}</h2>
      <p className="text-sm leading-6 text-muted-foreground">{t('account.setup_body')}</p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => void setSwitches({ accountEnabled: true })}>
          {t('account.use_yes')}
        </Button>
        <Button type="button" variant="outline" onClick={() => void setSwitches({ accountEnabled: false, syncEnabled: false })}>
          {t('account.use_no')}
        </Button>
      </div>
    </section>
  );
}
```

In `setup-gate.tsx`:
- Add `const [accountChoiceRequired, setAccountChoiceRequired] = useState(false);` next to `consentRequired`.
- Change the step-2 branch of `canContinue` to `? !consentRequired && !accountChoiceRequired`.
- In the `step === 2` block, render `<AccountConsent onRequirementChange={setAccountChoiceRequired} />` after `<AnalyticsConsent … />`.

This puts the choice in the existing privacy step, so no step indices change.

Existing installs never see setup again, so their switches stay at the default (off) until they visit Settings → Account. That matches "never default-on".

```tsx
// electron/src/renderer/src/features/settings/account-settings.tsx
import { UserRoundIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { useAccount } from '../account/use-account';
import { SettingsRow, SettingsSection } from './settings-layout';

export function AccountSettings() {
  const { t } = useTranslation();
  const { supported, state, setSwitches, signIn, cancelSignIn, signOut } = useAccount();
  if (!supported || !state?.available) return null;
  const who = state.profile?.displayName ?? state.profile?.email ?? '';
  return (
    <SettingsSection icon={UserRoundIcon} title={t('account.title')}>
      <SettingsRow id="account-features" title={t('account.features')} description={t('account.features_desc')}>
        <Switch
          aria-label={t('account.features')}
          checked={state.accountEnabled}
          onCheckedChange={(on) => void setSwitches({ accountEnabled: on })}
        />
      </SettingsRow>
      {state.accountEnabled && (
        <>
          <SettingsRow id="account-session" title={state.signedIn ? t('account.signed_in_as', { name: who }) : t('account.title')}>
            {state.signedIn ? (
              <Button variant="outline" onClick={() => void signOut()}>{t('account.sign_out')}</Button>
            ) : state.signingIn ? (
              <span className="flex items-center gap-2 text-sm text-muted-foreground">
                {t('account.continue_in_browser')}
                <Button variant="ghost" onClick={() => void cancelSignIn()}>{t('account.cancel')}</Button>
              </span>
            ) : (
              <Button variant="outline" onClick={() => void signIn()}>{t('account.sign_in')}</Button>
            )}
          </SettingsRow>
          <SettingsRow id="account-sync" title={t('account.sync')} description={t('account.sync_desc')}>
            <Switch
              aria-label={t('account.sync')}
              checked={state.syncEnabled}
              disabled={!state.signedIn}
              onCheckedChange={(on) => void setSwitches({ syncEnabled: on })}
            />
          </SettingsRow>
          {state.notice && <p role="status" className="text-sm text-muted-foreground">{t(`account.${state.notice}`)}</p>}
          {!state.remembered && <p className="text-sm text-muted-foreground">{t('account.not_remembered')}</p>}
        </>
      )}
    </SettingsSection>
  );
}
```

Two checks while implementing. First, `SettingsSection` children may need to be `SettingsRow`s only. If the notice `<p>`s break the layout, wrap them in a `SettingsRow id="account-status" title="" titleHidden`. Second, confirm that the Switch component renders `role="switch"`. If it doesn't, change the tests' `findByRole('switch', ...)` to `findByLabelText(...)`.

Build it with the same primitives that `privacy-settings.tsx` imports: `SettingsContent`, `SettingsSection` and `SettingsRow` from `./settings-layout`, plus the `Switch` component it uses. Copy its import lines exactly.

Register it in `settings-page.tsx` `extraSettings`, next to the privacy entry:

```ts
  { to: '/settings/account', label: 'account.title', Icon: UserRoundIcon,
    Component: AccountSettings, fields: ['account.title', 'account.features', 'account.sync', 'account.sign_in'] },
```

Hide the entry when the bridge is absent or `available` is false. Read how `extraSettings` entries are filtered, and if there is an existing `visible`/`hidden` predicate, use it. Otherwise filter in place with `useAccount().state?.available`.

Add `accountRoute` in `routes/index.ts`, copying `privacyRoute` with `path: '/settings/account'`, and add it to `routeTree`.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd electron && bunx vp test --run src/renderer/src/components/account-consent.test.tsx src/renderer/src/features/settings/account-settings.test.tsx && bun run locale:check && bun run typecheck`
Expected: PASS, and both checks exit 0.

- [ ] **Step 5: Commit**

```bash
git add electron/src/renderer electron/src/preload
git commit -m "feat(account): first-run account choice and Settings → Account"
```

---

### Task 11: Docs, sanctioned-calls list, changelog and full verification

**Files:**
- Modify: `.github/CONTRIBUTING.md` (Local-first list, about line 363)
- Modify: `CLAUDE.md` (Constraints → Local-first "Sanctioned calls"), and `AGENTS.md` if it carries the same list (`grep -n "Sanctioned" AGENTS.md`)
- Modify: `CHANGELOG.md` (`## [Unreleased]`)
- Modify: `account_login/HANDOFF.md` ("Open items for the owner"; local only, git-ignored)
- Check: `grep -rn -i "no account" README.md docs/install docs/*.md`

- [ ] **Step 1: Add the sanctioned call** (wording needs owner approval before merge). In `.github/CONTRIBUTING.md`, after the yt-dlp bullet:

```markdown
  - **VoiceStudio Cloud account features, only after the user turns them on**
    (first-run setup or Settings → Account; off by default, skipping = off):
    browser sign-in against `voicestudio.sh/identity` and the account API at
    `appapi.voicestudio.sh`, made only from Electron main
    (`electron/src/main/account/`). Turning account features off stops every
    request at once and sends one best-effort token revocation.
```

Mirror the same sentence into CLAUDE.md's Local-first "Sanctioned calls" list, and into AGENTS.md if it has one.

- [ ] **Step 2: Fix any docs this makes false.** Grep the docs for "no account" and "no accounts". Where a sentence says the app needs no account, reword it to "no account required". This claim stays true, because the account is optional and off by default. Leave the meaning of everything else alone. Ship-dark note: the build flag hides the feature in release builds, so user-facing install docs gain no new steps yet.

- [ ] **Step 3: Changelog.** Under `## [Unreleased]` → `### Added`, add one line:

```markdown
- Optional VoiceStudio account sign-in groundwork (off by default, hidden in release builds until VoiceStudio Cloud launches); account features and cloud sync can be switched off at setup or anytime
```

- [ ] **Step 4: Record the contract discrepancies for vssaas.** In `account_login/HANDOFF.md` → "Open items for the owner", add:

```markdown
- Contract mismatches to fix in `vssaas`: a wrong callback `state` (guide
  §2 step 3 "keep waiting" vs `desktop-account-client.md` step 4 "stop
  listening"; the app keeps waiting), and the account API host
  (`appapi.voicestudio.sh/v1/account` in the guide vs `voicestudio.sh/v1/account`
  in `desktop-account-client.md`; the app uses `appapi`).
```

- [ ] **Step 5: Run full verification**

Run each of these and confirm it exits 0:
```bash
cd electron && bun run test && bun run typecheck && bun run lint && bun run format:check
cd .. && uv run pytest tests/test_cloud_account_bridge.py tests/test_no_hardcoded_cjk.py tests/test_locale_parity.py tests/test_app_version.py -q
bun install --frozen-lockfile
```

- [ ] **Step 6: Manual check against real Keycloak.** Use a developer build with `VOICESTUDIO_CLOUD_ENV=dev`, or `local` with `vssaas` `make dev`.
  1. Fresh profile → setup privacy step shows the account choice. "Keep everything local" → Settings → Account shows features off. Confirm in the devtools Network panel and with `lsof -i -P | grep -i electron` that no connection goes to `voicestudio.sh`.
  2. Turn features on → Sign in → the browser opens → finish sign-in → the "You can return" page appears, and the app shows "Signed in as …".
  3. Restart → still signed in. On Linux without a keyring, the `not_remembered` note shows instead.
  4. Start a sign-in, then turn features off before finishing → the browser's callback fails to connect, and the app is signed out.
  5. While signed in, turn features off → the revocation shows in the Keycloak admin console's sessions, `userData/account.json` is gone, and no further requests are made.
  6. Repeat steps 1–5 on macOS, Windows and Linux. That is the parity rule.

- [ ] **Step 7: Commit**

```bash
git add .github/CONTRIBUTING.md CLAUDE.md AGENTS.md CHANGELOG.md README.md docs
git commit -m "docs(account): sanction opt-in VoiceStudio Cloud calls and note the account switches"
```

---

## Follow-on plans (written after this lands)

1. **S1 settings sync:** `sync_allowlist`, change hooks (`prefs.set`, `settings_store.set`, `syncedStorage`), alembic `sync_state`/`sync_items`/`sync_outbox`, Base62 IDs, `merge3`, the pull/push loop against the Prism mock (`npx @stoplight/prism-cli@5.12.0 mock account_login/sync-v1.yaml --port 8090`), an SSE client, and "Choose what to sync". The sync engine gets its token only from `cloud_account.access_token()`, which already returns `None` when sync is off. S1 adds a main→backend "sync stopped" push, so that the backend also closes its SSE stream and outbox at once when the switch flips.
2. **S2 prep:** `sync_id` on `voice_profiles`, consent capture in the voice UI, and `VSB1` encrypt/decrypt with tests.
3. **VoiceStudioPro:** port this plan with `voicestudio-desktop-pro`, then "Sign in to activate" (S0-12) and the Cloud transport in `data_sync` (S1-29).
