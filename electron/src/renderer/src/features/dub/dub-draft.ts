import { restoreExportPreferences } from './dub-export';
import type { DubSession } from './dub-session';
export const DUB_DRAFT_KEY = 'voicestudio.dub.session.v1';

function isProviderErrorText(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    (/error\s+5\d\d\s*\(server error\)/i.test(value) ||
      /that's an error\.?.*please try again later/i.test(value) ||
      /^\s*<!doctype\s+html/i.test(value) ||
      /^\s*<html[\s>]/i.test(value))
  );
}

/** A saved phase describes interrupted work, never proof that a job is still running. */
export function restoreDubDraft(raw: string | null, defaults: DubSession): DubSession {
  try {
    const value = JSON.parse(raw || 'null');
    if (!value || typeof value !== 'object' || !Array.isArray(value.segments)) return defaults;
    const safeId = (id: unknown) =>
      typeof id === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(id) ? id : null;
    const segments = value.segments
      .filter((segment: unknown) => {
        if (!segment || typeof segment !== 'object') return false;
        const s = segment as Record<string, unknown>;
        return (
          typeof s.id === 'string' &&
          typeof s.text === 'string' &&
          Number.isFinite(s.start) &&
          Number.isFinite(s.end) &&
          Number(s.start) >= 0 &&
          Number(s.end) > Number(s.start)
        );
      })
      .map((segment: DubSession['segments'][number]) => {
        const textOriginal =
          typeof segment.text_original === 'string' ? segment.text_original : segment.text;
        const translations = Object.fromEntries(
          Object.entries(segment.translations || {}).filter(
            ([, text]) => !isProviderErrorText(text),
          ),
        );
        const rejectedProviderOutput =
          isProviderErrorText(segment.text) ||
          Object.keys(translations).length !== Object.keys(segment.translations || {}).length;
        const agentGeneratedLangs = [
          ...(Array.isArray(segment.agent_generated_langs) ? segment.agent_generated_langs : []),
          ...(typeof segment.agent_generated_lang === 'string'
            ? [segment.agent_generated_lang]
            : []),
        ].filter(
          (language, index, all) =>
            typeof language === 'string' &&
            /^[a-zA-Z]{2,3}(-[a-zA-Z0-9]+)?$/.test(language) &&
            typeof translations[language] === 'string' &&
            all.indexOf(language) === index,
        );
        const activeAgentLanguage =
          typeof segment.agent_generated_lang === 'string' &&
          agentGeneratedLangs.includes(segment.agent_generated_lang)
            ? segment.agent_generated_lang
            : undefined;
        return {
          ...segment,
          text: isProviderErrorText(segment.text) ? textOriginal : segment.text,
          text_original: textOriginal,
          translations: Object.keys(translations).length ? translations : undefined,
          agent_generated_lang: activeAgentLanguage,
          agent_generated_langs: agentGeneratedLangs.length ? agentGeneratedLangs : undefined,
          translate_error: rejectedProviderOutput
            ? 'translation provider returned invalid output'
            : segment.translate_error,
        };
      });
    const taskId = safeId(value.taskId);
    const jobId = safeId(value.jobId);
    const phase = value.phase === 'done' ? 'done' : segments.length ? 'editing' : 'idle';
    const prior = value.recovery || value.phase;
    const recovery =
      jobId && prior === 'transcribing'
        ? 'transcribing'
        : jobId && taskId && (prior === 'preparing' || prior === 'generating')
          ? prior
          : null;
    return {
      ...defaults,
      project:
        value.project &&
        typeof value.project.id === 'string' &&
        typeof value.project.name === 'string'
          ? value.project
          : undefined,
      jobId,
      taskId,
      phase,
      recovery,
      segments,
      filename: typeof value.filename === 'string' ? value.filename : '',
      duration:
        Number.isFinite(value.duration) && Number(value.duration) > 0
          ? Number(value.duration)
          : Math.max(0, ...segments.map((segment: DubSession['segments'][number]) => segment.end)),
      sourceLang: typeof value.sourceLang === 'string' ? value.sourceLang : '',
      inputType: value.inputType === 'audio' ? 'audio' : 'video',
      tracks: Array.isArray(value.tracks)
        ? value.tracks.filter((track: unknown) => typeof track === 'string')
        : [],
      timingStrategy: ['concise', 'smart_fit', 'stretch_video', 'strict_slot'].includes(
        value.timingStrategy,
      )
        ? value.timingStrategy
        : 'strict_slot',
      voiceMatch: value.voiceMatch === 'consistent' ? 'consistent' : 'per_line',
      generatedTiming: ['concise', 'smart_fit', 'stretch_video', 'strict_slot'].includes(
        value.generatedTiming,
      )
        ? value.generatedTiming
        : undefined,
      pendingTiming: ['concise', 'smart_fit', 'stretch_video', 'strict_slot'].includes(
        value.pendingTiming,
      )
        ? value.pendingTiming
        : undefined,
      fingerprintsByLang:
        value.fingerprintsByLang &&
        typeof value.fingerprintsByLang === 'object' &&
        !Array.isArray(value.fingerprintsByLang)
          ? Object.fromEntries(
              Object.entries(value.fingerprintsByLang).flatMap(([language, hashes]) => {
                if (!hashes || typeof hashes !== 'object' || Array.isArray(hashes)) return [];
                const entries = Object.entries(hashes).filter(
                  (entry): entry is [string, string] => typeof entry[1] === 'string',
                );
                return entries.length ? [[language, Object.fromEntries(entries)]] : [];
              }),
            )
          : {},
      sourceLanguage:
        typeof value.sourceLanguage === 'string' &&
        /^[a-zA-Z]{2,3}(-[a-zA-Z0-9]+)?$/.test(value.sourceLanguage)
          ? value.sourceLanguage
          : undefined,
      numSpeakers:
        Number.isInteger(value.numSpeakers) && value.numSpeakers >= 1 && value.numSpeakers <= 20
          ? value.numSpeakers
          : undefined,
      steps:
        Number.isInteger(value.steps) && value.steps >= 8 && value.steps <= 64
          ? value.steps
          : undefined,
      guidance:
        Number.isFinite(value.guidance) && value.guidance >= 0 && value.guidance <= 4
          ? value.guidance
          : undefined,
      speed:
        Number.isFinite(value.speed) && value.speed >= 0.5 && value.speed <= 2
          ? value.speed
          : undefined,
      instruct: typeof value.instruct === 'string' ? value.instruct : undefined,
      fitOptions:
        value.fitOptions && typeof value.fitOptions === 'object' && !Array.isArray(value.fitOptions)
          ? Object.fromEntries(
              Object.entries(value.fitOptions).filter(([key, item]) =>
                key === 'allow_video_retime'
                  ? typeof item === 'boolean'
                  : [
                      'max_audio_only_rate',
                      'audio_rate_cap',
                      'video_slow_cap',
                      'gap_guard_s',
                    ].includes(key) &&
                    typeof item === 'number' &&
                    Number.isFinite(item) &&
                    item >= (key === 'gap_guard_s' ? 0 : 1),
              ),
            )
          : undefined,
      exportOptions: restoreExportPreferences(value.exportOptions),
      translationFallback: value.translationFallback === true,
      quality: ['fast', 'autofit', 'cinematic', 'agent'].includes(value.quality)
        ? value.quality
        : defaults.quality,
      agentCli: ['codex', 'claude', 'opencode', 'pi'].includes(value.agentCli)
        ? value.agentCli
        : undefined,
      autoGlossary:
        typeof value.autoGlossary === 'boolean' ? value.autoGlossary : defaults.autoGlossary,
      reflectPass:
        typeof value.reflectPass === 'boolean' ? value.reflectPass : defaults.reflectPass,
      condenseSuggest:
        typeof value.condenseSuggest === 'boolean'
          ? value.condenseSuggest
          : defaults.condenseSuggest,
      translationInstructions: typeof value.translationInstructions === 'string'
        ? value.translationInstructions.slice(0, 5000) : undefined,
      dialect:
        typeof value.dialect === 'string' && /^[a-zA-Z]{2,3}-[a-zA-Z]{2,4}$/.test(value.dialect)
          ? value.dialect
          : undefined,
      target: typeof value.target === 'string' ? value.target : defaults.target,
      multiTargets: Array.isArray(value.multiTargets)
        ? value.multiTargets.filter((item: unknown): item is { lang: string; code: string } =>
            Boolean(
              item &&
              typeof item === 'object' &&
              typeof (item as Record<string, unknown>).lang === 'string' &&
              typeof (item as Record<string, unknown>).code === 'string' &&
              /^[a-zA-Z]{2,3}(-[a-zA-Z0-9]+)?$/.test(
                (item as Record<string, unknown>).code as string,
              ),
            ),
          )
        : defaults.multiTargets,
    };
  } catch {
    return defaults;
  }
}
