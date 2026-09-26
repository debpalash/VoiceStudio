export function joinApiPath(base: string, path: string): string {
  return `${base}${path.startsWith('/') ? '' : '/'}${path}`;
}

/** Vite's `/api` is a transport prefix except for backend routers that
 * canonically own it. Electron main applies the same one-prefix rule. */
export function rewriteDevApiProxyPath(path: string): string {
  return /^\/api\/(settings|auth|integrations|mcp)(?:\/|$)/.test(path)
    ? path
    : path.replace(/^\/api/, '');
}
