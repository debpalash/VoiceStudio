import {
  appendFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readlinkSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';

export type TerminalCommandOptions = {
  platform: NodeJS.Platform;
  home: string;
  resources: string;
  appImage?: string;
  snap?: string;
  path?: string;
};

export type TerminalCommandStatus = {
  /** This installation can publish `~/.local/bin/voicestudio` itself. */
  supported: boolean;
  /** The command in `~/.local/bin` runs this installation. */
  linked: boolean;
  /** New shells find `~/.local/bin`, already or through VoiceStudio's profile lines. */
  onPath: boolean;
  command: string;
};

const PROFILE_MARKER = '# VoiceStudio terminal command\n';
const PROFILE_LINES =
  '\n' +
  PROFILE_MARKER +
  'case ":$PATH:" in\n    *":$HOME/.local/bin:"*) ;;\n' +
  '    *) export PATH="$HOME/.local/bin:$PATH" ;;\nesac\n';
// Bash login shells read .bash_profile instead of .profile when it exists.
const PROFILES = ['.profile', '.bash_profile', '.bashrc', '.zshrc'];

function commandTarget(options: TerminalCommandOptions): string | null {
  // Windows installers own PATH; Snap exposes its own command; a translocated
  // or mounted app disappears, so a link to it would dangle.
  if (
    options.platform === 'win32' ||
    options.snap ||
    options.resources.includes('/AppTranslocation/') ||
    options.resources.startsWith('/Volumes/')
  )
    return null;
  const target =
    options.platform === 'darwin' ? join(options.resources, 'cli/voicestudio') : options.appImage;
  return target && existsSync(target) ? target : null;
}

function commandPath(home: string): string {
  return join(home, '.local/bin', 'voicestudio');
}

/** A dangling link left by a moved or replaced VoiceStudio, never someone else's command. */
function isStaleOwnLink(command: string): boolean {
  // A relative target is relative to the link's folder, not the working directory.
  const target = resolve(dirname(command), readlinkSync(command));
  if (existsSync(target)) return false;
  return (
    target.endsWith('/Contents/Resources/cli/voicestudio') ||
    /^VoiceStudio.*\.AppImage$/i.test(basename(target))
  );
}

/**
 * DMG/AppImage users get the same command as installer users, without sudo.
 * Runs at startup and only manages the link; shell profiles change only on an
 * explicit request (`addTerminalCommandToPath`).
 */
export function linkTerminalCommand(
  options: TerminalCommandOptions,
): 'linked' | 'existing' | 'unavailable' {
  const target = commandTarget(options);
  if (!target) return 'unavailable';
  const command = commandPath(options.home);
  mkdirSync(join(options.home, '.local/bin'), { recursive: true });
  try {
    const stat = lstatSync(command);
    if (stat.isSymbolicLink() && readlinkSync(command) === target) return 'linked';
    // Never replace uv's command, a user's launcher or another installation.
    if (!stat.isSymbolicLink() || !isStaleOwnLink(command)) return 'existing';
    unlinkSync(command);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  symlinkSync(target, command);
  return 'linked';
}

export function terminalCommandStatus(options: TerminalCommandOptions): TerminalCommandStatus {
  const target = commandTarget(options);
  const command = commandPath(options.home);
  let linked = false;
  try {
    linked = Boolean(target) && readlinkSync(command) === target;
  } catch {
    linked = false;
  }
  const directory = join(options.home, '.local/bin');
  const configured = PROFILES.some((file) => {
    try {
      return readFileSync(join(options.home, file), 'utf8').includes(PROFILE_MARKER);
    } catch {
      return false;
    }
  });
  return {
    supported: Boolean(target),
    linked,
    onPath: configured || (options.path ?? '').split(':').includes(directory),
    command,
  };
}

/** The user's explicit "add to my terminal" action: idempotent profile lines. */
export function addTerminalCommandToPath(options: TerminalCommandOptions): TerminalCommandStatus {
  if (linkTerminalCommand(options) !== 'linked') return terminalCommandStatus(options);
  for (const file of PROFILES) {
    const profile = join(options.home, file);
    const exists = existsSync(profile);
    // Do not create a profile for a shell the user does not use, except the
    // POSIX login profile and, on macOS, zsh's (its default shell skips .profile).
    if (!exists && file !== '.profile' && !(options.platform === 'darwin' && file === '.zshrc'))
      continue;
    if (!exists || !readFileSync(profile, 'utf8').includes(PROFILE_MARKER))
      appendFileSync(profile, PROFILE_LINES);
  }
  const fishDirectory = join(options.home, '.config/fish');
  if (existsSync(fishDirectory)) {
    mkdirSync(join(fishDirectory, 'conf.d'), { recursive: true });
    const fishProfile = join(fishDirectory, 'conf.d/voicestudio.fish');
    if (!existsSync(fishProfile))
      writeFileSync(fishProfile, 'fish_add_path --path "$HOME/.local/bin"\n');
  }
  return terminalCommandStatus(options);
}
