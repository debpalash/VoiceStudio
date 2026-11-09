import { readdirSync } from 'node:fs';
import { readdir } from 'node:fs/promises';
import { homedir } from 'node:os';
import { delimiter, join } from 'node:path';

interface ToolPathOptions {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  home?: string;
  /** Async directory-listing seam (tests); defaults to fs/promises readdir. */
  readdirAsync?: (path: string) => Promise<string[]>;
}

function nvmRoot(home: string): string {
  return join(home, '.nvm', 'versions', 'node');
}

function nvmBinsFrom(root: string, names: string[]): string[] {
  // Newest numeric release first: a lexical sort would put v9 ahead of v24.
  return names
    .flatMap((name) => {
      const match = /^v(\d+)\.(\d+)\.(\d+)$/.exec(name);
      return match ? [{ name, parts: match.slice(1).map(Number) }] : [];
    })
    .sort((a, b) => b.parts[0] - a.parts[0] || b.parts[1] - a.parts[1] || b.parts[2] - a.parts[2])
    .map(({ name }) => join(root, name, 'bin'));
}

function nvmBinDirs(home: string): string[] {
  const root = nvmRoot(home);
  try {
    return nvmBinsFrom(root, readdirSync(root));
  } catch {
    return [];
  }
}

async function nvmBinDirsAsync(
  home: string,
  list: (path: string) => Promise<string[]>,
): Promise<string[]> {
  const root = nvmRoot(home);
  try {
    return nvmBinsFrom(root, await list(root));
  } catch {
    return [];
  }
}

function assembleDirs(
  { env = process.env, platform = process.platform, home = homedir() }: ToolPathOptions,
  nvm: string[],
): string[] {
  const inherited = (env.PATH ?? env.Path ?? '').split(delimiter).filter(Boolean);
  const common = [
    join(home, '.local', 'bin'),
    join(home, '.cargo', 'bin'),
    join(home, '.bun', 'bin'),
    join(home, '.claude', 'local'),
    join(home, '.volta', 'bin'),
  ];
  const extra =
    platform === 'win32'
      ? [
          join(env.APPDATA || join(home, 'AppData', 'Roaming'), 'npm'),
          join(env.LOCALAPPDATA || join(home, 'AppData', 'Local'), 'Programs', 'claude'),
        ]
      : ['/opt/homebrew/bin', '/usr/local/bin', join(home, '.npm-global', 'bin'), ...nvm];
  return [...new Set([...inherited, ...common, ...extra])];
}

/**
 * Directories to search for user-installed CLIs (uv, claude, codex, ...).
 *
 * A GUI launch (Finder, Explorer, a .desktop file) does not see the shell's
 * PATH additions, so the inherited PATH alone misses the standard user-level
 * install locations. Inherited entries stay first so an explicit PATH wins.
 */
export function toolSearchDirs(options: ToolPathOptions = {}): string[] {
  const { platform = process.platform, home = homedir() } = options;
  return assembleDirs(options, platform === 'win32' ? [] : nvmBinDirs(home));
}

/** Non-blocking twin of toolSearchDirs(): the nvm listing never touches the main thread. */
export async function toolSearchDirsAsync(options: ToolPathOptions = {}): Promise<string[]> {
  const { platform = process.platform, home = homedir(), readdirAsync = readdir } = options;
  return assembleDirs(
    options,
    platform === 'win32' ? [] : await nvmBinDirsAsync(home, readdirAsync),
  );
}

/** Copy of `env` whose PATH includes the user-level tool directories. */
export function envWithToolPath(options: ToolPathOptions = {}): NodeJS.ProcessEnv {
  const env = options.env ?? process.env;
  const next: NodeJS.ProcessEnv = { ...env };
  // Windows env keys are case-insensitive; keep one spelling to avoid a duplicate.
  const key = Object.keys(next).find((name) => name.toUpperCase() === 'PATH') ?? 'PATH';
  next[key] = toolSearchDirs({ ...options, env }).join(delimiter);
  return next;
}
