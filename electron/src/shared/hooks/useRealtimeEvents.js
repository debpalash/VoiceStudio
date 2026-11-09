import { useEffect, useLayoutEffect, useRef } from 'react';
import { API, apiUrl } from '../api/client';
import { authenticatedWsUrl } from '../api/authSession';

// Maintains the backend event stream. Project, profile, dub-history, and
// export-history changes arrive as { kind, action, id }; ping frames only keep
// the connection alive.
export default function useRealtimeEvents(handlers) {
  const handlersRef = useRef(handlers);
  const socketRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const retryCountRef = useRef(0);
  const connectingRef = useRef(false);
  const mountedRef = useRef(false);
  // Only committed props reach the socket: a render React discards must not
  // swap in its handlers.
  useLayoutEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    mountedRef.current = true;

    const hasLiveSocket = () => {
      const state = socketRef.current?.readyState;
      return state === 0 || state === 1;
    };

    let probeBackend;
    const scheduleReconnect = (baseDelay = 2000, maximumDelay = 60000) => {
      if (!mountedRef.current || reconnectTimerRef.current !== null) return;
      const delay = Math.min(baseDelay * 2 ** retryCountRef.current, maximumDelay);
      retryCountRef.current += 1;
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        probeBackend();
      }, delay);
    };

    const openSocket = async () => {
      try {
        const endpoint = await authenticatedWsUrl('/ws/events', { apiBase: API });
        if (!mountedRef.current || hasLiveSocket()) return;

        const socket = new WebSocket(endpoint);
        socketRef.current = socket;

        socket.onopen = () => {
          retryCountRef.current = 0;
          console.debug('[ws/events] connected');
        };
        socket.onmessage = (message) => {
          let event;
          try {
            event = JSON.parse(message.data);
          } catch {
            console.warn('[ws/events] malformed message ignored');
            return;
          }
          if (
            event === null ||
            typeof event !== 'object' ||
            Array.isArray(event) ||
            Object.getPrototypeOf(event) !== Object.prototype
          ) {
            console.warn('[ws/events] malformed message ignored');
            return;
          }
          if (event.kind === 'ping' || typeof event.kind !== 'string') return;
          const currentHandlers = handlersRef.current;
          if (
            currentHandlers &&
            Object.prototype.hasOwnProperty.call(currentHandlers, event.kind) &&
            typeof currentHandlers[event.kind] === 'function'
          ) {
            currentHandlers[event.kind](event);
          }
        };
        socket.onclose = (closeEvent) => {
          if (socketRef.current === socket) socketRef.current = null;
          if (!mountedRef.current) return;
          if (retryCountRef.current <= 5) {
            console.debug(`[ws/events] closed with code ${closeEvent.code}`);
          }
          scheduleReconnect();
        };
        socket.onerror = () => socket.close();
      } catch {
        console.warn('[ws/events] connection failed');
        scheduleReconnect(1000, 30000);
      } finally {
        connectingRef.current = false;
      }
    };

    probeBackend = async () => {
      if (!mountedRef.current || connectingRef.current || hasLiveSocket()) return;

      try {
        const response = await fetch(apiUrl('/health'), {
          signal: AbortSignal.timeout(2000),
        });
        if (!response.ok) throw new Error('health check failed');
      } catch {
        scheduleReconnect();
        return;
      }

      if (!mountedRef.current || connectingRef.current || hasLiveSocket()) return;
      retryCountRef.current = 0;
      connectingRef.current = true;
      await openSocket();
    };

    probeBackend();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      const socket = socketRef.current;
      if (socket) {
        socket.onclose = null;
        socket.close();
        socketRef.current = null;
      }
      connectingRef.current = false;
    };
  }, []);
}
