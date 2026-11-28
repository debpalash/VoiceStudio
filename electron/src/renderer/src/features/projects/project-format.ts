import { translationTargetCode } from '@/lib/translation-targets';
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
      filename: s.dubFilename || project.video_path || project.audio_path,
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
        ...(typeof s.exportOptions === 'object' && s.exportOptions ? s.exportOptions : {}),
      },
      fitOptions: s.fitOptions,
      steps: s.steps,
      guidance: s.cfg,
      speed: s.speed,
      instruct: s.dubInstruct,
      sourceLanguage: s.sourceLanguage,
      numSpeakers: s.dubNumSpeakers,
      timingStrategy: s.timingStrategy,
      autoFitOverflow: s.autoFitOverflow === true,
      voiceMatch: s.voiceMatch,
      generatedTiming: s.generatedTiming,
      fingerprintsByLang: s.segHashesByLang,
      generatedRequestKeys: s.generatedRequestKeys,
      exportedRequestKeys: s.exportedRequestKeys,
      lastGenerationError: s.lastGenerationError,
      sourceLang: s.dubSourceLangCode,
      tracks: s.dubTracks,
      phase: s.dubStep,
      inputType: s.inputType ?? (!project.video_path && project.audio_path ? 'audio' : 'video'),
      narration: s.narration,
      narrationWanted: s.narrationWanted,
      listenedRequestKeys: s.listenedRequestKeys,
      subtitleOnly: s.subtitleOnly,
      skipTranscription: s.skipTranscription,
      separateBackground: s.separateBackground,
      subtitleTargetLang: s.subtitleTargetLang,
    }),
    defaults,
  );
  return { ...restored, project, taskId: null, recovery: null };
}
export function projectPayload(session: DubSession, name: string) {
  const prior = session.project;
  const sourceDuration = session.duration;
  return {
    name: name.trim(),
    video_path: session.subtitleOnly ? null : prior?.video_path || session.filename,
    audio_path: prior?.audio_path ?? null,
    duration:
      typeof sourceDuration === 'number' && Number.isFinite(sourceDuration) && sourceDuration > 0
        ? sourceDuration
        : (prior?.duration ??
          session.segments.reduce((end, segment) => Math.max(end, segment.end), 0)),
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
            exportTracks: session.exportOptions.excluded
              ? Object.fromEntries(
                  ['original', ...session.tracks, ...session.exportOptions.excluded].map(
                    (code) => [code, !session.exportOptions?.excluded?.includes(code)],
                  ),
                )
              : undefined,
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
      autoFitOverflow: session.autoFitOverflow === true,
      voiceMatch: session.voiceMatch,
      generatedTiming: session.generatedTiming,
      segHashesByLang: session.fingerprintsByLang,
      generatedRequestKeys: session.generatedRequestKeys,
      exportedRequestKeys: session.exportedRequestKeys,
      lastGenerationError: session.lastGenerationError,
      dubLangCode: translationTargetCode(session.target) || 'auto',
      dubSourceLangCode: session.sourceLang,
      dubTracks: session.tracks,
      dubStep: session.phase,
      inputType: session.inputType,
      narration: session.narration,
      narrationWanted: session.narrationWanted,
      listenedRequestKeys: session.listenedRequestKeys,
      subtitleOnly: session.subtitleOnly,
      skipTranscription: session.skipTranscription,
      separateBackground: session.separateBackground,
      subtitleTargetLang: session.subtitleTargetLang,
    },
  };
}
