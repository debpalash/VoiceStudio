import { getBridge } from '@/components/bridge';
import { getBackendStatusSnapshot, type BackendStatus } from '@/hooks/use-backend-status';
import { absoluteApiBase } from './client';
import { authenticatedWsUrl } from '@shared/api/authSession';

export type BackendWebSocketPath = '/ws/events' | '/ws/transcribe' | '/ws/tts';

/** Resolve through main for packaged/authenticated remote sessions and Vite in local development. */
export async function backendWebSocketUrl(
  path: BackendWebSocketPath,
  status?: BackendStatus,
): Promise<string> {
  const backend = status || getBackendStatusSnapshot();
  const bridge = getBridge();
  if (__WEB_DEPLOYMENT__) {
    return authenticatedWsUrl(path, { apiBase: absoluteApiBase() });
  }
  const mainOwned = window.location.protocol === 'app:' || backend.remote;
  const raw =
    bridge && mainOwned
      ? await bridge.backend.websocketUrl(path)
      : new URL(
          mainOwned && backend.baseUrl ? path : '/api' + path,
          mainOwned ? backend.baseUrl || window.location.href : window.location.href,
        ).toString();
  const url = new URL(raw);
  if (url.protocol === 'https:') url.protocol = 'wss:';
  else if (url.protocol === 'http:') url.protocol = 'ws:';
  return url.toString();
}
