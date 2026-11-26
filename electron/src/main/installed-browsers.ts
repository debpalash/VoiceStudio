import { access } from 'node:fs/promises';
import { constants } from 'node:fs';
import { win32, posix } from 'node:path';
import { homedir } from 'node:os';
import { spawn } from 'node:child_process';
import { shell } from 'electron';
import { browserUrl, type InstalledBrowser } from '../shared/site-browser';

interface BrowserApp extends InstalledBrowser {
  path: string;
}
const brands = [
  [
    'chrome',
    'Google Chrome',
    'Google/Chrome/Application/chrome.exe',
    'Google Chrome.app',
    'google-chrome',
  ],
  [
    'edge',
    'Microsoft Edge',
    'Microsoft/Edge/Application/msedge.exe',
    'Microsoft Edge.app',
    'microsoft-edge',
  ],
  ['firefox', 'Firefox', 'Mozilla Firefox/firefox.exe', 'Firefox.app', 'firefox'],
  [
    'brave',
    'Brave',
    'BraveSoftware/Brave-Browser/Application/brave.exe',
    'Brave Browser.app',
    'brave-browser',
  ],
  ['vivaldi', 'Vivaldi', 'Vivaldi/Application/vivaldi.exe', 'Vivaldi.app', 'vivaldi'],
  ['opera', 'Opera', 'Programs/Opera/opera.exe', 'Opera.app', 'opera'],
  ['chromium', 'Chromium', 'Chromium/Application/chrome.exe', 'Chromium.app', 'chromium'],
  ['zen', 'Zen', 'Zen Browser/zen.exe', 'Zen.app', 'zen-browser'],
] as const;

/** Known browser executables only; the renderer never supplies commands or paths. */
export function browserCandidates(
  platform: NodeJS.Platform,
  env: NodeJS.ProcessEnv,
  home: string,
): BrowserApp[] {
  const candidates: BrowserApp[] = [];
  const { join, isAbsolute } = platform === 'win32' ? win32 : posix;
  for (const [id, name, windows, mac, linux] of brands) {
    const paths =
      platform === 'win32'
        ? [env.LOCALAPPDATA, env.ProgramFiles, env['ProgramFiles(x86)']]
            .filter((root): root is string => Boolean(root && isAbsolute(root)))
            .map((root) => join(root, ...windows.split('/')))
        : platform === 'darwin'
          ? ['/Applications', join(home, 'Applications')].map((root) => join(root, mac))
          : (env.PATH ?? '/usr/bin:/usr/local/bin:/snap/bin')
              .split(':')
              .filter(isAbsolute)
              .flatMap((root) => [
                join(root, linux),
                ...(id === 'chromium'
                  ? [join(root, 'chromium-browser')]
                  : id === 'chrome'
                    ? [join(root, 'google-chrome-stable')]
                    : []),
              ]);
    for (const path of paths) candidates.push({ id, name, path });
  }
  if (platform === 'darwin') {
    candidates.push({ id: 'safari', name: 'Safari', path: '/Applications/Safari.app' });
    candidates.push({ id: 'arc', name: 'Arc', path: '/Applications/Arc.app' });
  }
  return candidates;
}

async function detectedBrowsers(): Promise<BrowserApp[]> {
  const candidates = browserCandidates(process.platform, process.env, homedir());
  const results = await Promise.all(
    candidates.map(async (candidate) => {
      try {
        await access(
          candidate.path,
          process.platform === 'linux' ? constants.X_OK : constants.F_OK,
        );
        return candidate;
      } catch {
        return null;
      }
    }),
  );
  const found = new Map<string, BrowserApp>();
  for (const candidate of results)
    if (candidate && !found.has(candidate.id)) found.set(candidate.id, candidate);
  return [...found.values()];
}

export async function listInstalledBrowsers(): Promise<InstalledBrowser[]> {
  return (await detectedBrowsers()).map(({ id, name }) => ({ id, name }));
}

export async function openInBrowser(id: unknown, rawUrl: unknown): Promise<void> {
  const url = browserUrl(rawUrl);
  if (id === 'default') {
    await shell.openExternal(url);
    return;
  }
  const browser = (await detectedBrowsers()).find((item) => item.id === id);
  if (!browser) throw new Error('Browser is no longer installed');
  await new Promise<void>((resolve, reject) => {
    const usesLaunchHelper = process.platform === 'darwin';
    const child = usesLaunchHelper
      ? spawn('/usr/bin/open', ['-a', browser.path, url], { stdio: 'ignore', windowsHide: true })
      : spawn(browser.path, [url], { stdio: 'ignore', windowsHide: true, shell: false });
    child.once('error', reject);
    if (usesLaunchHelper) {
      child.once('exit', (code, signal) => {
        if (code === 0) resolve();
        else reject(new Error(`Could not open ${browser.name}: launcher exited (${signal ?? code})`));
      });
    } else {
      child.once('spawn', () => {
        child.unref();
        resolve();
      });
    }
  });
}
