import { apiFetch, apiJson } from '@/lib/api/client';
import { convertSpeech } from '@/lib/api/convert';
import { generateClone } from '@/lib/api/generate';
import { DEFAULT_CLONE_SETTINGS } from '@/lib/store/clone-settings';
import { LANG_CODES } from '@shared/utils/languages';
import { loadWorkflowMedia } from './workflow-run-store';
import type { WorkflowOperations } from './workflow-runtime';

function languageCode(language?: string) {
  return LANG_CODES.find((entry) => entry.label === language)?.code || language;
}

export const workflowOperations: WorkflowOperations = {
  loadAudio: loadWorkflowMedia,
  speak: async (text, step, signal) => {
    const result = await generateClone({
      ...DEFAULT_CLONE_SETTINGS, text, profileId: step.voiceId,
      language: step.language || 'Auto', speed: step.speed ?? 1,
    }, { signal });
    if (result.dropped) throw new Error('workflowRun.incomplete');
    return result.blob;
  },
  normalize: async (audio, step, signal) => {
    const body = new FormData();
    body.set('audio', audio, 'workflow.wav');
    body.set('target_dbfs', String(step.targetDb ?? -2));
    return (await apiFetch('/tools/normalize-speech', { method: 'POST', body, signal })).blob();
  },
  transcribe: async (audio, _step, signal) => {
    const readiness = await apiJson<{ ready: boolean }>('/dictation/readiness?purpose=transcribe', { signal });
    if (!readiness.ready) throw new Error('workflowRun.asr_required');
    const body = new FormData();
    body.set('audio', audio, audio instanceof File ? audio.name : 'workflow.wav');
    body.set('mode', 'reference');
    body.set('refine', 'false');
    const result = await apiJson<{ text: string }>('/transcribe', { method: 'POST', body, signal });
    return result.text;
  },
  convert: async (audio, step, signal) => {
    const result = await convertSpeech(
      new File([audio], audio instanceof File ? audio.name : 'workflow.wav', { type: audio.type }),
      step.voiceId!, true, signal,
    );
    if (!result.audio_url.startsWith('/audio/')) throw new Error('workflowRun.incomplete');
    return (await apiFetch(result.audio_url, { signal })).blob();
  },
  translate: async (text, step, signal) => {
    const provider = step.provider === 'nllb' ? 'nllb' : 'argos';
    const result = await apiJson<{ translated: { id: string; text: string; error?: string }[] }>('/dub/translate', {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        segments: [{ id: 'workflow', text }], source_lang: languageCode(step.sourceLanguage),
        target_lang: languageCode(step.language), provider, quality: 'fast',
        auto_glossary: false, reflect: false, condense: false,
      }),
    });
    const translated = result.translated?.find((segment) => segment.id === 'workflow');
    if (!translated || translated.error) throw new Error(translated?.error || 'workflowRun.incomplete');
    return translated.text;
  },
};
