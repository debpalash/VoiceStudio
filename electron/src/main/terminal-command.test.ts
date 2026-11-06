// @vitest-environment node
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readlinkSync,
  symlinkSync,
  rmSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import {
  addTerminalCommandToPath,
  linkTerminalCommand,
  setUpTerminalCommand,
  terminalCommandStatus,
} from './terminal-command';

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function fixture() {
  const home = mkdtempSync(join(tmpdir(), 'vs-path-'));
  roots.push(home);
  const resources = join(home, 'VoiceStudio.app/Contents/Resources');
  mkdirSync(join(resources, 'cli'), { recursive: true });
  writeFileSync(join(resources, 'cli/voicestudio'), 'fixture');
  return { home, resources, path: '/usr/bin', platform: 'darwin' as const };
}

test('startup links the bundled CLI without touching shell profiles', () => {
  const f = fixture();
  writeFileSync(join(f.home, '.zshrc'), '# existing preferences\n');
  expect(linkTerminalCommand(f)).toBe('linked');
  expect(readlinkSync(join(f.home, '.local/bin/voicestudio'))).toBe(
    join(f.resources, 'cli/voicestudio'),
  );
  expect(readFileSync(join(f.home, '.zshrc'), 'utf8')).toBe('# existing preferences\n');
  expect(existsSync(join(f.home, '.profile'))).toBe(false);
  expect(terminalCommandStatus(f)).toMatchObject({ supported: true, linked: true, onPath: false });
});

test('adding to PATH is explicit, idempotent and keeps profile content', () => {
  const f = fixture();
  writeFileSync(join(f.home, '.zshrc'), '# existing preferences\n');
  expect(addTerminalCommandToPath(f)).toMatchObject({ linked: true, onPath: true });
  addTerminalCommandToPath(f);
  const zsh = readFileSync(join(f.home, '.zshrc'), 'utf8');
  expect(zsh).toContain('# existing preferences\n');
  expect(zsh.match(/# VoiceStudio terminal command/g)).toHaveLength(1);
  expect(readFileSync(join(f.home, '.profile'), 'utf8')).toContain('.local/bin');
  // Shells the user does not have get no new configuration files.
  expect(existsSync(join(f.home, '.bashrc'))).toBe(false);
  expect(existsSync(join(f.home, '.config/fish'))).toBe(false);
});

test('macOS gets a zsh profile even when the user has none', () => {
  const f = fixture();
  addTerminalCommandToPath(f);
  expect(readFileSync(join(f.home, '.zshrc'), 'utf8')).toContain('.local/bin');
  const linux = fixture();
  const appImage = join(linux.home, 'VoiceStudio.AppImage');
  writeFileSync(appImage, 'fixture');
  mkdirSync(join(linux.home, '.config/fish'), { recursive: true });
  addTerminalCommandToPath({ ...linux, platform: 'linux', appImage });
  expect(existsSync(join(linux.home, '.zshrc'))).toBe(false);
  expect(readFileSync(join(linux.home, '.config/fish/conf.d/voicestudio.fish'), 'utf8')).toContain(
    'fish_add_path',
  );
});

test('preserves custom wrappers and commands belonging to uv or another installation', () => {
  const f = fixture();
  mkdirSync(join(f.home, '.local/bin'), { recursive: true });
  const command = join(f.home, '.local/bin/voicestudio');
  writeFileSync(command, 'existing wrapper');
  expect(linkTerminalCommand(f)).toBe('existing');
  expect(addTerminalCommandToPath(f).linked).toBe(false);
  expect(readFileSync(command, 'utf8')).toBe('existing wrapper');
  expect(existsSync(join(f.home, '.profile'))).toBe(false);
  rmSync(command);
  symlinkSync('/missing/other-installation', command);
  expect(linkTerminalCommand(f)).toBe('existing');
  expect(readlinkSync(command)).toBe('/missing/other-installation');
});

test('repairs its own link after the AppImage moved, never a live one', () => {
  const f = fixture();
  const appImage = join(f.home, 'Apps/VoiceStudio-0.5.9-linux-x64.AppImage');
  mkdirSync(join(f.home, 'Apps'));
  writeFileSync(appImage, 'fixture');
  mkdirSync(join(f.home, '.local/bin'), { recursive: true });
  const command = join(f.home, '.local/bin/voicestudio');
  symlinkSync(join(f.home, 'Downloads/VoiceStudio-0.5.8-linux-x64.AppImage'), command);
  expect(linkTerminalCommand({ ...f, platform: 'linux', appImage })).toBe('linked');
  expect(readlinkSync(command)).toBe(appImage);
  const other = join(f.home, 'Other/VoiceStudio.AppImage');
  mkdirSync(join(f.home, 'Other'));
  writeFileSync(other, 'fixture');
  expect(linkTerminalCommand({ ...f, platform: 'linux', appImage: other })).toBe('existing');
  expect(readlinkSync(command)).toBe(appImage);
});

test('AppImage links the persistent image, rather than its temporary mount', () => {
  const f = fixture();
  const appImage = join(f.home, 'VoiceStudio.AppImage');
  writeFileSync(appImage, 'fixture');
  expect(linkTerminalCommand({ ...f, platform: 'linux', appImage })).toBe('linked');
  expect(readlinkSync(join(f.home, '.local/bin/voicestudio'))).toBe(appImage);
  expect(linkTerminalCommand({ ...f, platform: 'linux', snap: '/snap/voicestudio/current' })).toBe(
    'unavailable',
  );
  expect(linkTerminalCommand({ ...f, platform: 'win32' })).toBe('unavailable');
  expect(linkTerminalCommand({ ...f, platform: 'linux' })).toBe('unavailable');
  expect(terminalCommandStatus({ ...f, platform: 'win32' }).supported).toBe(false);
});

test('a live relative link is kept, whatever the working directory', () => {
  const f = fixture();
  const appImage = join(f.home, 'Apps/VoiceStudio-new.AppImage');
  mkdirSync(join(f.home, 'Apps'));
  writeFileSync(appImage, 'fixture');
  writeFileSync(join(f.home, 'Apps/VoiceStudio-mine.AppImage'), 'user-managed');
  mkdirSync(join(f.home, '.local/bin'), { recursive: true });
  const command = join(f.home, '.local/bin/voicestudio');
  symlinkSync('../../Apps/VoiceStudio-mine.AppImage', command);
  expect(linkTerminalCommand({ ...f, platform: 'linux', appImage })).toBe('existing');
  expect(readlinkSync(command)).toBe('../../Apps/VoiceStudio-mine.AppImage');
});

test('bash login shells that read .bash_profile get the PATH entry there', () => {
  const f = fixture();
  const appImage = join(f.home, 'VoiceStudio.AppImage');
  writeFileSync(appImage, 'fixture');
  writeFileSync(join(f.home, '.bash_profile'), '# login settings\n');
  addTerminalCommandToPath({ ...f, platform: 'linux', appImage });
  const profile = readFileSync(join(f.home, '.bash_profile'), 'utf8');
  expect(profile).toContain('# login settings\n');
  expect(profile).toContain('.local/bin');
});

test('first launch makes the command work in new terminals, like the installers do', () => {
  const f = fixture();
  const state = join(f.home, 'terminal-command.json');
  expect(setUpTerminalCommand(f, state)).toMatchObject({ linked: true, onPath: true });
  expect(readFileSync(join(f.home, '.zshrc'), 'utf8')).toContain('.local/bin');
  expect(existsSync(state)).toBe(true);
});

test('first launch leaves profiles alone when new shells already find the command', () => {
  const f = { ...fixture(), path: '' };
  f.path = `/usr/bin:${join(f.home, '.local/bin')}`;
  const state = join(f.home, 'terminal-command.json');
  expect(setUpTerminalCommand(f, state)).toMatchObject({ linked: true, onPath: true });
  expect(existsSync(join(f.home, '.zshrc'))).toBe(false);
  expect(existsSync(join(f.home, '.profile'))).toBe(false);
});

test('setup runs once: lines the user removed are not added back', () => {
  const f = fixture();
  const state = join(f.home, 'terminal-command.json');
  setUpTerminalCommand(f, state);
  writeFileSync(join(f.home, '.zshrc'), '# my own setup\n');
  rmSync(join(f.home, '.profile'));
  expect(setUpTerminalCommand(f, state).onPath).toBe(false);
  expect(readFileSync(join(f.home, '.zshrc'), 'utf8')).toBe('# my own setup\n');
  expect(existsSync(join(f.home, '.profile'))).toBe(false);
});

test('setup never edits profiles for a command owned by another installation', () => {
  const f = fixture();
  mkdirSync(join(f.home, '.local/bin'), { recursive: true });
  writeFileSync(join(f.home, '.local/bin/voicestudio'), 'uv tool');
  const state = join(f.home, 'terminal-command.json');
  expect(setUpTerminalCommand(f, state)).toMatchObject({ linked: false });
  expect(existsSync(join(f.home, '.zshrc'))).toBe(false);
  expect(existsSync(state)).toBe(false);
});

test('a setup that failed halfway is finished on the next launch, not marked done', () => {
  const f = fixture();
  const state = join(f.home, 'terminal-command.json');
  // The first launch wrote .profile, then failed before .zshrc.
  writeFileSync(join(f.home, '.profile'), '\n# VoiceStudio terminal command\nexport PATH\n');
  expect(terminalCommandStatus(f).onPath).toBe(false); // Settings offers the repair
  expect(setUpTerminalCommand(f, state)).toMatchObject({ onPath: true });
  expect(readFileSync(join(f.home, '.zshrc'), 'utf8')).toContain('.local/bin');
  expect(readFileSync(join(f.home, '.profile'), 'utf8').match(/# VoiceStudio/g)).toHaveLength(1);
});
