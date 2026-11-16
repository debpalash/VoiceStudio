// Diagnostics-only build metadata. Never import this into renderer/web builds.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export function generatedSupermetryConfig(env = process.env) {
  const config = { diagnostics: { sessionPings: env.SUPERMETRY_DIAGNOSTICS_SESSION_PINGS === '1' } };
  const appId = (env.SUPERMETRY_APP_ID ?? '').trim();
  const key = (env.SUPERMETRY_INGEST_KEY ?? '').trim();
  if (/^[a-z0-9]{6,32}$/.test(appId)) config.app_id = appId;
  if (/^smk_[a-z2-7]{39}$/.test(key)) config.ingest_key = key;
  const host = (env.SUPERMETRY_APP_HOST ?? '').trim().replace(/\/+$/, '');
  try {
    const url = new URL(host);
    const literal = /^http:\/\/(\[[^\]]+\]|[^:/?#]+)(?::[0-9]+)?(?:\/|$)/i.exec(host)?.[1]?.toLowerCase();
    if (Buffer.byteLength(host) <= 1988 && !/[\x00-\x20]/.test(host) &&
      !url.username && !url.password && !url.search && !url.hash && url.pathname === '/' &&
      (url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(literal)))) {
      config.host = host;
    }
  } catch { /* Invalid configuration is absent, without logging its value. */ }
  return config;
}

export function writeSupermetryConfig(env = process.env, path = new URL('./supermetry-app.generated.json', import.meta.url)) {
  const config = generatedSupermetryConfig(env);
  const target = path instanceof URL ? fileURLToPath(path) : path;
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, JSON.stringify(config) + '\n', { mode: 0o600 });
  return config;
}
