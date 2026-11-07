import { BrowserWindow, ipcMain } from 'electron';
import { assertTrustedMainFrame } from '../ipc';
import { sendToLiveWindow } from '../window-safety';
import type { AccountApi, AccountPlan, AccountProfile } from './account-api';
import type { AccountPublicState } from '../../preload/index.d';
import type { AccountSession } from './account-session';
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

export type { AccountPublicState };

type PlanState = Omit<AccountPlan, 'settingsSync' | 'proDesktop'> & {
  planEndsAt: string | null;
  settingsSync: boolean | null;
  proDesktop: boolean | null;
};

export class AccountController {
  private profile: AccountProfile | null = null;
  private plan: PlanState | null = null;
  private profileError: string | null = null;

  constructor(
    private readonly parts: {
      available: boolean;
      switches: CloudSwitchStore;
      session: AccountSession;
      api: AccountApi;
      /** Read lazily: it asks safeStorage, which can prompt for the Keychain on macOS. */
      remembered: () => boolean;
      /** The dashboard pages for managing the profile and the plan (opened in the browser). */
      links: { profile: string; plan: string };
    },
  ) {}

  /** The Organization sync uses: the personal one (fetched once if not cached yet). */
  async organizationId(): Promise<string | null> {
    if (!this.profile) this.profile = await this.parts.api.get().catch(() => null);
    return this.profile?.personalOrganizationId ?? null;
  }

  async publicState(refreshProfile = false): Promise<AccountPublicState> {
    const { available, switches, session, remembered } = this.parts;
    const sw: CloudSwitches = available
      ? switches.get()
      : { accountEnabled: false, syncEnabled: false, prompted: true };
    const s =
      available && sw.accountEnabled
        ? session.state()
        : { signedIn: false, signingIn: false, notice: null };
    if (!s.signedIn) {
      this.profile = null;
      this.plan = null;
      this.profileError = null;
    } else if (refreshProfile || !this.profile) {
      try {
        this.profile = await this.parts.api.get();
        this.profileError = null;
      } catch (error) {
        // Shown on the account screen (status only), so a missing name or plan is never silent.
        const status = (error as { status?: number }).status;
        this.profileError = status ? String(status) : 'offline';
      }
      const org = this.profile?.personalOrganizationId;
      const full = org ? await this.parts.api.entitlements(org).catch(() => null) : null;
      const summary = this.profile?.summary;
      // Plan and storage from /v1/account; what the plan includes from entitlements (null: unknown).
      if (summary)
        this.plan = {
          ...summary,
          settingsSync: full?.settingsSync ?? null,
          proDesktop: full?.proDesktop ?? null,
        };
      else if (full) this.plan = { ...full, planEndsAt: null };
    }
    // The renderer never sees the signed photo link: main downloads it and hands over a data URL.
    const profile = this.profile
      ? {
          ...this.profile,
          avatarUrl: await this.parts.api.avatar(this.profile).catch(() => null),
        }
      : null;
    return {
      available,
      ...sw,
      // Only shown with account features on; reporting `true` otherwise means no warning.
      remembered: available && sw.accountEnabled ? remembered() : true,
      ...s,
      profile,
      plan: s.signedIn ? this.plan : null,
      profileError: s.signedIn ? this.profileError : null,
      links: s.signedIn ? this.parts.links : null,
    };
  }

  async setSwitches(raw: unknown): Promise<AccountPublicState> {
    const p = raw as Partial<CloudSwitches>;
    for (const key of ['accountEnabled', 'syncEnabled'] as const)
      if (p?.[key] !== undefined && typeof p[key] !== 'boolean')
        throw new Error('Invalid switches');
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
    for (const win of BrowserWindow.getAllWindows())
      sendToLiveWindow(win, ACCOUNT_CHANNELS.state, s);
  };
  const unsubscribe = session.onChange(() => void broadcast());
  ipcMain.handle(ACCOUNT_CHANNELS.get, (e) => (trusted(e), ctl.publicState(true)));
  ipcMain.handle(ACCOUNT_CHANNELS.signIn, (e) => (trusted(e), session.signIn().catch(() => {})));
  ipcMain.handle(ACCOUNT_CHANNELS.cancelSignIn, (e) => (trusted(e), session.cancelSignIn()));
  ipcMain.handle(ACCOUNT_CHANNELS.signOut, async (e) => (trusted(e), await session.signOut()));
  ipcMain.handle(ACCOUNT_CHANNELS.setSwitches, async (e, raw) => {
    trusted(e);
    try {
      return await ctl.setSwitches(raw);
    } finally {
      void broadcast();
    }
  });
  ipcMain.handle(ACCOUNT_CHANNELS.updateProfile, (e, raw) => (trusted(e), ctl.updateProfile(raw)));
  return () => {
    unsubscribe();
    for (const channel of Object.values(ACCOUNT_CHANNELS)) ipcMain.removeHandler(channel);
  };
}
