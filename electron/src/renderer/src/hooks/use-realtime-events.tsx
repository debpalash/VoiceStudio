import { backendWebSocketUrl } from '@/lib/api/websocket';
import { apiPath } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useBackendStatus } from './use-backend-status';

const EVENT_QUERY_KEYS: Readonly<Record<string, readonly QueryKey[]>> = {
  projects: [['projects']],
  profiles: [queryKeys.profiles],
  dub_history: [['dub-history']],
  export_history: [['export-history']],
  generation_history: [queryKeys.history],
  model_status: [
    queryKeys.engines,
    ['sidebar-model-status'],
    ['loaded-models'],
    ['model-catalogue'],
    ['performance-profile'],
  ],
};

async function devBackendReady(signal: AbortSignal, remote: boolean): Promise<boolean> {
  if (window.location.protocol === 'app:' || remote) return true;
  try {
    const response = await fetch(apiPath('/health'), {
      signal: AbortSignal.any([signal, AbortSignal.timeout(2_000)]),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export function RealtimeEventSync() {
  const backend = useBackendStatus();
  const client = useQueryClient();

  useEffect(() => {
    if (backend.stage !== 'ready') return;
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let retry = 0;
    let connecting = false;
    const request = new AbortController();

    const scheduleReconnect = () => {
      if (!active || reconnectTimer) return;
      const delay = Math.min(1_000 * 2 ** retry, 30_000);
      retry += 1;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        void connect();
      }, delay);
    };

    const connect = async () => {
      if (!active || connecting || (socket && socket.readyState < WebSocket.CLOSING)) return;
      connecting = true;
      try {
        if (!(await devBackendReady(request.signal, backend.remote))) {
          scheduleReconnect();
          return;
        }
        const url = await backendWebSocketUrl('/ws/events', backend);
        if (!active) return;
        socket = new WebSocket(url);
        socket.onopen = () => {
          retry = 0;
        };
        socket.onmessage = (message) => {
          let kind: string | undefined;
          try {
            const event: unknown = JSON.parse(String(message.data));
            if (event && typeof event === 'object' && !Array.isArray(event)) {
              const value = (event as { kind?: unknown }).kind;
              if (typeof value === 'string') kind = value;
            }
          } catch {
            return;
          }
          if (!kind || kind === 'ping') return;
          for (const queryKey of EVENT_QUERY_KEYS[kind] || []) {
            void client.invalidateQueries({ queryKey });
          }
        };
        socket.onerror = () => socket?.close();
        socket.onclose = () => {
          socket = null;
          scheduleReconnect();
        };
      } catch {
        scheduleReconnect();
      } finally {
        connecting = false;
      }
    };

    void connect();
    return () => {
      active = false;
      request.abort();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [backend.baseUrl, backend.remote, backend.stage, client]);

  return null;
}
