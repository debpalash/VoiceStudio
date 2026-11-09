import { apiJson } from '@/lib/api/client';
import { dubSourceLanguage } from './source-language';

export type ArgosPackStatus = {
  source_lang: string;
  pairs: Array<{
    source_lang: string;
    target_lang: string;
    installed: boolean;
  }>;
};

export type ArgosPackRequest = {
  source_lang?: string;
  target_langs: string[];
  job_id?: string;
};

export type ArgosPackSource = {
  sourceLang?: string;
  sourceLanguage?: string;
  jobId?: string | null;
};

export function buildArgosPackRequest(
  source: ArgosPackSource,
  targetLangs: string[],
): ArgosPackRequest {
  return {
    source_lang: dubSourceLanguage(source.sourceLanguage, source.sourceLang),
    target_langs: targetLangs,
    job_id: source.jobId || undefined,
  };
}

export function argosPackStatus(request: ArgosPackRequest, signal?: AbortSignal) {
  return apiJson<ArgosPackStatus>('/engines/translation/argos/packs/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  });
}

export function argosPackInstall(request: ArgosPackRequest) {
  return apiJson<ArgosPackStatus>('/engines/translation/argos/packs/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
}
