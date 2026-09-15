import { LANG_CODES } from '../../../../../../frontend/src/utils/languages';
import { restoreDubDraft } from '../dub/dub-draft';
import type { DubSession } from '../dub/dub-session';
export interface DubProject {
  id: string;
  name: string;
  video_path?: string | null;
  audio_path?: string | null;
  duration?: number | null;
  updated_at?: number;
  state: Record<string, unknown>;
}
export function projectSession(project: DubProject, defaults: DubSession): DubSession {
  const s = project.state || {};
  const segments = Array.isArray(s.dubSegments)
    ? s.dubSegments
        .filter((raw) => raw && typeof raw === 'object')
        .map((raw, index) => ({
          ...raw,
          id: String(raw.id ?? index),
        }))
    : [];
  const restored = restoreDubDraft(
    JSON.stringify({
      jobId: s.dubJobId,
      filename: s.dubFilename || project.video_path,
      duration: project.duration,
      segments,
      target: s.dubLang,
      multiTargets: s.multiLangs,
      quality: s.translateQuality,
      autoGlossary: s.autoGlossary,
      reflectPass: s.reflectPass,
      condenseSuggest: s.condenseSuggest,
      dialect: s.dubDialect,
      translationInstructions: s.translationInstructions,
      exportOptions: {
        ...(typeof s.exportOptions === 'object' && s.exportOptions ? s.exportOptions : {}),
        preserveBg: s.preserveBg,
        track: s.defaultTrack,
        burn: s.burnSubs,
        dual: s.dualSubs,
        karaoke: s.karaokeSubs,
        excluded:
          s.exportTracks && typeof s.exportTracks === 'object'
            ? Object.entries(s.exportTracks)
                .filter(([, on]) => on === false)
                .map(([code]) => code)
            : undefined,
      },
      fitOptions: s.fitOptions,
      steps: s.steps,
      guidance: s.cfg,
      speed: s.speed,
      instruct: s.dubInstruct,
      sourceLanguage: s.sourceLanguage,
      numSpeakers: s.dubNumSpeakers,
      timingStrategy: s.timingStrategy,
      voiceMatch: s.voiceMatch,
      generatedTiming: s.generatedTiming,
      fingerprintsByLang: s.segHashesByLang,
      sourceLang: s.dubSourceLangCode,
      tracks: s.dubTracks,
      phase: s.dubStep,
      inputType: s.inputType,
    }),
    defaults,
  );
  return { ...restored, project, taskId: null, recovery: null };
}
export function projectPayload(session: DubSession, name: string) {
  const prior = session.project;
  return {
    name: name.trim(),
    video_path: prior?.video_path || session.filename,
    audio_path: prior?.audio_path ?? null,
    duration: prior?.duration ?? Math.max(0, ...session.segments.map((s) => s.end)),
    state: {
      ...prior?.state,
      dubJobId: session.jobId,
      dubFilename: session.filename,
      dubSegments: session.segments,
      dubLang: session.target,
      multiLangs: session.multiTargets,
      translateQuality: session.quality,
      autoGlossary: session.autoGlossary,
      reflectPass: session.reflectPass,
      condenseSuggest: session.condenseSuggest,
      dubDialect: session.dialect,
      translationInstructions: session.translationInstructions,
      exportOptions: session.exportOptions,
      ...(session.exportOptions
        ? {
            preserveBg: session.exportOptions.preserveBg,
            defaultTrack: session.exportOptions.track,
            burnSubs: session.exportOptions.burn,
            dualSubs: session.exportOptions.dual,
            karaokeSubs: session.exportOptions.karaoke,
            exportTracks: Object.fromEntries(
              ['original', ...session.tracks, ...(session.exportOptions.excluded || [])].map(
                (code) => [code, !session.exportOptions?.excluded?.includes(code)],
              ),
            ),
          }
        : {}),
      fitOptions: session.fitOptions,
      steps: session.steps,
      cfg: session.guidance,
      speed: session.speed,
      dubInstruct: session.instruct,
      sourceLanguage: session.sourceLanguage,
      dubNumSpeakers: session.numSpeakers,
      timingStrategy: session.timingStrategy,
      voiceMatch: session.voiceMatch,
      generatedTiming: session.generatedTiming,
      segHashesByLang: session.fingerprintsByLang,
      dubLangCode: LANG_CODES.find((lang) => lang.label === session.target)?.code || 'auto',
      dubSourceLangCode: session.sourceLang,
      dubTracks: session.tracks,
      dubStep: session.phase,
      inputType: session.inputType,
    },
  };
}
