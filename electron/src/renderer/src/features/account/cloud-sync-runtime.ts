import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { getBridge } from '@/components/bridge';
import { installSyncedStorage } from './synced-storage';
import { useAccount } from './use-account';

/** Virtual key: the dictation shortcut lives in main, reached through the capture bridge. */
const SHORTCUT_KEY = 'voicestudio.shortcut.dictation';

type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
interface Capture {
  getShortcut(): Promise<{ accelerator: string; active: boolean; error?: string }>;
  setShortcut(
    accelerator: string,
  ): Promise<{ accelerator: string; active: boolean; error?: string }>;
}
type Pulled = { seq: number; values: Array<{ key: string; value: unknown; raw: boolean }> };

/**
 * While the user is signed in with sync on: apply what the backend pulled, then
 * mirror synced localStorage keys to it, and nudge a round on window focus.
 *
 * Pulled values are applied before any local value is mirrored, and every
 * mirror write carries the newest `seq` applied here, so a stale local value can
 * never overwrite a change another device made (the backend refuses it).
 */
export async function startCloudSyncRuntime(deps: {
  api: Api;
  capture?: Capture;
  intervalMs?: number;
}): Promise<() => void> {
  const { api, capture } = deps;
  const listing = await api<{ keys: string[]; raw?: string[]; partial?: string[] }>(
    '/api/cloud-sync/local/keys',
  );
  const keys = listing.keys;
  let seq = 0;
  let stopped = false;
  let shortcut: string | null = null;
  // The accelerator still active after this OS refused a synced one: not a user change.
  let refusedFallback: string | null = null;
  const post = (key: string, value: unknown) =>
    api('/api/cloud-sync/local', {
      method: 'PUT',
      body: JSON.stringify({ key, value, seen_seq: seq }),
    }).then(() => {});
  const synced = installSyncedStorage({
    storage: localStorage,
    keys: keys.filter((key) => key !== SHORTCUT_KEY),
    rawKeys: listing.raw ?? [],
    partialKeys: listing.partial ?? [],
    post,
  });
  const shortcutSynced = Boolean(capture) && keys.includes(SHORTCUT_KEY);

  const mirrorShortcutIfChangedHere = async () => {
    if (!shortcutSynced || !capture) return;
    const state = await capture.getShortcut().catch(() => null);
    if (
      state?.active &&
      state.accelerator &&
      state.accelerator !== shortcut &&
      state.accelerator !== refusedFallback
    ) {
      shortcut = state.accelerator;
      await post(SHORTCUT_KEY, state.accelerator).catch(() => {});
    }
  };

  const applyPulled = async () => {
    const pulled = await api<Pulled>(`/api/cloud-sync/local?since=${seq}`).catch(() => null);
    if (!pulled || stopped) return;
    for (const { key, value } of pulled.values) {
      if (key !== SHORTCUT_KEY) synced.apply(key, value);
      else if (capture && typeof value === 'string') {
        // Keep the synced value even when this OS refuses it ("Shortcut in use on this device").
        shortcut = value;
        const result = await capture.setShortcut(value).catch(() => null);
        refusedFallback = result && !result.active ? result.accelerator : null;
      }
    }
    seq = pulled.seq;
  };

  // 1. What the backend pulled before this window opened, first.
  await applyPulled();
  // 2. Then this device's current values (the backend ignores unchanged ones).
  for (const [key, value] of synced.snapshot()) await post(key, value).catch(() => {});
  await mirrorShortcutIfChangedHere();

  const tick = async () => {
    if (stopped) return;
    await applyPulled();
    await mirrorShortcutIfChangedHere();
  };
  const timer = setInterval(() => void tick(), deps.intervalMs ?? 3000);
  const onFocus = () => void api('/api/cloud-sync/run', { method: 'POST' }).catch(() => {});
  window.addEventListener('focus', onFocus);
  return () => {
    stopped = true;
    clearInterval(timer);
    window.removeEventListener('focus', onFocus);
    synced.uninstall();
  };
}

/** Mounted once in the app shell; does nothing unless signed in with sync on and set up. */
export function CloudSyncRuntime() {
  const { state } = useAccount();
  const active = Boolean(
    state?.available && state.signedIn && state.accountEnabled && state.syncEnabled,
  );
  // Shares the panel's query, so turning sync on in Settings starts the mirror at once.
  const status = useQuery({
    queryKey: ['cloud-sync-status'],
    queryFn: ({ signal }) => apiJson<{ enabled: boolean }>('/api/cloud-sync/status', { signal }),
    enabled: active,
    refetchInterval: active ? 15_000 : false,
  });
  const enabled = active && status.data?.enabled === true;
  useEffect(() => {
    if (!enabled) return;
    let stop: (() => void) | undefined;
    let cancelled = false;
    void startCloudSyncRuntime({ api: apiJson, capture: getBridge()?.capture })
      .then((s) => {
        if (cancelled) s();
        else stop = s;
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      stop?.();
    };
  }, [enabled]);
  return null;
}
