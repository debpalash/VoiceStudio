import {
  appendFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readlinkSync,
  statSync,
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
  /** The user's login shell (`$SHELL`), so a zsh user without `.zshrc` still gets one. */
  shell?: string;
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
const PROFILE_BLOCK =
  PROFILE_MARKER +
  'case ":$PATH:" in\n    *":$HOME/.local/bin:"*) ;;\n' +
  '    *) export PATH="$HOME/.local/bin:$PATH" ;;\nesac\n';
const FISH_LINE = 'fish_add_path --path "$HOME/.local/bin"\n';
// Bash login shells read the first of .bash_profile, .bash_login and .profile;
// existing ones are configured, never created (a new one would hide .profile).
const PROFILES = ['.profile', '.bash_profile', '.bash_login', '.bashrc', '.zshrc'];

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
      (file === '.zshrc' &&
        (options.platform === 'darwin' || basename(options.shell ?? '') === 'zsh')) ||
      existsSync(join(options.home, file)),
  ).map((file) => join(options.home, file));
  if (existsSync(join(options.home, '.config/fish')))
    files.push(join(options.home, '.config/fish/conf.d/voicestudio.fish'));
  return files;
}

/** The profile puts `~/.local/bin` on PATH: a marker left by an interrupted write does not. */
function profileConfigured(file: string): boolean {
  try {
    return readFileSync(file, 'utf8').includes(file.endsWith('.fish') ? FISH_LINE : PROFILE_BLOCK);
  } catch {
    return false;
  }
}

function onSearchPath(options: TerminalCommandOptions): boolean {
  return (options.path ?? '').split(':').includes(join(options.home, '.local/bin'));
}

function commandPath(home: string): string {
  return join(home, '.local/bin', 'voicestudio');
}

/**
 * The command already runs `target` without a link of ours: on a
 * case-insensitive disk `~/.local/bin/voicestudio` is the shell installer's
 * `~/.local/bin/VoiceStudio` AppImage, whose entry point is this command.
 */
function isSameFile(command: string, target: string): boolean {
  try {
    const a = statSync(command);
    const b = statSync(target);
    return a.dev === b.dev && a.ino === b.ino;
  } catch {
    return false;
  }
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
    if (isSameFile(command, target)) return 'linked';
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
  linked ||= Boolean(target) && isSameFile(command, target as string);
  // Every profile must carry the block: a setup that stopped halfway is not done.
  const configured = profilesToConfigure(options).every(profileConfigured);
  return {
    supported: Boolean(target),
    linked,
    onPath: configured || onSearchPath(options),
    command,
  };
}

/** The user's explicit "add to my terminal" action: idempotent profile lines. */
export function addTerminalCommandToPath(options: TerminalCommandOptions): TerminalCommandStatus {
  if (linkTerminalCommand(options) !== 'linked') return terminalCommandStatus(options);
  // Idempotent: a run that failed halfway is completed by the next one.
  for (const profile of profilesToConfigure(options)) {
    if (profileConfigured(profile)) continue;
    if (profile.endsWith('.fish')) {
      // VoiceStudio owns this conf.d file; rewrite an empty or truncated one.
      mkdirSync(dirname(profile), { recursive: true });
      writeFileSync(profile, FISH_LINE);
    } else {
      appendFileSync(profile, '\n' + PROFILE_BLOCK);
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
  if (linkTerminalCommand(options) !== 'linked' || setupRecorded(stateFile))
    return terminalCommandStatus(options);
  // Profiles are (re)written unless new shells already search ~/.local/bin; the
  // state file is written only after every profile write succeeded.
  const next = onSearchPath(options)
    ? terminalCommandStatus(options)
    : addTerminalCommandToPath(options);
  try {
    // Exclusive create: a second instance that finished first already recorded it.
    writeFileSync(stateFile, `${JSON.stringify({ schema: 1, configured: true })}\n`, {
      flag: 'wx',
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
  }
  return next;
}

/** Whether first-launch setup already ran; any state other than "absent" counts. */
function setupRecorded(stateFile: string): boolean {
  try {
    readFileSync(stateFile);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== 'ENOENT';
  }
}
