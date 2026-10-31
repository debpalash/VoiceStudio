import { describe, expect, it } from 'vitest';
import {
  backendCauseAction,
  backendFailureCause,
  backendFailureHints,
  inSyncedFolder,
} from './backendHint';

// #2440 / #2445 / #2465: the three recurring "backend would not start" reports
// each have one fix that the raw message never names. The hint maps the stable
// message shape to localized advice and must stay silent on anything else.
describe('backendFailureHints', () => {
  it('explains a launch the OS refused (#2440)', () => {
    expect(
      backendFailureHints(
        'Could not start C:\\runtime\\python.exe: spawn UNKNOWN. Install or repair the local runtime, then restart VoiceStudio.',
      ),
    ).toEqual(['backend.hint_spawn_blocked']);
    expect(backendFailureHints('Could not start /x/python: spawn EACCES')).toEqual([
      'backend.hint_spawn_blocked',
    ]);
  });

  it('names Smart App Control instead of suggesting an antivirus exclusion (#3224)', () => {
    expect(
      backendFailureHints(
        'Could not start C:\\runtime\\python.exe: spawn UNKNOWN. Windows Smart App Control blocked it.',
      ),
    ).toEqual(['backend.hint_smart_app_control']);
  });

  it('explains a startup budget that ran out on a slow host (#2445)', () => {
    expect(
      backendFailureHints(
        'Backend did not answer on port 3900 within 600 s (OMNIVOICE_STARTUP_BUDGET_S). It printed no output.',
      ),
    ).toEqual(['backend.hint_slow_start']);
  });

  it('still classifies a silent timeout that quotes the backend log tail', () => {
    expect(
      backendFailureHints(
        "Backend did not answer on port 3900 within 300 s (OMNIVOICE_STARTUP_BUDGET_S). It printed no output. The backend's own log (~/.omnivoice/omnivoice.log) ends with:\nINFO: loading torch\nSlow disks and antivirus scanning are the usual cause.",
      ),
    ).toEqual(['backend.hint_slow_start']);
  });

  it('flags a path inside a cloud-synced folder (#2465)', () => {
    expect(
      backendFailureHints(
        'The Python environment in C:\\Users\\a\\OneDrive\\Desktop\\VoiceStudio is missing or incomplete.',
      ),
    ).toEqual(['backend.hint_synced_folder']);
  });

  it('adds nothing for failures it does not recognise', () => {
    expect(backendFailureHints(undefined)).toEqual([]);
    expect(backendFailureHints('')).toEqual([]);
    expect(backendFailureHints('Backend exited unexpectedly (exit code 1).')).toEqual([]);
    // A crash that merely mentions a timeout is not a slow start.
    expect(backendFailureHints('Backend exited unexpectedly. Last output: timed out')).toEqual([]);
  });
});

describe('backendFailureCause', () => {
  it.each([
    ['Could not start the backend: spawn UNKNOWN', 'spawn_blocked'],
    ['Could not start the backend: spawn UNKNOWN (Smart App Control)', 'smart_app_control'],
    // #3209: a refused DLL load names its blocker, so the advice matches it.
    [
      "Backend exited (exit code 1): Windows refused to load one of the engine's files. Windows Smart App Control blocked it. Last output: ImportError: DLL load failed while importing _sqlite3: An Application Control policy has blocked this file.",
      'smart_app_control',
    ],
    [
      "Backend exited (exit code 1): Windows refused to load one of the engine's files. Your antivirus blocked it.",
      'spawn_blocked',
    ],
    ['Backend exited unexpectedly (exit code 3221225477).', 'native_crash'],
    ['Backend exited unexpectedly (exit code -1073741819).', 'native_crash'],
    ['Backend exited unexpectedly (signal SIGSEGV).', 'native_crash'],
    ['The Python environment in C:\\VoiceStudio is missing or incomplete.', 'missing_env'],
    [
      'The Python environment in C:\\Users\\a\\OneDrive\\VS is missing or incomplete.',
      'synced_folder',
    ],
    [
      'The Python environment in C:\\VoiceStudio is missing (no .venv\\Scripts\\python.exe). Install it.',
      'missing_env',
    ],
    [
      'The Python environment in C:\\VoiceStudio is incomplete: ImportError: torch. Install it.',
      'missing_env',
    ],
    ['EPERM: operation not permitted, mkdir', 'permission'],
    ['Backend did not answer on port 3900 within 600 s', 'slow_start'],
  ])('names %s', (message, cause) => expect(backendFailureCause(message)).toBe(cause));

  it('yields null for an unrecognised failure', () => {
    expect(backendFailureCause(undefined)).toBeNull();
    expect(backendFailureCause('Backend exited unexpectedly (exit code 1).')).toBeNull();
  });
});

describe('inSyncedFolder', () => {
  it.each([
    'C:\\Users\\a\\OneDrive\\Desktop\\VoiceStudio',
    'C:\\Users\\a\\OneDrive - Contoso\\VoiceStudio',
    '/Users/a/Dropbox/VoiceStudio',
    '/Users/a/Library/Mobile Documents/iCloud Drive/VoiceStudio',
    'G:\\Google Drive\\VoiceStudio',
  ])('detects %s', (path) => expect(inSyncedFolder(path)).toBe(true));

  it.each(['C:\\VoiceStudio', '/home/a/voicestudio', '/home/a/Documents/onedrive-notes-app'])(
    'ignores %s',
    (path) => expect(inSyncedFolder(path)).toBe(false),
  );
});

describe('backendCauseAction', () => {
  it('offers moving the environment only for an app-managed runtime (#3480)', () => {
    for (const cause of ['missing_env', 'permission', 'synced_folder'] as const) {
      expect(backendCauseAction(cause, { managedRuntime: true })).toBe('choose_folder');
      expect(backendCauseAction(cause, { managedRuntime: false })).toBe('retry');
    }
    expect(backendCauseAction('native_crash', { managedRuntime: false })).toBe('copy_diagnostics');
    expect(backendCauseAction(null, { managedRuntime: true })).toBe('retry');
  });
});
