import { getBridge } from '@/components/bridge';
import { API_BASE, apiFetch, apiJson } from '@/lib/api/client';
import { saveLocalFile } from '@/lib/local-export';
import { saveBackendFile } from '@/lib/native-save';
import { queryClient } from '@/lib/query';
import type { SaveAudioResult } from '../../../preload/index.d';

const AUDIO_EXTENSIONS = new Set(['aac', 'flac', 'm4a', 'm4b', 'mp3', 'ogg', 'opus', 'wav']);
const VIDEO_EXTENSIONS = new Set(['mkv', 'mov', 'mp4', 'webm']);

function exportMode(filename: string): 'audio' | 'video' | 'file' {
  const extension = filename.split('.').pop()?.toLocaleLowerCase() || '';
  if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
  if (VIDEO_EXTENSIONS.has(extension)) return 'video';
  return 'file';
}

/**
 * The browser build (Docker/web) has no native bridge. Fetch through `apiFetch`
 * so the share PIN, admin bearer session and cross-origin API bases all apply,
 * then save the blob: a plain link would drop those headers and, cross-origin,
 * open inline media instead of downloading it (#4222).
 */
async function browserDownload(url: string, suggestedName: string): Promise<SaveAudioResult> {
  const path = API_BASE && url.startsWith(API_BASE) ? url.slice(API_BASE.length) : url;
  const response = await apiFetch(path);
  return saveLocalFile(await response.blob(), suggestedName);
}

/**
 * Save through Electron's trusted main process, then mirror the successful
 * native save into the backend's shared export history. History recording is
 * deliberately non-fatal: a bookkeeping outage must not turn a written file
 * into a failed export. Without the bridge, the browser downloads the file.
 */
export async function saveExport(url: string, suggestedName: string) {
  if (!getBridge()) return browserDownload(url, suggestedName);
  const saved = await saveBackendFile({ url, suggestedName });
  if (!saved) return null;
  if (!saved.canceled && saved.path) {
    try {
      await apiJson('/export/record', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: saved.path.split(/[\\/]/).pop() || suggestedName,
          destination_path: saved.path,
          mode: exportMode(saved.path),
        }),
      });
      await queryClient.invalidateQueries({ queryKey: ['export-history'] });
    } catch (error) {
      console.warn('Could not record the completed export', error);
    }
  }
  return saved;
}
