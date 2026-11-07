import { parseSignInOptions } from './sign-in-options';
import type { AccountSignInOptions } from '../../preload/index.d';
import { CloudDisabledError, type CloudActivity } from './cloud-activity';
import type { CloudSwitchStore } from './cloud-switches';
import type { startLoopback as StartLoopback } from './loopback';
import {
  InvalidGrantError,
  TransientAuthError,
  createPkce,
  type OidcClient,
  type TokenResponse,
} from './oidc';
import type { RefreshTokenStore } from './token-store';

export type SessionNotice = 'signed_out_expired' | 'sign_in_failed' | 'offline' | null;
export interface SessionState {
  signedIn: boolean;
  signingIn: boolean;
  notice: SessionNotice;
}
export interface AccessToken {
  access_token: string;
  expires_at: number;
}

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

export class SignInCancelled extends Error {
  constructor() {
    super('Sign-in was cancelled');
    this.name = 'SignInCancelled';
  }
}

export const backoffDelay = (attempt: number): number => Math.min(300_000, 1000 * 2 ** attempt);

const REFRESH_MARGIN_MS = 60_000;

/** Longest a sign-out waits for a token refresh and revocation before forgetting locally. */
export const SIGN_OUT_DEADLINE_MS = 5_000;

function withinDeadline(work: Promise<unknown>, ms: number): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<void>((resolve) => (timer = setTimeout(resolve, ms)));
  return Promise.race([
    work.then(
      () => undefined,
      () => undefined,
    ),
    deadline,
  ]).finally(() => clearTimeout(timer));
}

export class AccountSession {
  private access: AccessToken | null = null;
  /** Bumped whenever a session ends, so a token request already in flight is discarded. */
  private generation = 0;
  private inflight: Promise<AccessToken | null> | null = null;
  private logout: Promise<void> | null = null;
  private closeLoopback: (() => void) | null = null;
  private signingIn = false;
  private attempt = 0;
  private notice: SessionNotice = null;
  private readonly listeners = new Set<(s: SessionState) => void>();
  private readonly now: () => number;

  constructor(private readonly deps: SessionDeps) {
    this.now = deps.now ?? Date.now;
    // Off means signed out: never keep a credential the user cannot see (e.g. after a
    // corrupt switches file reset to off).
    if (!deps.switches.get().accountEnabled) deps.store.clear();
    deps.activity.onStop(() => this.closeLoopback?.());
    deps.switches.subscribe((next, prev) => {
      if (prev.accountEnabled && !next.accountEnabled) void this.turnedOff();
    });
  }

  /** Invalidates asynchronous profile reads after sign-out or account switch-off. */
  identityGeneration(): number {
    return this.generation;
  }

  /** Only authenticated profile responses may supply this platform User ID. */
  bindIdentity(accountId: unknown, generation: number): boolean {
    if (
      generation !== this.generation ||
      !this.deps.switches.get().accountEnabled ||
      typeof accountId !== 'string'
    )
      return false;
    const previous = this.accountId();
    const bound = this.deps.store.bindAccount(accountId);
    if (bound && previous !== accountId) this.emit();
    return bound;
  }

  accountId(): string | null {
    return this.deps.switches.get().accountEnabled ? this.deps.store.accountId() : null;
  }

  state(): SessionState {
    return {
      signedIn: this.deps.store.load() !== null,
      signingIn: this.signingIn,
      notice: this.notice,
    };
  }

  onChange(cb: (s: SessionState) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  async signIn(options?: AccountSignInOptions): Promise<void> {
    if (this.logout) throw new TransientAuthError();
    const { intent } = parseSignInOptions(options);
    if (!this.deps.switches.get().accountEnabled) throw new CloudDisabledError();
    if (this.signingIn) return;
    const pkce = createPkce();
    const generation = this.generation;
    const attempt = ++this.attempt;
    // Cancel (or switching off) invalidates this attempt: checked after every awaited step.
    const stillWanted = () => {
      if (attempt !== this.attempt) throw new SignInCancelled();
      if (!this.deps.switches.get().accountEnabled) throw new CloudDisabledError();
    };
    this.signingIn = true;
    this.notice = null;
    this.emit();
    try {
      const endpoints = await this.deps.oidc.discover();
      stillWanted();
      const lb = await this.deps.startLoopback({
        expectedState: pkce.state,
        signedInPage: this.deps.signedInPage,
      });
      this.closeLoopback = lb.close;
      stillWanted();
      await this.deps.openExternal(
        this.deps.oidc.authorizeUrl(endpoints, lb.redirectUri, pkce, intent),
      );
      const code = await lb.code;
      stillWanted();
      const tokens = await this.deps.oidc.exchangeCode(code, lb.redirectUri, pkce.verifier);
      stillWanted();
      if (!this.accept(tokens, generation, true)) throw new CloudDisabledError();
    } catch (error) {
      const cancelled =
        error instanceof CloudDisabledError ||
        error instanceof SignInCancelled ||
        attempt !== this.attempt;
      if (!cancelled) this.notice = 'sign_in_failed';
      throw error;
    } finally {
      this.closeLoopback?.();
      this.closeLoopback = null;
      this.signingIn = false;
      this.emit();
    }
  }

  cancelSignIn(): void {
    this.attempt += 1; // whatever step the attempt is on, its result is dropped
    this.closeLoopback?.();
  }

  signOut(): Promise<void> {
    if (this.logout) return this.logout;
    this.cancelSignIn();
    const generation = this.generation;
    this.logout = (async () => {
      // Network work is capped so sign-out always finishes promptly, even on a dead network.
      await withinDeadline(
        (async () => {
          // Let an in-flight refresh settle so the newest refresh token is the one revoked.
          if (this.inflight) await this.inflight.catch(() => undefined);
          if (this.generation !== generation) return;
          const refresh = this.deps.store.load();
          // Best effort: signing out always forgets the session on this device, even offline.
          if (refresh) await this.deps.oidc.revoke(refresh).catch(() => undefined);
        })(),
        SIGN_OUT_DEADLINE_MS,
      );
      // Another sign-out, switch-off or sign-in already replaced this session.
      if (this.generation === generation) this.endSession(null);
    })().finally(() => {
      this.logout = null;
    });
    return this.logout;
  }

  /** Single-flight; null means signed out or switched off. */
  getAccessToken(): Promise<AccessToken | null> {
    if (this.logout) return Promise.reject(new TransientAuthError());
    if (!this.deps.switches.get().accountEnabled) return Promise.resolve(null);
    if (this.access && this.access.expires_at - this.now() >= REFRESH_MARGIN_MS)
      return Promise.resolve(this.access);
    if (!this.inflight) {
      const request = this.refresh().finally(() => {
        if (this.inflight === request) this.inflight = null;
      });
      this.inflight = request;
    }
    return this.inflight;
  }

  invalidateAccessToken(): void {
    this.access = null;
  }

  endSession(notice: SessionNotice): void {
    this.generation += 1;
    this.access = null;
    this.inflight = null;
    try {
      this.deps.store.clear();
    } finally {
      this.notice = notice;
      this.emit();
    }
  }

  private async refresh(): Promise<AccessToken | null> {
    const generation = this.generation;
    const refresh = this.deps.store.load();
    if (!refresh) return null;
    try {
      if (!this.accept(await this.deps.oidc.refresh(refresh), generation)) return null;
      if (this.notice === 'offline') {
        this.notice = null;
        this.emit();
      }
      return this.access;
    } catch (error) {
      if (generation !== this.generation) return null;
      if (error instanceof InvalidGrantError) {
        this.endSession('signed_out_expired');
        return null;
      }
      if (error instanceof CloudDisabledError) return null;
      if (error instanceof TransientAuthError) {
        this.notice = 'offline';
        this.emit();
      }
      throw error;
    }
  }

  /** Stores the tokens unless the session ended (sign-out or switch-off) since `generation`. */
  private accept(tokens: TokenResponse, generation: number, newSession = false): boolean {
    if (generation !== this.generation || !this.deps.switches.get().accountEnabled) return false;
    if (newSession) {
      this.generation += 1;
      this.inflight = null;
    }
    this.deps.store.save(tokens.refresh_token, { newSession });
    this.access = {
      access_token: tokens.access_token,
      expires_at: this.now() + tokens.expires_in * 1000,
    };
    return true;
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
