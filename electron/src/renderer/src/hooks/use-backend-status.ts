import { useSyncExternalStore } from 'react';
import { onlineManager } from '@tanstack/react-query';
import { isBackendReachable } from '@shared/utils/backendStage';
import { noteBackendStage } from '@/lib/status-polling';

// Derived from the global `Window.voicestudio` declaration (src/preload/index.d.ts,
// included by tsconfig.web.json) rather than imported by path: with
// allowImportingTsExtensions a `.d.ts` import resolves to the preload's `.ts`
// source, which the renderer project must not compile.
export type VoiceStudioBridge = Window['voicestudio'];
export type BackendStatus = Awaited<ReturnType<VoiceStudioBridge['backend']['getStatus']>>;
export type BackendStage = BackendStatus['stage'];

// The `ready`/`unresponsive` policy lives in shared/utils/backendStage so the
// main process, the shared client and the renderer cannot drift apart on what
// a stage means for an in-flight request. Re-exported here because nearly every
// readiness consumer already reads this module.
export { isBackendReachable, isBackendBusy } from '@shared/utils/backendStage';

/** Used when the bridge is absent (vitest, a plain browser tab): behave as if the backend is up. */
export const FALLBACK_BACKEND_STATUS: BackendStatus = {
  stage: 'ready',
  baseUrl: '',
  port: 3900,
  managed: false,
  remote: false,
  elapsedMs: 0,
  logTail: [],
};

function bridge(): VoiceStudioBridge['backend'] | null {
  if (typeof window === 'undefined') return null;
  const api = (window as { voicestudio?: VoiceStudioBridge }).voicestudio;
  return api?.backend ?? null;
}

const nativeBackend = bridge();
let current: BackendStatus = nativeBackend
  ? { ...FALLBACK_BACKEND_STATUS, stage: 'attaching' }
  : FALLBACK_BACKEND_STATUS;
// Every renderer query targets the local backend. Holding React Query offline
// until the native supervisor is ready prevents a startup/crash fan-out of
// doomed requests, then resumes active queries automatically on readiness.
if (nativeBackend) onlineManager.setOnline(false);
let revision = 0;
const listeners = new Set<() => void>();
let bridgeUnsubscribe: (() => void) | null = null;

function publish(status: BackendStatus): void {
  revision++;
  current = status;
  noteBackendStage(status.stage);
  if (nativeBackend) onlineManager.setOnline(isBackendReachable(status.stage));
  for (const listener of listeners) listener();
}

export function getBackendStatusSnapshot(): BackendStatus {
  return current;
}

export function subscribeBackendStatus(listener: () => void): () => void {
  listeners.add(listener);
  const api = bridge();
  if (api && !bridgeUnsubscribe) {
    bridgeUnsubscribe = api.onStatus(publish);
    // Seed with the current state: the stage may have changed before the
    // first subscriber mounted, and a late-mounted hook must not sit on the
    // fallback until the next transition.
    const snapshotRevision = revision;
    api
      .getStatus()
      .then((status) => {
        if (bridgeUnsubscribe && revision === snapshotRevision) publish(status);
      })
      .catch(() => {});
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && bridgeUnsubscribe) {
      bridgeUnsubscribe();
      bridgeUnsubscribe = null;
      revision++;
    }
  };
}

function getServerSnapshot(): BackendStatus {
  return FALLBACK_BACKEND_STATUS;
}

export function useBackendStatus(): BackendStatus {
  return useSyncExternalStore(subscribeBackendStatus, getBackendStatusSnapshot, getServerSnapshot);
}
