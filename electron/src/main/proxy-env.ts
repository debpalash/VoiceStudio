import { execFileSync } from 'node:child_process';

/** Convert WinINET's protocol map into URLs understood by uv. Never log credentials. */
export function parseWinInetProxy(raw: string): NodeJS.ProcessEnv {
  const urls: Record<string, string> = {};
  for (const part of raw.split(';')) {
    const item = part.trim();
    if (!item) continue;
    const match = /^(http|https|socks)\s*=\s*(.+)$/i.exec(item);
    const scheme = match?.[1].toLowerCase() ?? 'all';
    const address = match?.[2] ?? item;
    if (/\s|=/.test(address)) continue;
    const candidate = address.includes('://')
      ? address
      : `${scheme === 'socks' ? 'socks5h' : 'http'}://${address}`;
    try {
      const url = new URL(candidate);
      if (!['http:', 'https:', 'socks4:', 'socks4a:', 'socks5:', 'socks5h:'].includes(url.protocol))
        continue;
      if (!url.hostname || (url.pathname && url.pathname !== '/') || url.search || url.hash)
        continue;
      urls[scheme] = candidate;
    } catch {
      /* A malformed system value is not a valid proxy override. */
    }
  }
  const http = urls.http ?? urls.all ?? urls.socks;
  const https = urls.https ?? urls.all ?? urls.socks;
  return { ...(http ? { HTTP_PROXY: http } : {}), ...(https ? { HTTPS_PROXY: https } : {}) };
}

interface SystemProxy {
  server: string;
  bypass?: string;
}

/** `10.*` / `192.168.*` style prefix rules as the CIDR block NO_PROXY understands. */
function prefixWildcardCidr(value: string): string | null {
  const match = /^(\d{1,3}(?:\.\d{1,3}){0,2})\.\*$/.exec(value);
  if (!match) return null;
  const octets = match[1].split('.').map(Number);
  if (octets.some((octet) => octet > 255)) return null;
  const bits = octets.length * 8;
  return `${[...octets, 0, 0, 0, 0].slice(0, 4).join('.')}/${bits}`;
}

/** Public endpoints setup contacts by default (pypi-mirror.ts, runtime-project.ts, uv, HF). */
export const DEFAULT_SETUP_HOSTS: readonly string[] = [
  'pypi.org',
  'files.pythonhosted.org',
  'mirrors.aliyun.com',
  'download.pytorch.org',
  'download-r2.pytorch.org',
  'astral.sh',
  'github.com',
  'api.github.com',
  'objects.githubusercontent.com',
  'release-assets.githubusercontent.com',
  'raw.githubusercontent.com',
  'gh-proxy.com',
  'huggingface.co',
  'cdn-lfs.huggingface.co',
  'hf-mirror.com',
  'repo.radeon.com',
  'stable.repo.amd.com',
];

/** True when NO_PROXY entry `host` would also exclude a contacted subdomain of it. */
function hasUncoveredSubdomain(
  host: string,
  contacted: readonly string[],
  covered: (candidate: string) => boolean,
): boolean {
  const suffix = `.${host.toLowerCase()}`;
  return contacted.some((other) => other.toLowerCase().endsWith(suffix) && !covered(other));
}

/** Hosts matched by a WinINET wildcard rule (`*` = any characters, case-insensitive). */
function globMatchedHosts(
  rule: string,
  hosts: readonly string[],
  covered: (host: string) => boolean,
): string[] {
  // A scheme or port limits the rule to some requests; NO_PROXY hosts bypass all
  // of them, so a qualified rule is not translated (the host stays proxied).
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(rule) || /:\d*$/.test(rule) || /[/\\]/.test(rule)) {
    console.info('[proxy-env] not bypassing a scheme- or port-qualified system proxy rule');
    return [];
  }
  const matches = ruleMatcher(rule);
  if (!matches) return [];
  // NO_PROXY also excludes subdomains of an entry, so only keep a host whose
  // contacted subdomains some rule in the list covers too; the rest stay proxied.
  return hosts.filter((host) => {
    if (!matches(host)) return false;
    if (hasUncoveredSubdomain(host, hosts, covered)) {
      console.info('[proxy-env] not bypassing a glob match that would also bypass subdomains');
      return false;
    }
    return true;
  });
}

/** WinINET matching for one bypass rule: case-insensitive, `*` = any characters, no scheme/port/path. */
function ruleMatcher(rule: string): ((host: string) => boolean) | null {
  if (!rule || rule.includes('<')) return null;
  if (rule === '*') return () => true;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(rule) || /:\d*$/.test(rule) || /[/\\]/.test(rule)) return null;
  const pattern = new RegExp(
    `^${rule
      .split('*')
      .map((part) => part.replace(/[.+?^${}()|[\]\\]/g, '\\$&'))
      .join('.*')}$`,
    'i',
  );
  return (host) => pattern.test(host);
}

/**
 * Only translate bypass rules whose meaning NO_PROXY can preserve. Proxy tools
 * (Clash, v2rayN) write `localhost;127.*;10.*;172.16.*;192.168.*;<local>`, so
 * those exact forms must convert: refusing them blocked setup for every user
 * behind a system proxy. `<local>` only exempts dotless intranet names, which
 * downloads from the pinned public hosts never use, so it only keeps the
 * dotless hosts of user-configured download endpoints (`downloadHosts`) direct.
 */
export function proxyBypassHosts(
  raw: string,
  downloadHosts: string[] = [],
  contactedHosts?: readonly string[],
): string[] | null {
  const hosts: string[] = [];
  const rules = raw
    .split(';')
    .map((part) => part.trim())
    .filter(Boolean);
  const matchers = rules.map(ruleMatcher).filter((m) => m !== null);
  const covered = (host: string) => matchers.some((matches) => matches(host));
  for (const value of rules) {
    if (value === '*') {
      hosts.push(value);
      continue;
    }
    if (value.toLowerCase() === '<local>') {
      hosts.push(...downloadHosts.filter((host) => !host.includes('.') && !host.includes(':')));
      continue;
    }
    const cidr = prefixWildcardCidr(value);
    if (cidr) {
      hosts.push(cidr);
      continue;
    }
    const host = value.replace(/^\*\./, '.');
    if (!/^[a-z0-9_.:[\]-]+$/i.test(host) || value.includes('<')) {
      if (!contactedHosts) return null;
      // NO_PROXY cannot express this rule, but WinINET treats it as a
      // case-insensitive `*` glob. Bypass exactly the hosts setup contacts that
      // it matches; a rule matching none cannot affect any setup request.
      const matched = globMatchedHosts(value, contactedHosts, covered);
      if (matched.length === 0) {
        console.info('[proxy-env] ignoring a system proxy bypass rule that matches no setup host');
      }
      hosts.push(...matched);
      continue;
    }
    // NO_PROXY `foo.com` also bypasses `sub.foo.com`, WinINET's plain `foo.com`
    // does not. If setup contacts such a subdomain, keep the whole entry proxied.
    if (contactedHosts && !host.startsWith('.') && hasUncoveredSubdomain(host, contactedHosts, covered)) {
      console.info('[proxy-env] not bypassing a host rule that would also bypass its subdomains');
      continue;
    }
    hosts.push(host);
  }
  return hosts;
}

/** A bounded registry read; denied/unavailable registry access leaves uv's defaults intact. */
function systemProxy(): SystemProxy | undefined {
  try {
    const output = execFileSync(
      'reg.exe',
      ['query', 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'],
      { encoding: 'utf8', timeout: 2500, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] },
    );
    if (!/^\s*ProxyEnable\s+REG_DWORD\s+0x0*1\s*$/im.test(output)) return undefined;
    const server = /^\s*ProxyServer\s+REG_SZ\s+(.+)$/im.exec(output)?.[1].trim();
    const bypass = /^\s*ProxyOverride\s+REG_SZ\s+(.+)$/im.exec(output)?.[1].trim();
    return server ? { server, bypass } : undefined;
  } catch {
    return undefined;
  }
}

const DOWNLOAD_ENDPOINT_KEYS = [
  'UV_INDEX',
  'UV_INDEX_URL',
  'UV_DEFAULT_INDEX',
  'UV_EXTRA_INDEX_URL',
  'UV_PYTHON_INSTALL_MIRROR',
  'UV_FIND_LINKS',
  'PIP_INDEX_URL',
  'PIP_EXTRA_INDEX_URL',
  'HF_ENDPOINT',
];

/** Hostnames of the download endpoints the user configured through the environment. */
function configuredDownloadHosts(input: NodeJS.ProcessEnv): string[] {
  const hosts = new Set<string>();
  for (const key of DOWNLOAD_ENDPOINT_KEYS) {
    // uv splits UV_FIND_LINKS on commas; the index lists are whitespace-separated.
    const delimiter = key === 'UV_FIND_LINKS' ? /[,\s]+/ : /\s+/;
    for (const entry of (input[key] ?? '').split(delimiter)) {
      // UV_INDEX entries may be `name=url`.
      const value = entry.replace(/^[A-Za-z0-9_-]+=(?=[a-z][a-z0-9+.-]*:\/\/)/i, '');
      try {
        const host = new URL(value).hostname.toLowerCase();
        if (host) hosts.add(host);
      } catch {
        /* Not a URL (a local path or empty value). */
      }
    }
  }
  return [...hosts];
}

/** uv download subprocess settings; explicit caller proxies always take precedence. */
export function downloadProxyEnv(
  input: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
  readSystemProxy: () => string | SystemProxy | undefined = systemProxy,
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = Object.fromEntries(
    Object.entries(input).filter(([key]) => /^(http|https|all|no)_proxy$/i.test(key)),
  );
  const explicit = Object.keys(env).some((key) => /^(http|https|all)_proxy$/i.test(key));
  let systemBypass: string[] = [];
  if (platform === 'win32' && !explicit) {
    const raw = readSystemProxy();
    const settings = typeof raw === 'string' ? { server: raw } : raw;
    const configured = configuredDownloadHosts(input);
    const hosts = proxyBypassHosts(settings?.bypass ?? '', configured, [
      ...new Set([...DEFAULT_SETUP_HOSTS, ...configured]),
    ]);
    // The raw registry value never reaches uv: every rule is either translated
    // to NO_PROXY or glob-matched against the hosts setup contacts.
    if (settings && hosts) {
      Object.assign(env, parseWinInetProxy(settings.server));
      systemBypass = hosts;
    }
  }
  const bypass = new Set([
    ...`${env.NO_PROXY ?? ''},${env.no_proxy ?? ''}`
      .split(',')
      .map((host) => host.trim())
      .filter(Boolean),
    ...systemBypass,
    'localhost',
    '127.0.0.1',
    '::1',
  ]);
  env.NO_PROXY = env.no_proxy = [...bypass].join(',');
  return env;
}
