// #1188: backend errors carrying a machine-readable "[code]" marker are
// user-fixable input problems, not bugs. toastErrorWithReport must map the
// [clone_ref_unusable] marker (emitted by omnivoice/utils/audio.py when a
// clone reference clip has genuinely no audio) to the localized guidance in
// tts_errors.ref_audio_unusable — a plain toast, no "Report this bug" action.
// Pins both halves of the cross-layer contract: the marker string and the
// i18n key existing in every locale.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const { toastMock, toastErrorMock } = vi.hoisted(() => {
  const error = vi.fn();
  const mock = Object.assign(vi.fn(), { error, dismiss: vi.fn() });
  return { toastMock: mock, toastErrorMock: error };
});
vi.mock('react-hot-toast', () => ({ default: toastMock, toast: toastMock }));
vi.mock('i18next', () => ({ default: { t: (k) => `t:${k}` } }));
vi.mock('../api/external', () => ({ openExternal: vi.fn() }));
vi.mock('../utils/bugReport', () => ({ openBugReport: vi.fn() }));

import { toastErrorWithReport } from '../utils/errorToast';

const BACKEND_ERROR =
  '400 Bad Request: [clone_ref_unusable] Reference audio has no usable sound — ' +
  'the clip is empty or completely silent, so there is no voice to clone.';

describe('toastErrorWithReport user-fixable marker mapping (#1188)', () => {
  beforeEach(() => {
    toastErrorMock.mockClear();
  });

  it('shows the localized guidance for [clone_ref_unusable] instead of the raw detail', () => {
    toastErrorWithReport(`Error: ${BACKEND_ERROR}`, new Error(BACKEND_ERROR));
    expect(toastErrorMock).toHaveBeenCalledTimes(1);
    expect(toastErrorMock).toHaveBeenCalledWith('t:tts_errors.ref_audio_unusable', {
      duration: 8000,
    });
  });

  it('matches the marker even when only the message string carries it', () => {
    toastErrorWithReport(`Error: ${BACKEND_ERROR}`, undefined);
    expect(toastErrorMock).toHaveBeenCalledWith('t:tts_errors.ref_audio_unusable', {
      duration: 8000,
    });
  });

  it('shows localized trimming guidance for an overlong aligned reference', () => {
    const detail =
      '[clone_ref_too_long] Reference audio is 184.3 seconds long; supplied transcripts ' +
      'support at most 20 seconds.';
    toastErrorWithReport(detail, new Error(detail));
    expect(toastErrorMock).toHaveBeenCalledWith('t:tts_errors.ref_audio_too_long', {
      duration: 8000,
    });
  });

  it('shows localized guidance when bounded selection finds no speech', () => {
    const detail = '[clone_ref_no_speech] Automatic speech detection found no spoken words.';
    toastErrorWithReport(detail, new Error(detail));
    expect(toastErrorMock).toHaveBeenCalledWith('t:tts_errors.ref_audio_no_speech', {
      duration: 8000,
    });
  });

  it('unmarked errors keep the Report-action toast (JSX renderer, not a plain string)', () => {
    toastErrorWithReport('Error: something exploded', new Error('something exploded'));
    expect(toastErrorMock).toHaveBeenCalledTimes(1);
    expect(typeof toastErrorMock.mock.calls[0][0]).toBe('function');
  });

  it('tts_errors.ref_audio_unusable exists (non-empty) in every locale', () => {
    const localesDir = path.resolve(__dirname, '../i18n/locales');
    const files = fs.readdirSync(localesDir).filter((f) => f.endsWith('.json'));
    expect(files.length).toBeGreaterThanOrEqual(21);
    for (const f of files) {
      const locale = JSON.parse(fs.readFileSync(path.join(localesDir, f), 'utf8'));
      expect(locale.tts_errors?.ref_audio_unusable, `${f} missing the key`).toBeTruthy();
      expect(locale.tts_errors?.ref_audio_too_long, `${f} missing the key`).toBeTruthy();
      expect(locale.tts_errors?.ref_audio_no_speech, `${f} missing the key`).toBeTruthy();
    }
  });
});

// #1771: the voice-design instruct validator (omnivoice/models/omnivoice.py::
// _resolve_instruct, #664) rejects a dialect+accent mix and a same-category
// conflict with a fixed English message and no [marker]. The client-side
// guards in voiceInstruct.js already stop the picker from building these —
// this is the backstop for a conflict that reaches the engine by any other
// route (an imported project, a hand-edited/legacy profile).
describe('toastErrorWithReport voice-design instruct guidance (#1771)', () => {
  beforeEach(() => {
    toastErrorMock.mockClear();
  });

  it('shows actionable guidance for the dialect/accent 400, not the raw detail', () => {
    const detail =
      '400 Bad Request: Cannot mix Chinese dialect and English accent in a single instruct. ' +
      'Dialects are for Chinese speech, accents for English speech.';
    toastErrorWithReport(`Error: ${detail}`, new Error(detail));
    expect(toastErrorMock).toHaveBeenCalledWith('t:tts_errors.dialect_accent_conflict', {
      duration: 8000,
    });
  });

  it('shows actionable guidance for a same-category conflict 400', () => {
    const detail =
      "400 Bad Request: Conflicting instruct items within the same category: 'male' vs " +
      "'female'. Each category (gender, age, pitch, style, accent, dialect) allows at most " +
      'one item.';
    toastErrorWithReport(`Error: ${detail}`, new Error(detail));
    expect(toastErrorMock).toHaveBeenCalledWith('t:tts_errors.instruct_category_conflict', {
      duration: 8000,
    });
  });

  // Enumerated rather than globbed: a `>= 21` count over readdir() passes
  // even when a supported locale is missing, as long as some other .json
  // makes up the number. Naming the files means dropping one fails here.
  const SUPPORTED_LOCALES = [
    'ar',
    'de',
    'en',
    'es',
    'fr',
    'hi',
    'id',
    'it',
    'ja',
    'ko',
    'nl',
    'pl',
    'pt',
    'ru',
    'sv',
    'th',
    'tr',
    'uk',
    'vi',
    'zh-CN',
    'zh-TW',
  ];

  it('every new i18n key added for #1771 exists in all 21 locales', () => {
    const localesDir = path.resolve(__dirname, '../i18n/locales');
    expect(SUPPORTED_LOCALES).toHaveLength(21);
    for (const code of SUPPORTED_LOCALES) {
      const locale = JSON.parse(fs.readFileSync(path.join(localesDir, `${code}.json`), 'utf8'));
      expect(locale.tts_errors?.dialect_accent_conflict, `${code} missing key`).toBeTruthy();
      expect(locale.tts_errors?.instruct_category_conflict, `${code} missing key`).toBeTruthy();
      expect(locale.tts_errors?.ignored_conflict, `${code} missing key`).toBeTruthy();
      expect(locale.clone?.vd_exclusive_cleared, `${code} missing key`).toBeTruthy();
    }
  });
});

// #1276: a shutdown is not a fault and must not offer a bug report. Matched on
// the [shutting_down] MARKER, never on the bare 503 status — 503 is also how a
// real engine-load timeout and an unavailable engine are reported (#1246,
// #1260, #1277), and those are genuine bugs users need to be able to file.
describe('toastErrorWithReport shutdown handling (#1276)', () => {
  beforeEach(() => {
    toastErrorMock.mockClear();
  });

  const withStatus = (message, status) => {
    const e = new Error(message);
    e.name = 'ApiError';
    e.status = status;
    return e;
  };

  const SHUTDOWN =
    "[shutting_down] VoiceStudio is shutting down, so it didn't start loading " +
    'the model. Reopen the app and try again.';

  it('shows localized guidance for the shutdown marker, with no Report action', () => {
    const err = withStatus(SHUTDOWN, 503);
    toastErrorWithReport(err.message, err);

    expect(toastErrorMock).toHaveBeenCalledTimes(1);
    expect(toastErrorMock).toHaveBeenCalledWith('t:errors.backend_shutting_down', {
      duration: 8000,
    });
  });

  // The regression this exists to prevent: an earlier version of the fix keyed
  // off the 503 status alone, which would have removed the Report button from
  // the compute-timeout class — the exact bug #1277 was filed for.
  it('still offers Report for a 503 that is a real failure', () => {
    const err = withStatus(
      '503 Service Unavailable: TTS generate ran for more than 300s of actual ' +
        'compute time and was abandoned',
      503,
    );
    toastErrorWithReport(err.message, err);

    expect(toastErrorMock).toHaveBeenCalledTimes(1);
    // A render function, not a string — the reportable path.
    expect(typeof toastErrorMock.mock.calls[0][0]).toBe('function');
  });

  it('still offers Report for an engine-unavailable 503', () => {
    const err = withStatus("503 Service Unavailable: TTS engine 'xtts' is unavailable", 503);
    toastErrorWithReport(err.message, err);
    expect(typeof toastErrorMock.mock.calls[0][0]).toBe('function');
  });

  it('still offers Report for a genuine 500', () => {
    const err = withStatus('500 Internal Server Error: something actually broke', 500);
    toastErrorWithReport(err.message, err);
    expect(typeof toastErrorMock.mock.calls[0][0]).toBe('function');
  });

  it('still offers Report when there is no status at all', () => {
    toastErrorWithReport('Something broke', new Error('Something broke'));
    expect(typeof toastErrorMock.mock.calls[0][0]).toBe('function');
  });
});
