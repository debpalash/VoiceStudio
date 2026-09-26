/**
 * "An update is available" — as a toast with actions, not a wall of text.
 *
 * The release notes for a version are the whole changelog section, which for
 * v0.4.1 was 42 bullets. Anything that renders them inline becomes unusable:
 * older builds put them in a blocking OS dialog that filled the screen and had
 * to be dismissed before the app could be touched.
 *
 * The opposite failure is just as real — this app's only remaining signal was a
 * 6-pixel dot beside the version number in the footer, which nobody notices.
 *
 * So: a toast that states the version, offers the two actions that matter, and
 * leaves. The notes stay one click away in Settings → Updates, where there is
 * room for them.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import { useAppStore } from '../store';
import { installUpdate } from '../utils/updater';
import { isAppBusy } from '../utils/appBusy';

/** Toast id — one per version, so a re-check can't stack duplicates. */
const toastId = (version) => `update-available-${version}`;

function UpdateToastBody({ id, version }) {
  const { t } = useTranslation();

  const openNotes = () => {
    useAppStore.getState().openSettingsTab?.('updates');
    toast.dismiss(id);
  };

  const install = () => {
    // Installing relaunches the process, so anything in flight is lost — not
    // just a dub synth, but an upload, a transcription, a translation, an
    // export or a standalone TTS run. `isAppBusy` is the shared answer;
    // UpdatesPanel asks it too, because that path is reachable without this
    // toast and the two checks must not drift.
    if (isAppBusy(useAppStore.getState())) {
      toast(t('update.busy'), { icon: '⏳' });
      return;
    }
    toast.dismiss(id);
    installUpdate(useAppStore.getState());
  };

  return (
    <div className="flex flex-col gap-2" data-testid="update-toast">
      <div className="leading-snug">
        {t('update.toast_available', {
          version,
          defaultValue: 'VoiceStudio {{version}} is available',
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={install}
          className="rounded px-2 py-[3px] text-[0.72rem] font-semibold [background:var(--chrome-accent)] text-black hover:opacity-90"
        >
          {t('update.toast_install', { defaultValue: 'Install and restart' })}
        </button>
        <button
          type="button"
          onClick={openNotes}
          className="rounded px-2 py-[3px] text-[0.72rem] underline opacity-80 hover:opacity-100"
        >
          {t('update.toast_whats_new', { defaultValue: "What's new" })}
        </button>
        <button
          type="button"
          onClick={() => toast.dismiss(id)}
          className="ml-auto rounded px-2 py-[3px] text-[0.72rem] opacity-60 hover:opacity-100"
        >
          {t('update.toast_later', { defaultValue: 'Later' })}
        </button>
      </div>
    </div>
  );
}

/**
 * Announce *version* once. Idempotent per version: react-hot-toast replaces a
 * toast with the same id rather than stacking, so the 6-hourly re-check can't
 * pile up copies of the same announcement.
 *
 * Deliberately never auto-dismisses — an update the user hasn't answered is
 * still true. It does not block anything, and "Later" removes it.
 */
export function showUpdateToast(version) {
  if (!version) return;
  const id = toastId(version);
  toast.custom((tst) => <UpdateToastBody id={tst.id} version={version} />, {
    id,
    duration: Infinity,
    position: 'bottom-right',
  });
}

export default UpdateToastBody;
