/**
 * deploymentMode — which of the three runtime contexts is this frontend in?
 *
 *   - 'desktop': the Tauri webview. The shell supervises the backend
 *     (bootstrap.rs), records crash markers (crash.rs), and auto-restarts —
 *     "restart the app / check Settings → Logs" is real advice here.
 *   - 'dev': the Vite dev server (`bun run dev`). There is NO supervisor —
 *     concurrently tears the whole dev stack down when the backend exits —
 *     so the honest advice is "look at the terminal / omnivoice.log".
 *   - 'server': everything else — the page was served by the backend itself
 *     (Docker, LAN share, remote GPU). If that backend dies, this very page
 *     may go down with it; the evidence is in the container/server logs.
 *
 * Used by api/client.ts (#1164) to stop giving desktop-shaped advice
 * ("restart the app") to users who have no app to restart.
 */

export type DeploymentMode = 'desktop' | 'dev' | 'server';

/** Pure + injectable for unit tests (same pattern as client._resolveApiBase). */
export function detectDeploymentMode(env: unknown, win: unknown): DeploymentMode {
  const w = win as Record<string, unknown> | undefined | null;
  if (w && (w.__TAURI__ || w.__TAURI_INTERNALS__)) return 'desktop';
  if ((env as { DEV?: boolean } | undefined | null)?.DEV) return 'dev';
  return 'server';
}

/** The running context's mode. */
export function deploymentMode(): DeploymentMode {
  return detectDeploymentMode(
    import.meta.env ?? {},
    typeof window !== 'undefined' ? window : undefined,
  );
}
