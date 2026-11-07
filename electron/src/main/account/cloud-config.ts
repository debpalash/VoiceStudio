/**
 * VoiceStudio Cloud endpoints, one block per environment in `config/account.json`.
 * Interactive sign-in starts on the dashboard, which owns session freshness and approval.
 * Zitadel remains the issuer for app token refresh and revocation.
 */
import { ACCOUNT_CONFIG } from '../../../../config/account-config.mjs';
export type CloudEnvName = 'prod' | 'dev' | 'local';

export interface CloudEnvironment {
  name: CloudEnvName;
  issuer: string;
  accountApi: string;
  syncApi: string;
  /** The web dashboard: the browser lands here after sign-in, and the account screen links to it. */
  dashboard: string;
  signedInPage: string;
  clientId: string;
}

const ENVIRONMENTS = ACCOUNT_CONFIG.environments;

export function resolveCloudEnvironment(opts: {
  packaged: boolean;
  requested?: string;
  /** VOICESTUDIO_CLOUD_CLIENT_ID; honoured in developer builds only. */
  clientId?: string;
}): CloudEnvironment {
  const requested = opts.requested as CloudEnvName | undefined;
  const name: CloudEnvName = opts.packaged
    ? 'prod'
    : requested && Object.hasOwn(ENVIRONMENTS, requested)
      ? requested
      : 'prod';
  const { clientId, ...endpoints } = ENVIRONMENTS[name];
  const override = opts.packaged ? '' : (opts.clientId?.trim() ?? '');
  return {
    name,
    ...endpoints,
    signedInPage: `${endpoints.dashboard}/desktop-signed-in`,
    clientId: override || clientId,
  };
}

/** Only the configured first-party identity, account and dashboard origins are allowed. */
export function cloudOrigins(env: CloudEnvironment): Set<string> {
  return new Set([
    new URL(env.issuer).origin,
    new URL(env.accountApi).origin,
    new URL(env.dashboard).origin,
  ]);
}

/** The dashboard pages the account screen opens in the browser. */
export function dashboardLinks(env: CloudEnvironment): {
  profile: string;
  plan: string;
  deletion: string;
} {
  return {
    profile: `${env.dashboard}/settings/profile`,
    plan: `${env.dashboard}/settings/plan`,
    deletion: `${env.dashboard}/settings/deletion-review`,
  };
}

/**
 * Sign-in is offered wherever the environment has a client to sign in with: production and
 * dev always do; a local stack needs VOICESTUDIO_CLOUD_CLIENT_ID. Each user still turns
 * accounts on themselves (off by default), and nothing contacts VoiceStudio Cloud until then.
 */
export function cloudAccountsAvailable(env: CloudEnvironment): boolean {
  return env.clientId !== '';
}
