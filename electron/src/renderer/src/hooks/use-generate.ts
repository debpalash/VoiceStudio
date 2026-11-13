import { acquireSynthesis } from '@/lib/synthesis-lock';
import { useCloneInputsReadiness } from './use-clone-readiness';
import type { CloneBlocker } from '@/lib/clone-readiness';
import { setWorkspace } from '@/lib/store/workspace';
import { rememberTake } from '@/lib/store/takes';
import {
  createContext,
  createElement,
  useContext,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { throttle } from '@tanstack/react-pacer';
import { toast } from 'sonner';
import { ApiError, apiJson, describeError, isAbortError } from '@/lib/api/client';
import { failureTopicOf } from '@shared/utils/failureCatalog';
import { generateClone, sanitizeInstruct } from '@/lib/api/generate';
import type {
  DesignRecipe,
  EnginesResponse,
  GenerateResult,
  InstructVocabulary,
  Profile,
} from '@/lib/api/types';
import { effectiveSamplingSteps } from '@/lib/audio/quality';
import { cachedTtsLanguagesSupported } from '@/lib/language-options';
import { tr } from '@/lib/i18n-text';
import { queryKeys } from '@/lib/query';
import {
  cloneSettingsStore,
  DEFAULT_CLONE_SETTINGS,
  type CloneSettings,
} from '@/lib/store/clone-settings';
import { setLatestOutput } from '@/lib/store/output';
import { referenceStore } from '@/lib/store/reference';
import { beginAppActivity } from '@/lib/app-activity';
import { isPresetOnly, useTtsReadiness } from './use-tts-readiness';
import { useEngines } from './use-engines';
import { expressionCaps } from '@/lib/expression';
import { recordActionBreadcrumb } from '@/lib/report-breadcrumb';
import { useResolvedLicenceError } from './use-resolved-licence-error';
import { announceDroppedSpeech } from '@/lib/dropped-speech';
import { recordTakeSpeed } from '@/lib/store/quality-offer';
import {
  transcriptSaveMarker,
  transcriptSavedSince,
  voiceChangePending,
  voiceChangesSettled,
} from '@/lib/store/transcript-saves';

const TIMER_TICK_MS = 100;
const PROGRESS_THROTTLE_MS = 100;
const MODEL_NOT_DOWNLOADED = 'model_not_downloaded';

// One synthesis at a time across every mounted hook instance: the backend
// serialises on the GPU anyway. Takes requested while one renders wait in a
// client-side queue and start one after another.

// The routing notice fires once per distinct status per session — a batch of
// renders on a CPU-fallback box must not toast on every take.
let lastRoutingStatus: string | null = null;

/** Takes that may wait at once; beyond this a click is refused with a toast. */
const MAX_QUEUED = 50;
/** Finished entries kept in the queue list before the oldest drop off. */
const MAX_FINISHED_SHOWN = 20;
const QUEUE_LABEL_CHARS = 80;
/** How often a waiting take re-checks a synthesis slot another feature holds. */
const QUEUE_RETRY_MS = 1000;
let takeCounter = 0;

type CloneInput = Parameters<typeof generateClone>[0];

export type GenerationQueueStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

export interface GenerationQueueItem {
  id: string;
  /** The start of the script, for display. */
  label: string;
  /** Saved voice, clip file or preset voice name, when there is one. */
  voice: string | null;
  mode: 'clone' | 'design';
  status: GenerationQueueStatus;
  error?: string;
}

/** Everything a take needs, captured when it was requested. */
interface PreparedTake {
  id: string;
  mode: 'clone' | 'design';
  input: CloneInput;
  /** Sampling steps actually sent, resolved for the engine active at request time. */
  steps: ReturnType<typeof effectiveSamplingSteps>;
  spokenText: string;
  direction: string;
  settings: CloneSettings;
  engine: { id: string; device: Parameters<typeof recordTakeSpeed>[1] } | null;
  label: string;
  voice: string | null;
  /** The saved voice's conditioning when requested; null for a clip, preset or design take. */
  voiceFingerprint: string | null;
  /**
   * For a take that sends no transcript of its own: the saved transcript when
   * it was requested and a marker for user saves after that point.
   */
  transcriptBaseline: { transcript: string | null; marker: number } | null;
}

/**
 * Which voice /generate renders for a saved profile: the reference clip
 * (replacing it writes a new versioned file), the locked take, seed and style.
 * A queued take compares this when it starts, because the backend resolves the
 * profile at render time, not at request time.
 *
 * The transcript is checked separately (`transcriptSavedSince`): the backend
 * fills or repairs some transcripts on its own during a render, and those must
 * not skip the takes queued behind it; only a transcript the user saved does.
 */
export function voiceFingerprint(profile: Profile | null | undefined): string | null {
  if (!profile) return null;
  return JSON.stringify([
    profile.ref_audio_path ?? null,
    profile.locked_audio_path || null,
    Boolean(profile.is_locked),
    profile.seed ?? null,
    profile.instruct ?? null,
  ]);
}

function trimFinished(items: GenerationQueueItem[]): GenerationQueueItem[] {
  const finished = items.filter((item) => item.status !== 'queued' && item.status !== 'running');
  const drop = new Set(
    finished.slice(0, Math.max(0, finished.length - MAX_FINISHED_SHOWN)).map((item) => item.id),
  );
  return drop.size ? items.filter((item) => !drop.has(item.id)) : items;
}

export interface DesignGenerateInput {
  text: string;
  instruct: string;
  /** Omitted to use the linked profile's own (possibly unset) seed. */
  seed?: number;
  language?: string;
  profileId?: string | null;
  /** Draft to record alongside the take (display only; the audio ignores it). */
  recipe?: DesignRecipe;
}

/**
 * `design`: the engine can't design and there is no saved sample to clone.
 * `cloning`: a saved sample would be re-rendered, but the engine ignores
 * reference audio, so it would speak with a preset voice instead.
 */
export type DesignBlocker = 'engine' | 'loading' | 'design' | 'cloning' | null;

/**
 * True when re-rendering this linked design profile clones its saved sample
 * (the locked take, else the rendered sample), so the engine designs nothing.
 * Mirrors /generate's profile conditioning for an undiverged design request.
 */
export function clonesSavedSample(profile: Profile | null | undefined): boolean {
  return Boolean(
    profile && ((profile.is_locked && profile.locked_audio_path) || profile.ref_audio_path),
  );
}

export interface UseGenerateClone {
  generateDesign(input: DesignGenerateInput): Promise<void>;
  generate(): Promise<void>;
  /** Stop the running take; the queue moves on to the next one. */
  cancel(): void;
  /** Stop the running take and drop every waiting one. */
  cancelAll(): void;
  /** Running, waiting and recently finished takes, oldest first. */
  queue: GenerationQueueItem[];
  /** How many takes are waiting (not counting the running one). */
  queuedCount: number;
  removeQueued(id: string): void;
  clearFinished(): void;
  isGenerating: boolean;
  elapsedSeconds: number;
  /** 0..100 while the WAV body streams in; null when unknown. */
  progress: number | null;
  stage: 'preparing' | 'loading' | 'generating' | 'receiving';
  modelStage: 'importing' | 'loading_weights' | 'loading_asr' | 'compiling' | null;
  /** 0..100 reported while model weights or runtime are loading. */
  modelProgress: number | null;
  error: string | null;
  /** Stable backend failure topic of `error` (e.g. `REFERENCE_STT_MISSING`), when it sent one. */
  errorTopic: string | null;
  clearError(): void;
  canGenerate: boolean;
  canGenerateDesign: boolean;
  /** `design`: the active engine needs a reference clip, so it can't design. */
  designBlocker: DesignBlocker;
  /**
   * The blocker for re-rendering `profile`: cloning its saved sample isn't
   * design, but it does need an engine that reads reference audio.
   */
  designBlockerFor(profile: Profile | null | undefined): DesignBlocker;
  cloneBlocker: CloneBlocker;
  /** The active engine's reading of `instruct`; the OmniVoice tag list unless it says otherwise. */
  instructVocabulary: InstructVocabulary;
}

/** A saved voice carries its own transcript; send only a manual correction (#3192),
 * so unchanged or blank text keeps the server's stored-transcript path. */
export function manualProfileTranscript(
  typed: string,
  stored: string | null | undefined,
): string | undefined {
  return typed.trim() && typed !== (stored ?? '') ? typed : undefined;
}

function isModelNotDownloaded(err: unknown): boolean {
  if (!(err instanceof ApiError) || !err.payload) return false;
  const detail = err.payload.detail;
  return (
    typeof detail === 'object' &&
    detail !== null &&
    (detail as { error?: unknown }).error === MODEL_NOT_DOWNLOADED
  );
}

function announceInstructWarnings(free: string): string {
  const { instruct, unsupported, duplicates, conflicts } = sanitizeInstruct(free);
  if (unsupported.length) {
    toast.warning(tr('tts_errors.ignored_unsupported', { items: unsupported.join(', ') }));
  }
  if (duplicates.length) {
    toast.warning(tr('tts_errors.ignored_duplicate', { items: duplicates.join(', ') }));
  }
  if (conflicts.length) {
    toast.warning(tr('tts_errors.ignored_conflict', { items: conflicts.join(', ') }));
  }
  return instruct;
}

function announceResultNotices(result: GenerateResult): void {
  announceDroppedSpeech(result.dropped);
  // The backend only sets the routing headers on cpu_fallback / accelerated-
  // with-caveat, so their presence is the signal.
  if (result.routing && result.routing.status !== lastRoutingStatus) {
    lastRoutingStatus = result.routing.status;
    const { status, reason } = result.routing;
    if (status === 'cpu_fallback') {
      toast.info(tr('tts.routingFallback', { reason }));
    } else if (status === 'accelerated' && reason) {
      toast.warning(tr('tts.routingCaveat', { reason }));
    }
  }
}

/** Drive a single synthesis lifecycle and forward per-take engine capabilities.
 *
 * Cloning may include IndexTTS2 text or vector emotion; unsupporting engines
 * and the independent Voice Design workspace never inherit those controls.
 */
function useGenerateController(): UseGenerateClone {
  const queryClient = useQueryClient();
  const inputBlocker = useCloneInputsReadiness();
  const ttsBlocker = useTtsReadiness();
  const cloneEngineBlocker = useTtsReadiness('clone');
  const blocker = cloneEngineBlocker ?? inputBlocker;
  const activeTts = useEngines().activeTts;
  const instructVocabulary = activeTts?.instruct_vocabulary ?? 'tags';
  const sendsDirection = expressionCaps(activeTts).emotion === 'direction';
  // An engine that declares it can't design would only fail inside the
  // engine (and /generate refuses it); say so before anything starts.
  const designBlocker: DesignBlocker =
    activeTts?.supports_voice_design === false ? 'design' : ttsBlocker;
  const cannotClone = activeTts?.supports_cloning === false;
  const presetOnly = isPresetOnly(activeTts);
  const designBlockerFor = useCallback(
    (profile: Profile | null | undefined): DesignBlocker => {
      if (!clonesSavedSample(profile)) return designBlocker;
      // /generate refuses a saved sample on an engine that ignores it.
      return ttsBlocker ?? (cannotClone ? 'cloning' : null);
    },
    [cannotClone, designBlocker, ttsBlocker],
  );
  const [error, setError] = useState<string | null>(null);
  const [errorTopic, setErrorTopic] = useState<string | null>(null);
  const [errorCause, setErrorCause] = useState<unknown>(null);
  const clearError = useCallback(() => {
    setError(null);
    setErrorTopic(null);
    setErrorCause(null);
  }, []);
  useResolvedLicenceError(errorCause, clearError);
  const [isGenerating, setIsGenerating] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [progress, setProgress] = useState<number | null>(null);
  const [stage, setStage] = useState<UseGenerateClone['stage']>('preparing');
  const [modelStage, setModelStage] = useState<UseGenerateClone['modelStage']>(null);
  const [modelProgress, setModelProgress] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const reportModeRef = useRef<'clone' | 'design' | null>(null);
  const cancelledRef = useRef(false);
  const activeRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // A take that waited for model loading says nothing about render speed.
  const sawModelLoadRef = useRef(false);
  // Takes waiting their turn, oldest first; the running one is not in here.
  const pendingRef = useRef<PreparedTake[]>([]);
  const currentTakeIdRef = useRef<string | null>(null);
  const drainRetryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const drainRef = useRef<() => void>(() => {});
  const [queue, setQueue] = useState<GenerationQueueItem[]>([]);

  // Trailing-edge throttle can fire after completion; `activeRef` gates it so
  // a stale 100% never lands on an idle form, and the take id so a queued take
  // that has already started never shows the previous take's last progress.
  const onProgress = useMemo(
    () =>
      throttle(
        (takeId: string, pct: number | null) => {
          if (activeRef.current && currentTakeIdRef.current === takeId) {
            setProgress(pct);
            setStage('receiving');
          }
        },
        { wait: PROGRESS_THROTTLE_MS, leading: true, trailing: true },
      ),
    [],
  );

  useEffect(() => {
    if (!isGenerating) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const model = await apiJson<{
          status: string;
          loading?: boolean;
          sub_stage?: string;
          progress?: number;
          checkpoint?: string | null;
          detail?: string | null;
          error?: string | null;
        }>('/model/status', { signal: controller.signal });
        if (!controller.signal.aborted) {
          // The composer and Engine Ready panel describe the same runtime.
          // Publish this faster generation-time sample instead of making the
          // sidebar wait for its slower idle poll.
          queryClient.setQueryData(['sidebar-model-status'], model);
          const nextModelStage = [
            'importing',
            'loading_weights',
            'loading_asr',
            'compiling',
          ].includes(model.sub_stage ?? '')
            ? (model.sub_stage as UseGenerateClone['modelStage'])
            : null;
          if (nextModelStage || model.loading || model.status === 'loading')
            sawModelLoadRef.current = true;
          setModelStage(nextModelStage);
          setModelProgress(
            typeof model.progress === 'number' ? Math.max(0, Math.min(100, model.progress)) : null,
          );
          setStage((current) =>
            current === 'receiving'
              ? current
              : model.loading || model.status === 'loading'
                ? 'loading'
                : 'generating',
          );
        }
      } catch {
        /* Status is optional; never interrupt the generation request. */
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 750);
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [isGenerating, queryClient]);

  useEffect(
    () => () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (drainRetryRef.current) clearTimeout(drainRetryRef.current);
      pendingRef.current = [];
      abortRef.current?.abort();
    },
    [],
  );

  /** Patch one queue entry by id. */
  const patchQueueItem = useCallback((id: string, patch: Partial<GenerationQueueItem>) => {
    setQueue((items) => items.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  /**
   * Validate a take and snapshot everything it needs at click time, so the
   * user can edit the script, voice and settings for the next take while this
   * one waits in the queue. Returns null (with any feedback shown) when the
   * take can't be rendered.
   */
  const prepareTake = useCallback(
    (design?: DesignGenerateInput): PreparedTake | null => {
      const settings = cloneSettingsStore.state;
      const reference = referenceStore.state;
      if (
        !cachedTtsLanguagesSupported(queryClient, design ? 'tts' : 'clone', [
          design?.language ?? settings.language,
        ])
      ) {
        setErrorCause(null);
        setError(tr('languagePicker.chooseSupported'));
        return null;
      }
      if (
        design
          ? designBlockerFor(
              design.profileId
                ? queryClient
                    .getQueryData<Profile[]>(queryKeys.profiles)
                    ?.find((profile) => profile.id === design.profileId)
                : null,
            ) || !design.text.trim()
          : blocker ||
            !settings.text.trim() ||
            // A preset-only engine speaks its preset voice: no clip or profile.
            (!presetOnly && !settings.selectedProfileId && !reference.file?.size)
      ) {
        if (blocker === 'reference') setWorkspace({ panel: 'voice' });
        return null;
      }
      // A preset-only engine that can't design never reads a style
      // instruct, and /generate refuses one there as a design request: the
      // preset voice take keeps working with a leftover instruct (#3413).
      const style =
        design?.instruct ??
        (presetOnly && activeTts?.supports_voice_design === false ? '' : settings.instruct);
      // Delivery direction is a per-engine draft like IndexTTS2 emotion: only
      // engines whose instruct steers a clone receive it; others never see it.
      const direction = !design && sendsDirection ? settings.direction.trim() : '';
      const free = [style.trim(), direction].filter(Boolean).join(', ');
      // Only tag-list engines need the text filtered (with warnings); a
      // free-form engine reads whatever was written.
      const wording = free.trim();
      let instruct = wording;
      if (wording && instructVocabulary !== 'freeform') instruct = announceInstructWarnings(free);
      const profiles = queryClient.getQueryData<Profile[]>(queryKeys.profiles);
      const profileId = design
        ? (design.profileId ?? null)
        : presetOnly
          ? null
          : settings.selectedProfileId;
      const refAudio = design || settings.selectedProfileId || presetOnly ? null : reference.file;
      const presetVoice = activeTts?.preset_voices?.includes(settings.presetVoice)
        ? settings.presetVoice
        : undefined;
      // Pin the engine active now: a take that waits in the queue must not move
      // to an engine switched to in Settings meanwhile, which would read this
      // take's engine-specific controls (steps, presets, emotion) differently.
      const engineId =
        queryClient.getQueryData<EnginesResponse>(queryKeys.engines)?.tts?.active ??
        activeTts?.id ??
        undefined;
      const steps = effectiveSamplingSteps(settings.steps, engineId);
      const input: CloneInput = {
        text: design?.text ?? settings.text,
        seed: design?.seed,
        language: design?.language ?? settings.language,
        // A preset-only engine renders its preset voice; sending the saved
        // voice or clip would be refused (it cannot clone).
        profileId,
        refAudio,
        refText:
          design || presetOnly
            ? undefined
            : settings.selectedProfileId
              ? manualProfileTranscript(
                  settings.refText,
                  profiles?.find((profile) => profile.id === settings.selectedProfileId)?.ref_text,
                )
              : settings.refText,
        instruct,
        instructVocabulary,
        designRecipe: design?.recipe,
        // IndexTTS2's emotion controls affect the performance, not the
        // reference voice. Never leak stale emotion options to other engines
        // or to the separate Voice Design workspace.
        ...(!design && activeTts?.supports_emotion
          ? settings.emotionMode === 'vector'
            ? { emoVector: [...settings.emotionVector] }
            : settings.emotionMode === 'text' && settings.emotionText.trim()
              ? { emoText: settings.emotionText.trim(), emoAlpha: settings.emotionAlpha }
              : {}
          : {}),
        steps,
        ...(engineId ? { engine: engineId } : {}),
        wavBits: settings.wavBits,
        effectPreset: settings.effectPreset,
        presetVoice,
        cfg: settings.cfg,
        speed: settings.speed,
        tShift: settings.tShift,
        posTemp: settings.posTemp,
        classTemp: settings.classTemp,
        layerPenalty: settings.layerPenalty,
        denoise: settings.denoise,
        postprocess: settings.postprocess,
        duration: settings.duration,
      };
      const spokenText = input.text.trim();
      return {
        id: `take-${Date.now().toString(36)}-${(takeCounter += 1)}`,
        mode: design ? 'design' : 'clone',
        input,
        steps,
        spokenText,
        direction,
        settings: { ...settings, emotionVector: [...settings.emotionVector] },
        // The take renders on the pinned engine, so its speed sample belongs there.
        engine: engineId
          ? {
              id: engineId,
              device: activeTts?.id === engineId ? activeTts.effective_device : undefined,
            }
          : null,
        label:
          spokenText.length > QUEUE_LABEL_CHARS
            ? `${spokenText.slice(0, QUEUE_LABEL_CHARS).trimEnd()}…`
            : spokenText,
        voice:
          (profileId && profiles?.find((profile) => profile.id === profileId)?.name) ||
          refAudio?.name ||
          presetVoice ||
          null,
        // Only a clone take renders through its saved voice's stored conditioning.
        voiceFingerprint:
          !design && profileId
            ? voiceFingerprint(profiles?.find((profile) => profile.id === profileId))
            : null,
        // A manual transcript travels with the request, so only the stored one can drift.
        transcriptBaseline:
          !design && profileId && input.refText === undefined
            ? {
                transcript: profiles?.find((profile) => profile.id === profileId)?.ref_text ?? null,
                marker: transcriptSaveMarker(),
              }
            : null,
      };
    },
    [
      queryClient,
      blocker,
      designBlockerFor,
      instructVocabulary,
      activeTts,
      presetOnly,
      sendsDirection,
    ],
  );

  /** Drive one synthesis lifecycle; the caller already holds the synthesis slot. */
  const runTake = useCallback(
    async (take: PreparedTake, release: () => void) => {
      const finishActivity = beginAppActivity('synthesis');
      const reportMode = take.mode;
      const { input } = take;
      reportModeRef.current = reportMode;
      currentTakeIdRef.current = take.id;
      recordActionBreadcrumb(`generate:${reportMode}:start`);
      patchQueueItem(take.id, { status: 'running' });

      activeRef.current = true;
      cancelledRef.current = false;
      sawModelLoadRef.current = false;
      setError(null);
      setErrorTopic(null);
      setErrorCause(null);
      setIsGenerating(true);
      setElapsedSeconds(0);
      setProgress(null);
      setStage('preparing');
      setModelStage(null);
      setModelProgress(null);
      const startedAt = Date.now();
      timerRef.current = setInterval(() => {
        setElapsedSeconds(Math.round((Date.now() - startedAt) / TIMER_TICK_MS) / 10);
      }, TIMER_TICK_MS);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        if (take.voiceFingerprint) {
          // An edit, lock or unlock still in flight has reached (or may reach) the
          // backend before the cache: wait for it to settle and refresh first.
          if (input.profileId && voiceChangePending(input.profileId))
            await voiceChangesSettled(input.profileId, controller.signal);
          // A voice replaced, re-locked, restyled or re-transcribed while the take waited
          // would render a voice the user never queued: skip it and say why.
          const current = queryClient
            .getQueryData<Profile[]>(queryKeys.profiles)
            ?.find((profile) => profile.id === input.profileId);
          if (
            current &&
            (voiceFingerprint(current) !== take.voiceFingerprint ||
              (take.transcriptBaseline !== null &&
                transcriptSavedSince(
                  current.id,
                  take.transcriptBaseline.marker,
                  take.transcriptBaseline.transcript,
                )))
          )
            throw new Error(tr('generationQueue.voice_changed'));
        }
        const result = await generateClone(input, {
          signal: controller.signal,
          onProgress: (pct) => onProgress(take.id, pct),
        });
        announceResultNotices(result);
        if (
          take.engine &&
          !sawModelLoadRef.current &&
          result.genTimeSeconds !== null &&
          result.durationSeconds !== null
        )
          // Local only: per engine and device, so the next estimate is this machine's.
          recordTakeSpeed(take.engine.id, take.engine.device, {
            chars: take.spokenText.length,
            audioSeconds: result.durationSeconds,
            renderSeconds: result.genTimeSeconds,
            steps: take.steps,
            at: Date.now(),
          });
        setLatestOutput(result, input.text);
        recordActionBreadcrumb(`generate:${reportMode}:complete`);
        patchQueueItem(take.id, { status: 'done' });
        toast.success(tr('tts.generationComplete'), { position: 'top-right' });
        if (reportMode === 'clone')
          // Record what was actually rendered: a take sent without emotion (an
          // engine without IndexTTS2 controls, or Off) or without a delivery
          // direction is saved without it, so reusing it later never adds
          // direction the recording did not use.
          rememberTake(result.id, {
            ...take.settings,
            // The steps actually rendered, not "best quality" for whichever engine is active later.
            steps: take.steps,
            direction: take.direction,
            ...('emoText' in input || 'emoVector' in input
              ? {}
              : {
                  emotionMode: DEFAULT_CLONE_SETTINGS.emotionMode,
                  emotionText: DEFAULT_CLONE_SETTINGS.emotionText,
                  emotionAlpha: DEFAULT_CLONE_SETTINGS.emotionAlpha,
                  emotionVector: [...DEFAULT_CLONE_SETTINGS.emotionVector],
                }),
          });
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.history }),
          queryClient.invalidateQueries({ queryKey: ['loaded-models'] }),
          queryClient.invalidateQueries({ queryKey: queryKeys.engines }),
        ]).catch(() => {});
      } catch (err) {
        if (cancelledRef.current) {
          patchQueueItem(take.id, { status: 'cancelled' });
        } else {
          recordActionBreadcrumb(`generate:${reportMode}:error`);
          const message = isAbortError(err)
            ? tr('tts_errors.timeout')
            : isModelNotDownloaded(err)
              ? tr('tts_errors.model_not_downloaded')
              : describeError(err);
          setErrorCause(err);
          setErrorTopic(failureTopicOf(err) ?? null);
          setError(message);
          patchQueueItem(take.id, { status: 'failed', error: message });
        }
      } finally {
        finishActivity();
        release();
        activeRef.current = false;
        abortRef.current = null;
        reportModeRef.current = null;
        currentTakeIdRef.current = null;
        if (timerRef.current) clearInterval(timerRef.current);
        timerRef.current = null;
        setIsGenerating(false);
        setProgress(null);
        setModelStage(null);
        setModelProgress(null);
        void queryClient
          .invalidateQueries({
            queryKey: ['sidebar-model-status'],
          })
          .catch(() => {});
        // Start the next queued take, if any.
        drainRef.current();
      }
    },
    [queryClient, onProgress, patchQueueItem],
  );

  /**
   * Start the next waiting take when nothing is rendering. Another feature
   * (voice convert, SFX, a profile preview) may hold the shared synthesis
   * slot; then wait for it to free up instead of dropping the take.
   */
  const drain = useCallback(() => {
    if (activeRef.current || drainRetryRef.current) return;
    const next = pendingRef.current[0];
    if (!next) return;
    const release = acquireSynthesis();
    if (!release) {
      drainRetryRef.current = setTimeout(() => {
        drainRetryRef.current = null;
        drainRef.current();
      }, QUEUE_RETRY_MS);
      return;
    }
    pendingRef.current.shift();
    void runTake(next, release);
  }, [runTake]);
  drainRef.current = drain;

  /** Render a take now, or add it to the queue when one is already running. */
  const generate = useCallback(
    async (design?: DesignGenerateInput) => {
      if (design) {
        // Voice Design has no queue view to show, remove or cancel waiting
        // takes, so it keeps one render at a time: start now or say it's busy.
        // A waiting clone take keeps its turn even when the slot just freed up.
        if (activeRef.current || pendingRef.current.length > 0) {
          toast.info(tr('tts_errors.generation_in_progress'));
          return;
        }
        const take = prepareTake(design);
        if (!take) return;
        const release = acquireSynthesis();
        if (!release) {
          toast.info(tr('tts_errors.generation_in_progress'));
          return;
        }
        // Resolves when the take finishes, as callers such as first-sound onboarding expect.
        await runTake(take, release);
        return;
      }
      if (pendingRef.current.length >= MAX_QUEUED) {
        toast.warning(tr('generationQueue.full', { count: MAX_QUEUED }));
        return;
      }
      const take = prepareTake(design);
      if (!take) return;
      pendingRef.current.push(take);
      setQueue((items) => [
        // Keep only the most recent finished takes alongside the live ones.
        ...trimFinished(items),
        { id: take.id, label: take.label, voice: take.voice, mode: take.mode, status: 'queued' },
      ]);
      drain();
      if (pendingRef.current.includes(take))
        toast.info(tr('generationQueue.added', { count: pendingRef.current.length }));
    },
    [prepareTake, drain, runTake],
  );

  /** Drop a take that hasn't started yet. */
  const removeQueued = useCallback((id: string) => {
    pendingRef.current = pendingRef.current.filter((take) => take.id !== id);
    setQueue((items) => items.filter((item) => item.id !== id || item.status !== 'queued'));
  }, []);

  /** Remove finished, failed and cancelled entries from the list. */
  const clearFinished = useCallback(() => {
    setQueue((items) =>
      items.filter((item) => item.status === 'queued' || item.status === 'running'),
    );
  }, []);

  const cancel = useCallback(() => {
    if (!abortRef.current) return;
    cancelledRef.current = true;
    const reportMode = reportModeRef.current;
    if (reportMode) recordActionBreadcrumb(`generate:${reportMode}:cancel`);
    abortRef.current.abort();
  }, []);

  /** Stop the running take and drop everything still waiting. */
  const cancelAll = useCallback(() => {
    const dropped = new Set(pendingRef.current.map((take) => take.id));
    pendingRef.current = [];
    if (drainRetryRef.current) clearTimeout(drainRetryRef.current);
    drainRetryRef.current = null;
    setQueue((items) =>
      items.map((item) => (dropped.has(item.id) ? { ...item, status: 'cancelled' } : item)),
    );
    cancel();
  }, [cancel]);

  return {
    generate: () => generate(),
    generateDesign: generate,
    cancel,
    isGenerating,
    elapsedSeconds,
    progress,
    stage,
    modelStage,
    modelProgress,
    error,
    errorTopic,
    clearError,
    // A take started while another renders joins the queue.
    canGenerate: blocker === null,
    canGenerateDesign: designBlocker === null && !isGenerating,
    queue,
    queuedCount: queue.filter((item) => item.status === 'queued').length,
    removeQueued,
    clearFinished,
    cancelAll,
    designBlocker,
    designBlockerFor,
    cloneBlocker: blocker,
    instructVocabulary,
  };
}

const GenerationContext = createContext<UseGenerateClone | null>(null);
export function GenerationProvider({ children }: { children: ReactNode }) {
  const value = useGenerateController();
  return createElement(GenerationContext.Provider, { value }, children);
}
export function useGenerateClone(): UseGenerateClone {
  const value = useContext(GenerationContext);
  if (!value) throw new Error('GenerationProvider is required');
  return value;
}
