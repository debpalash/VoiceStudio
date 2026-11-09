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

/** The profile files "Add to terminal" writes, so each must carry the block. */
function profilesToConfigure(options: TerminalCommandOptions): string[] {
  const files = PROFILES.filter(
    (file) =>
      // Do not create a profile for a shell the user does not use, except the
      // POSIX login profile and, on macOS, zsh's (its default shell skips .profile).
      file === '.profile' ||
      (options.platform === 'darwin' && file === '.zshrc') ||
      existsSync(join(options.home, file)),
  ).map((file) => join(options.home, file));
  if (existsSync(join(options.home, '.config/fish')))
    files.push(join(options.home, '.config/fish/conf.d/voicestudio.fish'));
  return files;
}

function onSearchPath(options: TerminalCommandOptions): boolean {
  return (options.path ?? '').split(':').includes(join(options.home, '.local/bin'));
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
  // Every profile must carry the block: a setup that stopped halfway is not done.
  const configured = profilesToConfigure(options).every((file) => {
    try {
      const text = readFileSync(file, 'utf8');
      return file.endsWith('.fish') || text.includes(PROFILE_MARKER);
    } catch {
      return false;
    }
  });
  return { supported: Boolean(target), linked, onPath: configured || onSearchPath(options), command };
}

/** The user's explicit "add to my terminal" action: idempotent profile lines. */
export function addTerminalCommandToPath(options: TerminalCommandOptions): TerminalCommandStatus {
  if (linkTerminalCommand(options) !== 'linked') return terminalCommandStatus(options);
  // Idempotent: a run that failed halfway is completed by the next one.
  for (const profile of profilesToConfigure(options)) {
    if (profile.endsWith('.fish')) {
      mkdirSync(dirname(profile), { recursive: true });
      if (!existsSync(profile)) writeFileSync(profile, 'fish_add_path --path "$HOME/.local/bin"\n');
    } else if (!existsSync(profile) || !readFileSync(profile, 'utf8').includes(PROFILE_MARKER)) {
      appendFileSync(profile, PROFILE_LINES);
    }
  }
  return terminalCommandStatus(options);
}

/**
 * First-launch setup for direct downloads (DMG, AppImage), matching what the
 * Windows installer and Linux packages do: `voicestudio` works in a new
 * terminal without visiting Settings. Profiles are edited once, only when new
 * shells would not already find `~/.local/bin`; `stateFile` records that the
 * setup ran, so a user who later removes the lines is not overridden.
 */
export function setUpTerminalCommand(
  options: TerminalCommandOptions,
  stateFile: string,
): TerminalCommandStatus {
  if (linkTerminalCommand(options) !== 'linked' || existsSync(stateFile))
    return terminalCommandStatus(options);
  // Profiles are (re)written unless new shells already search ~/.local/bin; the
  // state file is written only after every profile write succeeded.
  const next = onSearchPath(options)
    ? terminalCommandStatus(options)
    : addTerminalCommandToPath(options);
  writeFileSync(stateFile, `${JSON.stringify({ schema: 1, configured: true })}\n`);
  return next;
}
