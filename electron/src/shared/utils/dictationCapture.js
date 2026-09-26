import { invoke } from '@tauri-apps/api/core';

export const BROWSER_DICTATION_REQUEST = 'voicestudio:dictation-request';

export async function requestDictationCapture(action = 'start') {
  if (typeof window === 'undefined') return;
  if ('__TAURI_INTERNALS__' in window) {
    await invoke('request_dictation_capture', { action });
    return;
  }
  window.dispatchEvent(
    new CustomEvent(BROWSER_DICTATION_REQUEST, {
      detail: { action },
    }),
  );
}
