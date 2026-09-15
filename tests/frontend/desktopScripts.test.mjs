// Unit tests for scripts/desktop-common.mjs — the pure helpers behind the
// local desktop launcher scripts (desktop-fresh.mjs / desktop-prod.sh).
//
// The load-bearing guarantees:
//   * every path the fresh script may delete is app-scoped (contains
//     "omnivoice" or "com.debpalash") and lives under the given home dir —
//     a typo can never widen a rm -rf beyond OmniVoice's own traces;
//   * the PATH sanitizer strips exactly the intended dev-tool prefixes and
//     preserves order otherwise;
//   * the env sanitizer removes exactly the HF/OMNIVOICE keys, nothing else.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  APP_ID,
  STRIPPED_PATH_ENTRIES,
  STRIPPED_ENV_VARS,
  UPDATER_ARTIFACTS_OFF,
  hiddenFrom,
  isDevAppProcess,
  isAppScoped,
  macosFreshTraces,
  sanitizedEnv,
  sanitizedPath,
  tauriBuildArgs,
} from '../../scripts/desktop-common.mjs';

const HOME = '/Users/testuser';

test('every fresh-trace path is app-scoped and under home', () => {
  const traces = macosFreshTraces(HOME);
  assert.ok(traces.length >= 8, 'expected the full trace list');
  for (const { path } of traces) {
    assert.ok(path.startsWith(`${HOME}/`), `not under home: ${path}`);
    // The scoping must come from the path BELOW home, not from home itself.
    const rel = path.slice(HOME.length);
    assert.ok(isAppScoped(rel), `not app-scoped below home: ${path}`);
  }
});

test('fresh traces cover every macOS install remnant', () => {
  const paths = macosFreshTraces(HOME).map((t) => t.path);
  const mustCover = [
    `${HOME}/Library/Application Support/${APP_ID}`, // Tauri app data
    `${HOME}/Library/Application Support/OmniVoice`, // backend data
    `${HOME}/Library/Logs/${APP_ID}`,
    `${HOME}/Library/WebKit/${APP_ID}`, // webview localStorage
    `${HOME}/Library/Caches/${APP_ID}`,
    `${HOME}/Library/HTTPStorages/${APP_ID}`,
    `${HOME}/Library/Preferences/${APP_ID}.plist`,
    `${HOME}/Library/Saved Application State/${APP_ID}.savedState`,
  ];
  for (const p of mustCover) {
    assert.ok(paths.includes(p), `missing trace: ${p}`);
  }
  // HTTPStorages must be prefix-matched (dir AND .binarycookies file).
  const http = macosFreshTraces(HOME).find((t) => t.path.includes('HTTPStorages'));
  assert.equal(http.kind, 'prefix');
});

test('isAppScoped accepts app dirs, rejects shared paths', () => {
  assert.ok(isAppScoped(`/x/Library/Caches/${APP_ID}`));
  assert.ok(isAppScoped('/x/Library/Application Support/OmniVoice'));
  assert.ok(isAppScoped('C:/Users/u/AppData/Local/OmniVoice/hf_cache'));
  assert.ok(!isAppScoped(`${HOME}/.cache/huggingface`)); // shared global HF cache
  assert.ok(!isAppScoped(`${HOME}/Library`));
  assert.ok(!isAppScoped(''));
});

test('sanitizedPath strips exactly the dev-tool entries, keeps order', () => {
  const input = [
    '/opt/homebrew/bin',
    '/usr/bin',
    '/opt/homebrew/sbin',
    '/bin',
    '/usr/local/bin',
    '/usr/sbin',
    '/sbin',
  ].join(':');
  assert.equal(sanitizedPath(input), '/usr/bin:/bin:/usr/sbin:/sbin');
});

test('sanitizedPath normalises trailing slashes but not look-alikes', () => {
  assert.equal(
    sanitizedPath('/usr/local/bin/:/usr/bin:/opt/homebrew/bin//'),
    '/usr/bin',
  );
  // Prefix look-alikes and subdirectories must survive.
  assert.equal(
    sanitizedPath('/usr/local/bin-extra:/usr/local/bin/sub:/opt/homebrew/binx'),
    '/usr/local/bin-extra:/usr/local/bin/sub:/opt/homebrew/binx',
  );
  assert.equal(sanitizedPath(''), '');
});

test('sanitizedEnv removes exactly the HF and OMNIVOICE_* keys', () => {
  const input = {
    HOME: '/Users/testuser',
    USER: 'testuser',
    PATH: '/opt/homebrew/bin:/usr/bin:/bin',
    HF_TOKEN: 'x',
    HUGGING_FACE_HUB_TOKEN: 'x',
    HF_HOME: '/tmp/hf',
    HF_HUB_CACHE: '/tmp/hf/hub',
    HF_ENDPOINT: 'https://example.invalid',
    OMNIVOICE_DATA_DIR: '/tmp/ov',
    OMNIVOICE_CACHE_DIR: '/tmp/ovc',
    // Look-alikes that must survive:
    HF_TOKENX: 'keep',
    MY_HF_TOKEN: 'keep',
    OMNIVOICEX: 'keep', // no underscore → not the OMNIVOICE_ prefix
  };
  const out = sanitizedEnv(input);
  for (const key of STRIPPED_ENV_VARS) assert.ok(!(key in out), `should strip ${key}`);
  assert.ok(!('OMNIVOICE_DATA_DIR' in out));
  assert.ok(!('OMNIVOICE_CACHE_DIR' in out));
  assert.equal(out.HOME, '/Users/testuser');
  assert.equal(out.USER, 'testuser');
  assert.equal(out.HF_TOKENX, 'keep');
  assert.equal(out.MY_HF_TOKEN, 'keep');
  assert.equal(out.OMNIVOICEX, 'keep');
  assert.equal(out.PATH, '/usr/bin:/bin'); // PATH sanitized too
  // Input must not be mutated.
  assert.equal(input.HF_TOKEN, 'x');
  assert.equal(input.PATH, '/opt/homebrew/bin:/usr/bin:/bin');
});

test('hiddenFrom reports only what is actually present', () => {
  const { vars, pathEntries } = hiddenFrom({
    PATH: '/usr/bin:/opt/homebrew/bin',
    HF_TOKEN: 'x',
    OMNIVOICE_DATA_DIR: '/tmp',
    HOME: '/Users/testuser',
  });
  assert.deepEqual(vars.sort(), ['HF_TOKEN', 'OMNIVOICE_DATA_DIR']);
  assert.deepEqual(pathEntries, ['/opt/homebrew/bin']);
  const empty = hiddenFrom({ HOME: '/Users/testuser' });
  assert.deepEqual(empty.vars, []);
  assert.deepEqual(empty.pathEntries, []);
});

test('tauriBuildArgs builds only the launched bundle, updater artifacts off', () => {
  const macos = tauriBuildArgs('darwin');
  assert.deepEqual(macos.slice(0, 3), ['tauri', 'build', '--debug']);
  assert.ok(macos.join(' ').includes('--bundles app'));
  assert.deepEqual(tauriBuildArgs('linux').join(' ').includes('--bundles appimage'), true);
  assert.ok(tauriBuildArgs('win32').includes('--no-bundle'));

  for (const platform of ['darwin', 'linux', 'win32']) {
    const args = tauriBuildArgs(platform);
    const config = args[args.indexOf('--config') + 1];
    // The override must parse and target the exact tauri.conf.json key.
    assert.deepEqual(JSON.parse(config), { bundle: { createUpdaterArtifacts: false } });
    assert.equal(config, UPDATER_ARTIFACTS_OFF);
  }
});

test('STRIPPED_PATH_ENTRIES are exactly the intended dev prefixes', () => {
  assert.deepEqual(STRIPPED_PATH_ENTRIES, [
    '/opt/homebrew/bin',
    '/opt/homebrew/sbin',
    '/usr/local/bin',
  ]);
});

// ── Dev-launcher stale-instance guard ──────────────────────────────────────
// `bun desktop` clears a leftover dev app before starting: two instances fight
// over the dev server, and the loser's Vite exits while its window stays open —
// a BLANK app with nothing to explain it (reproduced deterministically). The
// cleanup kills by process name, so the safety boundary is that it matches the
// cargo dev binary ONLY. Killing a user's installed app would be far worse than
// the bug being fixed.

test('stale-instance cleanup matches the dev binary', () => {
  assert.equal(isDevAppProcess('omnivoice-studio'), true);
  assert.equal(isDevAppProcess('omnivoice-studio.exe'), true);
  assert.equal(isDevAppProcess('omnivoice-studio.EXE'), true);
});

test('stale-instance cleanup NEVER matches the installed release app', () => {
  // The release app is "VoiceStudio". The cargo package deliberately kept the
  // old binary name through the rename so the two can never collide.
  assert.equal(isDevAppProcess('VoiceStudio'), false);
  assert.equal(isDevAppProcess('VoiceStudio.exe'), false);
  // The pre-rename names must stay rejected too: a user who has not updated
  // is still running an app called "OmniVoice Studio".
  assert.equal(isDevAppProcess('OmniVoice Studio'), false);
  assert.equal(isDevAppProcess('OmniVoice Studio.exe'), false);
  assert.equal(isDevAppProcess('OmniVoice-Studio.exe'), false);
});

test('stale-instance cleanup ignores unrelated or empty process names', () => {
  for (const name of ['', '   ', 'node', 'chrome.exe', 'omnivoice', 'my-omnivoice-studio']) {
    assert.equal(isDevAppProcess(name), false, `should not match ${name}`);
  }
  assert.equal(isDevAppProcess(undefined), false);
  assert.equal(isDevAppProcess(null), false);
});

// ── Stale-PATH healing shared by every desktop launcher ────────────────────
// `bun desktop` healed a pre-rustup terminal's PATH; `bun desktop-prod` did
// not and died with "cargo metadata … program not found" on the very next
// command. The helper is pure and probe-injected so this pins the contract
// for all launchers without a toolchain on the CI box.
import {
  healToolchainPath,
  healedPathNotes,
  cargoMissingMessage,
  pathKeyOf,
} from '../../scripts/desktop-toolchain-path.mjs';

const winOpts = (overrides) => ({
  platform: 'win32',
  home: 'C:\\Users\\dev',
  ...overrides,
});

test('healToolchainPath prepends installed-but-invisible tool dirs, never mutates input', () => {
  const input = { Path: 'C:\\Windows\\System32', HOME: 'x' };
  const { env, added, missing } = healToolchainPath(
    input,
    winOpts({ resolvable: () => false, exists: () => true }),
  );
  assert.equal(
    env.Path,
    'C:\\Users\\dev\\.local\\bin;C:\\Users\\dev\\.cargo\\bin;C:\\Windows\\System32',
  );
  assert.deepEqual(
    added.map((a) => a.tool),
    ['cargo', 'uv'],
  );
  assert.deepEqual(missing, []);
  assert.equal(input.Path, 'C:\\Windows\\System32'); // untouched
  assert.equal(env.HOME, 'x'); // other keys carried over
});

test('healToolchainPath is a no-op when every tool already resolves', () => {
  const input = { PATH: '/usr/bin' };
  const { env, added, missing } = healToolchainPath(input, {
    platform: 'darwin',
    home: '/Users/dev',
    resolvable: () => true,
    exists: () => {
      throw new Error('must not probe the filesystem when the tool resolves');
    },
  });
  assert.deepEqual(env, input);
  assert.deepEqual(added, []);
  assert.deepEqual(missing, []);
});

test('healToolchainPath reports tools that are genuinely absent, heals the rest', () => {
  const { env, added, missing } = healToolchainPath(
    { PATH: '/usr/bin' },
    {
      platform: 'linux',
      home: '/home/dev',
      resolvable: () => false,
      exists: (exe) => exe.endsWith('/.local/bin/uv'), // uv installed, cargo not
    },
  );
  assert.equal(env.PATH, '/home/dev/.local/bin:/usr/bin');
  assert.deepEqual(added, [{ tool: 'uv', dir: '/home/dev/.local/bin' }]);
  assert.deepEqual(missing, ['cargo']);
});

test('healToolchainPath probes the platform-specific executable name', () => {
  const probed = [];
  healToolchainPath(
    { PATH: '' },
    winOpts({
      resolvable: () => false,
      exists: (exe) => {
        probed.push(exe);
        return false;
      },
    }),
  );
  assert.deepEqual(probed, [
    'C:\\Users\\dev\\.cargo\\bin\\cargo.exe',
    'C:\\Users\\dev\\.local\\bin\\uv.exe',
  ]);
});

test('pathKeyOf matches the PATH key case-insensitively and defaults to PATH', () => {
  assert.equal(pathKeyOf({ Path: '' }), 'Path');
  assert.equal(pathKeyOf({ PATH: '' }), 'PATH');
  assert.equal(pathKeyOf({}), 'PATH');
});

test('launcher messages name the tag, the permanent remedy, and the install path', () => {
  const [note] = healedPathNotes([{ tool: 'cargo', dir: '/home/dev/.cargo/bin' }], 'desktop-prod');
  assert.match(note, /^\[desktop-prod\] added '\/home\/dev\/\.cargo\/bin' to PATH/);
  assert.match(note, /Open a new terminal/);
  const msg = cargoMissingMessage('`bun desktop-prod`');
  assert.match(msg, /`bun desktop-prod` needs Rust\/cargo/);
  assert.match(msg, /winget install Rustlang\.Rustup/);
  assert.match(msg, /rustup\.rs/);
});
