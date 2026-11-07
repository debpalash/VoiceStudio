import { useEffect, useState } from 'react';
import { getBridge } from '@/components/bridge';
import type { AccountPublicState } from '../../../../preload/index.d';

export function useAccount() {
  const account = getBridge()?.account;
  const [state, setState] = useState<AccountPublicState | undefined>();
  useEffect(() => {
    if (!account) return;
    let live = true;
    void account
      .get()
      .then((s) => live && setState(s))
      .catch(() => {});
    const off = account.onState((s) => live && setState(s));
    return () => {
      live = false;
      off();
    };
  }, [account]);
  return {
    supported: Boolean(account),
    state,
    setSwitches: async (p: { accountEnabled?: boolean; syncEnabled?: boolean }) => {
      if (!account) return;
      try {
        setState(await account.setSwitches(p));
      } catch {
        // Main applies the change for this run even when saving fails; show what is real.
        setState(await account.get().catch(() => undefined));
      }
    },
    /** Reload profile, plan and photo from VoiceStudio Cloud (Refresh, or Retry after a failure). */
    refresh: async () => {
      if (!account) return;
      const next = await account.refresh().catch(() => undefined);
      if (next) setState(next);
    },
    signIn: () => account?.signIn(),
    cancelSignIn: () => account?.cancelSignIn(),
    signOut: () => account?.signOut(),
  };
}
