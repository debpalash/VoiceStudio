/**
 * useRealtimeEvents — WebSocket connection to /ws/events for live sidebar updates.
 *
 * Connects once on mount, automatically reconnects with exponential backoff,
 * and dispatches invalidation signals to the parent callbacks.
 *
 * Events from backend:
 *   { kind: "projects",       action: "created"|"updated"|"deleted", id: "..." }
 *   { kind: "profiles",       action: "created"|"updated"|"locked"|"unlocked"|"deleted", id: "..." }
 *   { kind: "dub_history",    action: "saved"|"deleted", id: "..." }
 *   { kind: "export_history", action: "exported"|"recorded", id: "..." }
 *   { kind: "ping" }  // keepalive, ignored
 */
import { useEffect, useRef, useCallback } from 'react';
import { API, apiUrl } from '../api/client';
import { authenticatedWsUrl } from '../api/authSession';

// HTTP health-check URL (derived from same base as WS). We poll this before
// creating the WebSocket so the first attempt doesn't fail with ECONNREFUSED
// when the Python backend hasn't finished starting Uvicorn (~14s on cold start).
//
// Must be the auth-exempt liveness endpoint /health (in backend _SHELL_PATHS),
// NOT /model/status: this is a raw fetch() that does NOT carry the LAN PIN or
// short-lived administrator session apiFetch attaches. In LAN-share / remote
// mode a gated path returns 401, which would reject this probe forever and the
// WebSocket would never open. /health is exempt from both gates and returns
// 200 as soon as Uvicorn is up — exactly the liveness signal this probe needs.
const HEALTH_CHECK_URL = apiUrl('/health');

/**
 * @param {Object} handlers - Map of event kind → callback
 * @param {Function} handlers.projects      - Called when projects list changes
 * @param {Function} handlers.profiles      - Called when profiles list changes
 * @param {Function} handlers.dub_history   - Called when dub history changes
 * @param {Function} handlers.export_history - Called when export history changes
 */
export default function useRealtimeEvents(handlers) {
  const wsRef = useRef(null);
  const handlersRef = useRef(handlers);
  const reconnectTimerRef = useRef(null);
  const retryCountRef = useRef(0);
  const mountedRef = useRef(true);
  const connectingRef = useRef(false);
  const connectRef = useRef(() => {});
  const openWebSocketRef = useRef(async () => {});

  // Keep handlers ref current without causing reconnects
  useEffect(() => {
    handlersRef.current = handlers;
  });

  const scheduleReconnect = useCallback(() => {
    if (!mountedRef.current) return;
    const delay = Math.min(2000 * Math.pow(2, retryCountRef.current), 60_000);
    retryCountRef.current++;
    reconnectTimerRef.current = setTimeout(() => connectRef.current(), delay);
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    // Don't double-connect
    if (connectingRef.current || (wsRef.current && wsRef.current.readyState <= 1)) return;

    // ── Phase 1: Wait for backend HTTP to be reachable ────────────────
    // Background: the Python backend takes ~14s to import torch/fastapi/etc
    // before Uvicorn starts. The frontend mounts faster, so the first
    // WebSocket attempt always fails with code 1006. Polling HTTP first
    // eliminates that noise and prevents the false "reconnecting" log.
    fetch(HEALTH_CHECK_URL, { signal: AbortSignal.timeout(2000) })
      .then((res) => {
        if (!res.ok) throw new Error(`health check returned ${res.status}`);
        // Backend is up — proceed to Phase 2
        if (!mountedRef.current) return;
        if (connectingRef.current || (wsRef.current && wsRef.current.readyState <= 1)) return;
        retryCountRef.current = 0; // reset backoff — health passed
        connectingRef.current = true;
        void openWebSocketRef.current();
      })
      .catch(() => {
        // Backend not ready yet — schedule reconnect (no error log)
        scheduleReconnect();
      });
  }, [scheduleReconnect]);

  async function openWebSocket() {
    if (!mountedRef.current) return;

    try {
      // Browser WebSockets cannot set Authorization. Cross-origin clients mint
      // a fresh, path-bound, one-use ticket for every connection attempt;
      // same-origin cookie and loopback clients receive a credential-free URL.
      const endpoint = await authenticatedWsUrl('/ws/events', { apiBase: API });
      if (!mountedRef.current) return;
      if (wsRef.current && wsRef.current.readyState <= 1) return;
      const ws = new WebSocket(endpoint);
      wsRef.current = ws;

      ws.onopen = () => {
        retryCountRef.current = 0;
        console.debug('[ws/events] connected');
      };

      ws.onmessage = (e) => {
        let event;
        try {
          event = JSON.parse(e.data);
          if (!event || typeof event !== 'object' || Array.isArray(event)) throw new TypeError();
        } catch {
          // The frame and parser exception are remote-controlled. Keep the
          // warning useful without placing either value in browser logs.
          console.warn('[ws/events] malformed message ignored');
          return;
        }
        const kind = event.kind;
        if (kind === 'ping') return; // keepalive, ignore
        if (typeof kind !== 'string') return;

        const handler = Object.entries(handlersRef.current ?? {}).find(
          ([registeredKind]) => registeredKind === kind,
        )?.[1];
        if (typeof handler === 'function') {
          handler(event);
        }
      };

      ws.onclose = (e) => {
        wsRef.current = null;
        if (!mountedRef.current) return;
        // Exponential backoff: 2s, 4s, 8s, 16s, max 60s
        const delay = Math.min(2000 * Math.pow(2, retryCountRef.current), 60_000);
        retryCountRef.current++;
        if (retryCountRef.current <= 5) {
          console.debug(`[ws/events] closed (code=${e.code}), reconnecting in ${delay}ms`);
        }
        reconnectTimerRef.current = setTimeout(() => connectRef.current(), delay);
      };

      ws.onerror = () => {
        // onerror is always followed by onclose, so we just let onclose handle reconnect
        ws.close();
      };
    } catch {
      // Authentication and transport failures may wrap request metadata. Keep
      // logs useful without ever serializing a credential-bearing exception.
      console.warn('[ws/events] connection failed');
      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30_000);
      retryCountRef.current++;
      reconnectTimerRef.current = setTimeout(() => connectRef.current(), delay);
    } finally {
      connectingRef.current = false;
    }
  }

  connectRef.current = connect;
  openWebSocketRef.current = openWebSocket;

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      connectingRef.current = false;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on unmount
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);
}
