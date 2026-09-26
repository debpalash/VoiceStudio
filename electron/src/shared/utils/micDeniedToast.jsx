/**
 * micDeniedToast — the guided "microphone access denied" toast.
 *
 * Shown by the dictation/recording pre-flight when the OS reports the mic
 * grant as denied (utils/permissions.js) — in that state getUserMedia can
 * only throw an opaque NotAllowedError, so we skip it and walk the user to
 * the fix instead: the per-OS hint (capture.mic_hint_*) plus, inside Tauri,
 * an "Open Settings" button that deep-links the OS microphone-privacy pane.
 * On Linux (no such pane) the deep-link resolves false and the toast falls
 * back to the "use your system sound settings" hint.
 *
 * Same toast-with-action pattern as utils/errorToast.jsx.
 */
import toast from 'react-hot-toast';
import { detectPlatform, micHintKey } from './micError';
import { inTauri, openMicrophoneSettings } from './permissions';

export function showMicDeniedGuide(t, platform = detectPlatform()) {
  const message = t('capture.mic_denied_toast', { hint: t(micHintKey(platform)) });
  if (!inTauri()) {
    // Browser/dev: no OS pane to deep-link — plain reactive-style toast.
    toast.error(message, { duration: 8000 });
    return;
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
            if (!(await openMicrophoneSettings())) {
              toast(t('capture.mic_hint_linux'), { icon: 'ℹ️', duration: 8000 });
            }
          }}
        >
          {t('permissions.open_settings')}
        </button>
      </div>
    ),
    { duration: 10000 },
  );
}
