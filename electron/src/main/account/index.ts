import { join } from 'node:path';
import { app, safeStorage, shell, type BrowserWindow } from 'electron';
import { AccountApi } from './account-api';
import { AvatarCache } from './avatar-cache';
import { AccountController, registerAccountIpc } from './account-ipc';
import { AccountSession } from './account-session';
import { startAccountTokenBridge } from './account-token-bridge';
import { CloudActivity } from './cloud-activity';
import {
  cloudAccountsAvailable,
  cloudOrigins,
  dashboardLinks,
  resolveCloudEnvironment,
} from './cloud-config';
import { CloudSwitchStore } from './cloud-switches';
import { startLoopback } from './loopback';
import { OidcClient } from './oidc';
import { RefreshTokenStore } from './token-store';
import { setBackendOnlyEnv } from '../backend';

interface StartOptions {
  getMainWindow: () => BrowserWindow | null;
  isRemoteBackend: () => boolean;
  /** POST to the local backend (never a remote one); errors are the caller's to swallow. */
  notifyBackend?: (path: '/api/cloud-sync/start' | '/api/cloud-sync/stop') => Promise<void>;
}

const BRIDGE_ENV_OFF = {
  VOICESTUDIO_ACCOUNT_BRIDGE_URL: undefined,
  VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN: undefined,
};

/**
 * Wires VoiceStudio Cloud sign-in. Must run before the backend starts (bridge env).
 * Never throws: an optional feature must not stop the local app from starting.
 */
export async function startAccount(opts: StartOptions): Promise<() => void> {
  try {
    return await wireAccount(opts);
  } catch (error) {
    console.error('[account] disabled for this run:', (error as Error).name);
    return () => {};
  }
}

async function wireAccount(opts: StartOptions): Promise<() => void> {
  const env = resolveCloudEnvironment({
    packaged: app.isPackaged,
    requested: process.env.VOICESTUDIO_CLOUD_ENV,
    clientId: process.env.VOICESTUDIO_CLOUD_CLIENT_ID,
  });
  const available = cloudAccountsAvailable(env);
  const switches = new CloudSwitchStore(join(app.getPath('userData'), 'cloud-switches.json'));
  const allowed = () => available && switches.get().accountEnabled;
  const activity = new CloudActivity({ isAllowed: allowed, origins: cloudOrigins(env) });
  const store = new RefreshTokenStore(join(app.getPath('userData'), 'account.json'), safeStorage);
  const oidc = new OidcClient(env, activity);
  const session = new AccountSession({
    oidc,
    store,
    activity,
    switches,
    startLoopback,
    openExternal: (url) => shell.openExternal(url),
    signedInPage: env.signedInPage,
  });
  // The profile picture lives only while signed in with account features on.
  const avatars = new AvatarCache(join(app.getPath('userData'), 'account-avatar.json'));
  if (!switches.get().accountEnabled || !session.state().signedIn) avatars.clear();
  const unwatchPhoto = session.onChange((s) => {
    if (!s.signedIn) avatars.clear();
  });
  const unwatchPhotoSwitch = switches.subscribe((s) => {
    if (!s.accountEnabled) avatars.clear();
  });
  const api = new AccountApi(env, session, activity, avatars);
  const ctl = new AccountController({
    available,
    switches,
    session,
    api,
    remembered: store.persistent,
    links: dashboardLinks(env),
  });
  const closeIpc = registerAccountIpc(ctl, session, opts.getMainWindow);
  if (!available)
    return () => {
      unwatchPhoto();
      unwatchPhotoSwitch();
      closeIpc();
      activity.stopAll();
    };
  const bridge = await startAccountTokenBridge({
    getToken: () => session.getAccessToken(),
    isSyncAllowed: () => switches.get().syncEnabled,
    isRemoteBackend: opts.isRemoteBackend,
    context: async () => ({ organization_id: await ctl.organizationId(), sync_api: env.syncApi }),
    invalidate: () => session.invalidateAccessToken(),
  });
  // The backend runs the sync engine: tell it at once when sync turns on or off.
  const tell = (path: '/api/cloud-sync/start' | '/api/cloud-sync/stop') => {
    if (opts.isRemoteBackend() || !opts.notifyBackend) return;
    void opts.notifyBackend(path).catch(() => {});
  };
  // Sync should run only while signed in with both switches on; tell the backend when that flips
  // (switch changes, sign-in, sign-out or an expired session), never in between.
  const shouldSync = () => {
    const s = switches.get();
    return (
      s.accountEnabled && s.syncEnabled && session.state().signedIn && Boolean(session.accountId())
    );
  };
  let syncing = shouldSync();
  const reconcile = () => {
    const next = shouldSync();
    if (next !== syncing) tell(next ? '/api/cloud-sync/start' : '/api/cloud-sync/stop');
    syncing = next;
  };
  const unwatchSwitches = switches.subscribe(() => reconcile());
  const unwatchSession = session.onChange(() => reconcile());
  // Only the managed backend's spawn env carries the capability; see setBackendOnlyEnv.
  setBackendOnlyEnv({
    VOICESTUDIO_ACCOUNT_BRIDGE_URL: bridge.url,
    VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN: bridge.token,
  });
  return () => {
    unwatchPhoto();
    unwatchPhotoSwitch();
    unwatchSwitches();
    unwatchSession();
    closeIpc();
    activity.stopAll();
    bridge.close();
    setBackendOnlyEnv(BRIDGE_ENV_OFF);
  };
}
