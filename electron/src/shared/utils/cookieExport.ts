export const DUB_COOKIE_TRANSPORT_ERROR = 'DUB_COOKIE_TRANSPORT';
export const DUB_COOKIE_SIZE_ERROR = 'DUB_COOKIE_TOO_LARGE';
export const MAX_COOKIE_EXPORT_BYTES = 1024 * 1024;

export function cookieSelectionError(code: string): Error & { code: string } {
  return Object.assign(new Error(code), { code });
}

export function _cookieTransportAllowed(apiBase: string): boolean {
  const endpoint = new URL(apiBase, window.location.href);
  return (
    endpoint.protocol === 'https:' ||
    endpoint.hostname === 'localhost' ||
    endpoint.hostname === '127.0.0.1' ||
    endpoint.hostname === '[::1]'
  );
}
