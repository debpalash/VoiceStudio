import { createHash, randomBytes } from 'node:crypto';
import { ACCOUNT_CONFIG } from '../../../../config/account-config.mjs';
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
  /** Absent when it equals the requested scope (RFC 6749 section 5.1). */
  scope?: string;
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

export function createPkce(): {
  state: string;
  verifier: string;
  challenge: string;
} {
  const verifier = base64url(randomBytes(64));
  return {
    state: base64url(randomBytes(32)),
    verifier,
    challenge: challengeFor(verifier),
  };
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
    const doc = (await res.json()) as Partial<OidcEndpoints> & {
      issuer?: string;
    };
    if (doc.issuer !== this.env.issuer) throw new Error('Discovery issuer does not match');
    const endpoints = {
      authorization_endpoint: String(doc.authorization_endpoint),
      token_endpoint: String(doc.token_endpoint),
      revocation_endpoint: String(doc.revocation_endpoint),
    };
    this.cached = { at: this.now(), endpoints };
    return endpoints;
  }

  authorizeUrl(
    _e: OidcEndpoints,
    redirectUri: string,
    p: { state: string; challenge: string },
    intent: 'login' | 'signup' = 'login',
  ): string {
    const url = new URL(`${this.env.dashboard}/auth/app`);
    url.search = new URLSearchParams({
      client_id: this.env.clientId,
      response_type: 'code',
      redirect_uri: redirectUri,
      scope: 'openid offline_access',
      max_age: String(ACCOUNT_CONFIG.browserSessionMaxAgeSeconds),
      prompt: intent === 'signup' ? 'create consent' : 'consent',
      state: p.state,
      code_challenge: p.challenge,
      code_challenge_method: 'S256',
    }).toString();
    return url.toString();
  }

  exchangeCode(code: string, redirectUri: string, verifier: string): Promise<TokenResponse> {
    return this.token(
      {
        grant_type: 'authorization_code',
        client_id: this.env.clientId,
        code,
        redirect_uri: redirectUri,
        code_verifier: verifier,
      },
      `${this.env.dashboard}/auth/app/token`,
    );
  }

  refresh(refreshToken: string): Promise<TokenResponse> {
    return this.token({
      grant_type: 'refresh_token',
      client_id: this.env.clientId,
      refresh_token: refreshToken,
    });
  }

  /** Explicit sign-out requires acknowledgement before deleting the stored token. */
  async revoke(refreshToken: string): Promise<void> {
    const { revocation_endpoint } = await this.discover();
    const response = await this.activity.fetch(
      revocation_endpoint,
      this.form({
        client_id: this.env.clientId,
        token: refreshToken,
        token_type_hint: 'refresh_token',
      }),
      { final: true, timeoutMs: 5_000 },
    );
    if (!response.ok) throw new TransientAuthError(response.status);
  }

  private form(fields: Record<string, string>): RequestInit {
    return {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(fields).toString(),
    };
  }

  private async token(fields: Record<string, string>, endpoint?: string): Promise<TokenResponse> {
    const tokenEndpoint = endpoint ?? (await this.discover()).token_endpoint;
    let res: Response;
    try {
      res = await this.activity.fetch(tokenEndpoint, this.form(fields));
    } catch (error) {
      if ((error as Error).name === 'CloudDisabledError') throw error;
      throw new TransientAuthError();
    }
    const body = (await res.json().catch(() => ({}))) as Partial<TokenResponse> & {
      error?: string;
    };
    if (res.status === 400 && body.error === 'invalid_grant') throw new InvalidGrantError();
    if (!res.ok) throw new TransientAuthError(res.status);
    // An absent scope means the requested scopes were granted (RFC 6749
    // section 5.1; Zitadel never sends it). A present one that dropped
    // offline_access means no lasting session was granted.
    if (body.scope !== undefined && !String(body.scope).split(' ').includes('offline_access'))
      throw new Error('Token response lacks offline_access');
    if (typeof body.refresh_token !== 'string' || body.refresh_token === '')
      throw new Error('Token response lacks a refresh token');
    return body as TokenResponse;
  }
}
