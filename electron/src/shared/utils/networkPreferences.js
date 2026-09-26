export const PROXY_KEYS = [
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'ALL_PROXY',
  'http_proxy',
  'https_proxy',
  'all_proxy',
];
/** Keep native libraries and Python clients on the same proxy; a failure must reach the caller. */
export async function saveProxyPreference(value, setEnv) {
  for (const key of PROXY_KEYS) await setEnv(key, value.trim());
}
