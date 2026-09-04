/**
 * errorToast — error toast with a "Report" action.
 *
 * Drop-in upgrade for `toast.error(message)` at call sites that have the
 * failure in hand: same toast, plus a button that opens the prefilled
 * GitHub Issues form (utils/bugReport.js) with the scrubbed error attached.
 * Nothing is sent anywhere until the user clicks Submit on github.com.
 */
import toast from 'react-hot-toast';
import i18next from 'i18next';
import { openBugReport } from './bugReport';

// #1188: backend errors that carry a machine-readable "[code]" marker are
// user-fixable input problems, not bugs — show localized guidance (what
// happened + the concrete fix) instead of the raw English detail, and skip
// the "Report" action.
//
// #1276 adds [shutting_down]: the backend is on its way out, so nothing
// failed and there is nothing to report.
//
// Matched on the MARKER, never on the bare 503 status. 503 is also how a real
// engine-load timeout and an unavailable engine are reported (#1246, #1260,
// #1277) — those are genuine bugs users need to file, and keying off the
// status alone would silence exactly that class.
//
// Markers are emitted by the backend ([clone_ref_unusable] in
// omnivoice/utils/audio.py, [shutting_down] in main.py) — keep in sync.
const USER_FIXABLE_MARKERS = {
  '[clone_ref_unusable]': 'tts_errors.ref_audio_unusable',
  '[clone_ref_too_long]': 'tts_errors.ref_audio_too_long',
  '[clone_ref_no_speech]': 'tts_errors.ref_audio_no_speech',
  '[shutting_down]': 'errors.backend_shutting_down',
};

// #1771: the voice-design instruct validator (omnivoice/models/omnivoice.py::
// _resolve_instruct, #664) rejects a couple of user-fixable combinations with
// a fixed English message and no [marker]. voiceInstruct.js's client-side
// guards already stop the picker from building these — this is the backstop
// for anything that still reaches the engine (an imported project, a
// hand-edited/legacy profile, a free-text instruct) so the user sees "here's
// what to fix" instead of the raw 400. Matched on the engine's exact message
// text — keep in sync with omnivoice/models/omnivoice.py::_resolve_instruct.
const INSTRUCT_VALIDATION_MESSAGES = [
  {
    match: /cannot mix chinese dialect and english accent/i,
    i18nKey: 'tts_errors.dialect_accent_conflict',
  },
  {
    match: /conflicting instruct items within the same category/i,
    i18nKey: 'tts_errors.instruct_category_conflict',
  },
];

export function toastErrorWithReport(message, error) {
  const err = error instanceof Error ? error : new Error(String(error ?? message));
  const raw = `${err.message ?? ''} ${message ?? ''}`;
  for (const [marker, i18nKey] of Object.entries(USER_FIXABLE_MARKERS)) {
    if (raw.includes(marker)) {
      toast.error(i18next.t(i18nKey), { duration: 8000 });
      return;
    }
  }
  for (const { match, i18nKey } of INSTRUCT_VALIDATION_MESSAGES) {
    if (match.test(raw)) {
      toast.error(i18next.t(i18nKey), { duration: 8000 });
      return;
    }
  }
  toast.error(
    (tst) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ flex: 1 }}>{message}</span>
        <button
          type="button"
          className="btn-secondary"
          style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
          onClick={async () => {
            toast.dismiss(tst.id);
            try {
              await openBugReport({ error: err });
            } catch (e) {
              // openExternal already falls back to window.open; if even
              // that failed there's nothing actionable left to surface.
              console.warn('[errorToast] report action failed', e);
            }
          }}
        >
          {i18next.t('errors.report')}
        </button>
      </div>
    ),
    { duration: 8000 },
  );
}
