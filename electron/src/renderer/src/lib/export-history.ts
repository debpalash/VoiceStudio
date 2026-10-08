import { apiJson } from '@/lib/api/client';
import { saveBackendFile } from '@/lib/native-save';
import { queryClient } from '@/lib/query';

const AUDIO_EXTENSIONS = new Set(['aac', 'flac', 'm4a', 'm4b', 'mp3', 'ogg', 'opus', 'wav']);
const VIDEO_EXTENSIONS = new Set(['mkv', 'mov', 'mp4', 'webm']);

function exportMode(filename: string): 'audio' | 'video' | 'file' {
  const extension = filename.split('.').pop()?.toLocaleLowerCase() || '';
  if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
  if (VIDEO_EXTENSIONS.has(extension)) return 'video';
  return 'file';
}

/**
 * Save through Electron's trusted main process, then mirror the successful
 * native save into the backend's shared export history. History recording is
 * deliberately non-fatal: a bookkeeping outage must not turn a written file
 * into a failed export.
 */
export async function saveExport(url: string, suggestedName: string) {
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
