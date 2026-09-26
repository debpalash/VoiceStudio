/**
 * dictationNotice — carries dictation failures from the hidden widget window
 * to a window the user can actually see.
 *
 * The dictation widget used to be a floating pill, and that pill was where
 * every failure surfaced: Accessibility not granted, the mic denied by the OS,
 * a transcription error. The widget window is now a permanently hidden host
 * for the recorder (owner decision, 2026-08-07) — nothing it renders is ever
 * on screen — so those messages had nowhere left to go. Without this bridge,
 * pressing the hotkey with Accessibility ungranted would do nothing at all and
 * say nothing about why.
 *
 * The widget emits; the main window listens and toasts. The label is
 * localized by the sender (both windows share one i18n instance and language,
 * and the sender is where the error's context lives), so this module only
 * routes and decorates.
 *
 * Same toast-with-action shape as utils/micDeniedToast.jsx.
 */
import toast from 'react-hot-toast';
import i18next from 'i18next';
import { inTauri, openAccessibilitySettings, openMicrophoneSettings } from './permissions';
import { toastAsrModelMissing } from './asrModelMissing';

export const DICTATION_NOTICE_EVENT = 'dictation-notice';

/** Fire-and-forget: a failed notice must never break the recording path. */
export async function emitDictationNotice(payload) {
  if (!inTauri()) return;
  try {
    const { emit } = await import('@tauri-apps/api/event');
    await emit(DICTATION_NOTICE_EVENT, payload);
  } catch (err) {
    console.warn('dictation notice emit failed:', err);
  }
}

/** Subscribe in the main window. Resolves to an unlisten fn (no-op outside Tauri). */
export async function listenDictationNotice(handler) {
  if (!inTauri()) return () => {};
  try {
    const { listen } = await import('@tauri-apps/api/event');
    return await listen(DICTATION_NOTICE_EVENT, (event) => handler(event.payload));
  } catch (err) {
    console.warn('dictation notice listen failed:', err);
    return () => {};
  }
}

/**
 * Render one notice. Kinds that name a fixable OS grant get the button that
 * fixes it — the rest are informational, because there is nothing to click.
 */
export function showDictationNotice(notice) {
  // No speech model installed: the same one-click download toast every other
  // ASR surface shows, not a bare label. The widget cannot show it itself —
  // its window has no <Toaster> in the desktop app.
  if (notice?.kind === 'asr_missing') {
    toastAsrModelMissing(notice.missing || {});
    return;
  }
  const label = notice?.label;
  if (!label) return; // nothing worth interrupting the user for
  const opener =
    notice.kind === 'a11y' || notice.kind === 'setup'
      ? openAccessibilitySettings
      : // A mic error only earns the settings button when the OS actually
        // denied it. "Busy" and "no device" arrive under the same kind, and
        // for those the permissions pane shows nothing wrong — which reads as
        // the app blaming the user for a permission they already granted.
        notice.kind === 'mic' && notice.deniedByOs
        ? openMicrophoneSettings
        : null;

  if (!opener || !inTauri()) {
    toast.error(label, { duration: 8000 });
    return;
  }

  toast.error(
    (tst) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ flex: 1 }}>{label}</span>
        <button
          type="button"
          className="btn-secondary"
          style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
          onClick={() => {
            toast.dismiss(tst.id);
            opener();
          }}
        >
          {i18next.t('permissions.open_settings')}
        </button>
      </div>
    ),
    { duration: 10000 },
  );
}
