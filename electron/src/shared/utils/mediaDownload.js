// Shared "save this rendered file to disk" util — the ONE place that decides
// how a finished media file leaves the app, on every platform.
//
// Why this exists (#1218): a raw `<a href={httpMediaUrl} download>` does NOT
// download in the Tauri desktop WebView. WebKit (macOS/WKWebView) "handles the
// load" for any media URL its engine can play by NAVIGATING the whole webview
// to the file and playing it fullscreen — replacing the app. The blank-window
// guard then reloads and eventually paints its failure page. So server media
// must never be reached via `<a download>` inside the shell.
//
// The parity-safe route, ported from App.jsx's `triggerDownload`:
//   * Tauri  → native save dialog (`plugin-dialog.save`) → server-side copy so
//              the bytes actually land on disk at the user's chosen path. WebKit
//              silently drops blob downloads too, so a blob is not sufficient.
//   * Browser/Docker → `browserDownload` (fetch → blob → temporary <a download>),
//              which is correct outside the shell.
//
// Two Tauri copy mechanisms, picked by the caller's knowledge of the source:
//   (a) `sourceFilename` set → the file already lives in OUTPUTS_DIR (audiobook
//       / story renders, served at /audio/<file>): copy it via the /export API
//       (`exportAction`), which resolves the name inside OUTPUTS_DIR and copies
//       to the destination. The /audio mount is a StaticFiles mount with no
//       ?save_path= support, so this is the only server-side copy that works
//       for those files.
//   (b) no `sourceFilename` → a dynamic dub endpoint: Tauri authorizes the
//       chosen path once and only the capability token crosses loopback HTTP.
//   Subtitles (srt/vtt) are small text bodies fetched raw and written via the
//   trusted `save_text_file` command — the backend never handles their dest
//   path (#309).
import { toast } from 'react-hot-toast';
import i18n from '../i18n';
import { isTauri } from './media';
import { apiFetch } from '../api/client';
import { browserDownload } from './download';
import { exportAction, exportRecord } from '../api/exports';

const VIDEO_EXTS = ['mp4', 'mov', 'mkv', 'webm'];
const AUDIO_EXTS = ['wav', 'mp3', 'flac', 'm4b', 'm4a', 'aac', 'ogg', 'opus'];

function guessMode(ext) {
  if (VIDEO_EXTS.includes(ext)) return 'video';
  if (AUDIO_EXTS.includes(ext)) return 'audio';
  return 'file';
}

/**
 * Save a server-rendered media file to disk without ever navigating the
 * webview. Works in the Tauri shell (native dialog + server-side copy) and in
 * the browser/Docker build (blob download). Shows the same user-facing toasts
 * as the App's own export flow. Never creates an `<a href={httpUrl} download>`.
 *
 * @param {string} url            HTTP URL of the file (also the source for the
 *                                browser blob download + authorized dynamic copy).
 * @param {string} fallbackName   Suggested filename in the save dialog / for the
 *                                download.
 * @param {object} [opts]
 * @param {string} [opts.sourceFilename] Basename of a file in OUTPUTS_DIR — when
 *                                set, the Tauri copy goes through `exportAction`
 *                                (/export) instead of a ?save_path= append.
 * @param {() => void} [opts.onValueMoment]   Fires once on a successful save
 *                                (App wires `recordValueMoment('export')`).
 * @param {() => void} [opts.onHistoryChanged] Fires after the export-history
 *                                row is written (App wires `loadExportHistory`).
 */
export async function downloadMedia(url, fallbackName, opts = {}) {
  const { sourceFilename = null, onValueMoment = null, onHistoryChanged = null } = opts;
  const extGuess = (
    fallbackName.includes('.') ? fallbackName.split('.').pop() : 'bin'
  ).toLowerCase();
  const modeGuess = guessMode(extGuess);

  // exportRecord writes the history row for paths the backend didn't already
  // record (browser download, authorized dub save, subtitle). Non-fatal: a failed record
  // must not turn a successful save into an error toast.
  const recordHistory = async (filename, destinationPath) => {
    try {
      await exportRecord({ filename, destination_path: destinationPath, mode: modeGuess });
      onHistoryChanged?.();
    } catch (err) {
      console.warn('exportRecord failed:', err);
    }
  };

  // ── Tauri: native save dialog + server-side copy ────────────────────────
  if (isTauri) {
    try {
      // Dynamic dub saves are selected inside the native command. A webview
      // path is never treated as filesystem authority.
      if (!sourceFilename && !['srt', 'vtt'].includes(extGuess)) {
        const { invoke } = await import('@tauri-apps/api/core');
        const selection = await invoke('authorize_host_path', {
          kind: 'dub_export',
          suggestedName: fallbackName,
        });
        if (!selection) return;
        toast.loading(i18n.t('app.toast_saving', { name: fallbackName }), { id: fallbackName });
        const res = await apiFetch(url, {
          headers: { 'X-VoiceStudio-Path-Authorization': selection.authorization },
          retryTransport: false,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const ctype = res.headers.get('content-type') || '';
        if (!ctype.includes('application/json')) {
          throw new Error(
            `Server returned ${ctype || 'an unknown content type'} instead of a JSON save confirmation`,
          );
        }
        const data = await res.json();
        toast.success(i18n.t('app.toast_saved', { path: data.path }), { id: fallbackName });
        onValueMoment?.();
        await recordHistory(data.display_name || fallbackName, data.path);
        return;
      }
      const { invoke } = await import('@tauri-apps/api/core');
      const selection = await invoke('authorize_host_path', {
        kind: 'dub_export',
        suggestedName: fallbackName,
      });
      if (!selection) return; // user cancelled
      toast.loading(i18n.t('app.toast_saving', { name: fallbackName }), { id: fallbackName });

      // (a) File already in OUTPUTS_DIR — copy by source filename via /export.
      // /export records its own history row, so no exportRecord here.
      if (sourceFilename) {
        await exportAction({
          source_filename: sourceFilename,
          authorization: selection.authorization,
          mode: modeGuess,
        });
        toast.success(i18n.t('app.toast_saved', { path: selection.path }), { id: fallbackName });
        onValueMoment?.();
        onHistoryChanged?.();
        return;
      }

      // (b) Subtitles: fetch the small text body and write it from this trusted
      // process — the backend never handles the destination path (#309).
      if (['srt', 'vtt'].includes(extGuess)) {
        const res = await apiFetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`); // don't write an error body to disk
        const text = await res.text();
        await invoke('save_text_file', { path: selection.path, contents: text });
        toast.success(i18n.t('app.toast_saved', { path: selection.path }), { id: fallbackName });
        onValueMoment?.();
        await recordHistory(fallbackName, selection.path);
        return;
      }
    } catch (err) {
      console.error(err);
      toast.error(i18n.t('app.toast_save_error', { message: err.message }), { id: fallbackName });
    }
    return;
  }

  // ── Browser / Docker: standard blob download ────────────────────────────
  try {
    toast.loading(i18n.t('app.toast_processing', { name: fallbackName }), { id: fallbackName });
    const finalName = await browserDownload(url, fallbackName);
    toast.success(i18n.t('app.toast_downloaded', { name: finalName }), { id: fallbackName });
    onValueMoment?.();
    await recordHistory(finalName, `~/Downloads/${finalName}`);
  } catch (err) {
    console.error(err);
    toast.error(i18n.t('app.toast_download_error', { message: err.message }), { id: fallbackName });
  }
}
