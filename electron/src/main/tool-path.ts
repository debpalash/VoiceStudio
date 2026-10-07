import { readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { delimiter, join } from 'node:path';

interface ToolPathOptions {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  home?: string;
}

function nvmBinDirs(home: string): string[] {
  const root = join(home, '.nvm', 'versions', 'node');
  try {
    return readdirSync(root)
      .sort()
      .reverse()
      .map((version) => join(root, version, 'bin'));
  } catch {
    return [];
  }
}

/**
 * Directories to search for user-installed CLIs (uv, claude, codex, ...).
 *
 * A GUI launch (Finder, Explorer, a .desktop file) does not see the shell's
 * PATH additions, so the inherited PATH alone misses the standard user-level
 * install locations. Inherited entries stay first so an explicit PATH wins.
 */
export function toolSearchDirs({
  env = process.env,
  platform = process.platform,
  home = homedir(),
}: ToolPathOptions = {}): string[] {
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
      : [
          '/opt/homebrew/bin',
          '/usr/local/bin',
          join(home, '.npm-global', 'bin'),
          ...nvmBinDirs(home),
        ];
  return [...new Set([...inherited, ...common, ...extra])];
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
