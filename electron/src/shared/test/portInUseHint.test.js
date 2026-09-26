/**
 * #1223 — "Backend died (exit code 1)" when port 3900 was already taken.
 *
 * The backend log said:
 *   ERROR: [Errno 10048] error while attempting to bind on address
 *   ('127.0.0.1', 3900): обычно разрешается только одно использование адреса…
 *
 * Two reasons the user got nothing useful:
 *
 *  - `detectHints` only matched /port.*in use|address.*in use/. Windows'
 *    WSAEADDRINUSE wording ("only one usage of each socket address is normally
 *    permitted") contains NEITHER phrase — and Windows translates it into the
 *    user's locale, so no English phrase can be relied on at all. The correct
 *    hint string existed in en.json and was simply unreachable on Windows.
 *  - `crashCauseHint` had no branch for it, so a port conflict was described
 *    with the small-GPU VRAM guidance.
 *
 * Both are pinned here on the locale-independent signals: the errno and the
 * backend's dedicated exit code.
 */
import { describe, it, expect } from 'vitest';
import { detectHints } from '../components/BootstrapSplash';
import { crashCauseHint } from '../utils/backendCrash';

const line = (s) => [{ line: s }];

describe('detectHints — port-in-use (#1223)', () => {
  it('matches the Windows errno even when the message is localised', () => {
    // The reporter's actual log line, Russian text and all.
    const russian =
      "ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', 3900): " +
      'обычно разрешается только одно использование адреса сокета';
    expect(detectHints('', line(russian))).toContain('bootstrap.hint_port');
  });

  it('matches the English Windows wording', () => {
    const english =
      "[Errno 10048] error while attempting to bind on address ('127.0.0.1', 3900): " +
      'only one usage of each socket address (protocol/network address/port) is normally permitted';
    expect(detectHints('', line(english))).toContain('bootstrap.hint_port');
  });

  it.each([
    ['macOS/BSD', "[Errno 48] error while attempting to bind on address ('127.0.0.1', 3900)"],
    ['Linux', "[Errno 98] error while attempting to bind on address ('127.0.0.1', 3900)"],
  ])('matches the %s errno', (_os, msg) => {
    expect(detectHints('', line(msg))).toContain('bootstrap.hint_port');
  });

  it("matches the backend's dedicated exit code with no log at all", () => {
    // The crash path where stderr was never captured — the exit code is the
    // only signal left.
    expect(detectHints('Backend process exited (exit code 78)')).toContain('bootstrap.hint_port');
  });

  it('still matches the pre-existing English phrasings', () => {
    expect(detectHints('', line('address already in use'))).toContain('bootstrap.hint_port');
    expect(detectHints('', line('Port 3900 is in use'))).toContain('bootstrap.hint_port');
  });

  it('does not fire on unrelated failures', () => {
    expect(detectHints('', line('uv sync failed'))).not.toContain('bootstrap.hint_port');
    // The generic fallback must still be the only hint here.
    expect(detectHints('something else entirely')).toEqual(['bootstrap.hint_default']);
  });
});

describe('crashCauseHint — port conflict is not a memory problem (#1223)', () => {
  it('explains the port conflict for the dedicated exit code', () => {
    const hint = crashCauseHint({ exit_code: 78, signal: null });
    expect(hint).toMatch(/port 3900 is already in use/i);
    expect(hint).not.toMatch(/VRAM|RAM/);
  });

  it('leaves the OS-OOM branch alone', () => {
    expect(crashCauseHint({ exit_code: null, signal: 9 })).toMatch(/ran out of/i);
  });

  it('leaves the default VRAM branch alone', () => {
    expect(crashCauseHint({ exit_code: 1, signal: null })).toMatch(/VRAM/);
  });
});

describe('the Rust failure messages reach the localised hint (#1223, #1933)', () => {
  // Every port-conflict failure bootstrap.rs emits now comes from one builder,
  // `backend::port_conflict_message`, which words it by WHO holds the port
  // (#1933) — the user's own orphaned backend is not "another application",
  // and telling them to quit a window that does not exist gave them nothing.
  //
  // These are English, as every Rust-side failure message is. They only need
  // to be MATCHABLE: detectHints turns them into `bootstrap.hint_port`, which
  // IS localised. A rewording the matcher misses costs the user the translated
  // guidance — the #1223 failure mode, one layer up. The Rust side pins the
  // same contract on the builder itself.
  it.each([
    [
      'our own orphan',
      'Port 3900 is in use by a VoiceStudio backend from an earlier session that never shut ' +
        'down. It has no window to quit, so closing VoiceStudio will not release it. End it ' +
        'Find and end it from a terminal:\n\n    lsof -nP -iTCP:3900 -sTCP:LISTEN',
    ],
    [
      'a backend from another version',
      'Port 3900 is in use by a VoiceStudio backend from version 0.1.0, left running by an ' +
        'earlier install. This build is 0.5.2, so it cannot use that one. End it from a ' +
        'terminal:\n\n    lsof -nP -iTCP:3900 -sTCP:LISTEN',
    ],
    [
      'an unidentified listener',
      'Port 3900 is already in use by another application, and VoiceStudio could not free it. ' +
        'Quit whatever is using that port and try again.',
    ],
    [
      'with the respawn suffix',
      'Port 3900 is already in use by another application, and VoiceStudio could not free it. ' +
        'Quit whatever is using that port and try again.\n\nThe backend cannot restart until ' +
        'that port is free.',
    ],
  ])('%s maps to the localised port hint', (_which, message) => {
    expect(detectHints(message)).toContain('bootstrap.hint_port');
  });
});
