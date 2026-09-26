export const PROXY_KEYS: readonly string[];
export function saveProxyPreference(
  value: string,
  setEnv: (key: string, value: string) => Promise<unknown>,
): Promise<void>;
