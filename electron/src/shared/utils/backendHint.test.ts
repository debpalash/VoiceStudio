import { describe, expect, it } from 'vitest';
import { backendFailureHints, inSyncedFolder } from './backendHint';

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

  it('explains a startup budget that ran out on a slow host (#2445)', () => {
    expect(
      backendFailureHints(
        'Backend did not answer on port 3900 within 600 s (OMNIVOICE_STARTUP_BUDGET_S). It printed no output.',
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
