import { apiJson } from '@/lib/api/client';
import type {
  DownloadParams,
  GalleryVoice,
  YoutubeSearchResult,
} from '@shared/api/gallery-types';
import type { PersonaImportResult } from '@shared/api/types';
export type { GalleryVoice, YoutubeSearchResult };
export const importsApi = {
  list: (signal?: AbortSignal) => apiJson<GalleryVoice[]>('/gallery/voices', { signal }),
  upload: (file: File, metadata?: Pick<GalleryVoice, 'character' | 'description'>) => {
    const body = new FormData();
    body.append('audio', file);
    body.append('category', 'import');
    if (metadata) {
      body.append('character', metadata.character || '');
      body.append('description', metadata.description || '');
    }
    body.append('name', file.name.replace(/\.[^.]+$/, ''));
    return apiJson('/gallery/upload', { method: 'POST', body });
  },
  persona: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return apiJson<PersonaImportResult>('/personas/import', { method: 'POST', body });
  },
  search: (query: string) =>
    apiJson<{ results: YoutubeSearchResult[] }>(
      '/gallery/search/youtube?' +
        new URLSearchParams({ query, category: 'import', max_results: '10' }),
      { method: 'POST' },
    ),
  download: (options: DownloadParams) =>
    apiJson(
      '/gallery/download?' +
        new URLSearchParams({
          video_url: options.video_url,
          start_time: String(options.start_time),
          duration: String(options.duration),
          character_name: options.character_name,
          category: options.category,
          description: options.description || '',
        }),
      { method: 'POST' },
    ),
  save: (voice: GalleryVoice) =>
    apiJson<{ profile_id: string }>(
      '/gallery/voices/' +
        encodeURIComponent(voice.id) +
        '/save-as-profile?' +
        new URLSearchParams({ profile_name: voice.name }),
      { method: 'POST' },
    ),
  remove: (id: string) =>
    apiJson('/gallery/voices/' + encodeURIComponent(id), { method: 'DELETE' }),
};
