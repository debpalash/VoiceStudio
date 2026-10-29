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
import { cloneSettingsStore, DEFAULT_CLONE_SETTINGS } from '@/lib/store/clone-settings';
import { setLatestOutput } from '@/lib/store/output';
import { referenceStore } from '@/lib/store/reference';
import { beginAppActivity } from '@/lib/app-activity';
import { isPresetOnly, useTtsReadiness } from './use-tts-readiness';
import { useEngines } from './use-engines';
import { expressionCaps } from '@/lib/expression';
import { recordActionBreadcrumb } from '@/lib/report-breadcrumb';
import { announceDroppedSpeech } from '@/lib/dropped-speech';

const TIMER_TICK_MS = 100;
const PROGRESS_THROTTLE_MS = 100;
const MODEL_NOT_DOWNLOADED = 'model_not_downloaded';

// One synthesis at a time across every mounted hook instance: the backend
// serialises on the GPU anyway, and a second request would only queue behind
// the first while the UI shows two spinners.

// The routing notice fires once per distinct status per session — a batch of
// renders on a CPU-fallback box must not toast on every take.
let lastRoutingStatus: string | null = null;

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
  cancel(): void;
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
  // Inline-tag pre-flight (services/tag_policy): a reaction tag the engine does
  // not render is spoken as a literal word. The take is clean, just wrong —
  // name the tags once; removing them or switching engines is the fix.
  if (result.tagWarning?.count) {
    toast.warning(tr('tts.tagWarning', { tags: result.tagWarning.tags.join(', ') }));
  }
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

  // Trailing-edge throttle can fire after completion; `activeRef` gates it so
  // a stale 100% never lands on an idle form.
  const onProgress = useMemo(
    () =>
      throttle(
        (pct: number | null) => {
          if (activeRef.current) {
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
      abortRef.current?.abort();
    },
    [],
  );

  /** Synthesize a clone or designed take; only supported clone engines get emotion. */
  const generate = useCallback(
    async (design?: DesignGenerateInput) => {
      const settings = cloneSettingsStore.state;
      const reference = referenceStore.state;
      if (
        !cachedTtsLanguagesSupported(queryClient, design ? 'tts' : 'clone', [
          design?.language ?? settings.language,
        ])
      ) {
        setError(tr('languagePicker.chooseSupported'));
        return;
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
        return;
      }
      const release = acquireSynthesis();
      if (!release) {
        toast.info(tr('tts_errors.generation_in_progress'));
        return;
      }
      const finishActivity = beginAppActivity('synthesis');
      const reportMode = design ? 'design' : 'clone';
      reportModeRef.current = reportMode;
      recordActionBreadcrumb(`generate:${reportMode}:start`);

      activeRef.current = true;
      cancelledRef.current = false;
      setError(null);
      setErrorTopic(null);
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
        const input = {
          text: design?.text ?? settings.text,
          seed: design?.seed,
          language: design?.language ?? settings.language,
          // A preset-only engine renders its preset voice; sending the saved
          // voice or clip would be refused (it cannot clone).
          profileId: design
            ? (design.profileId ?? null)
            : presetOnly
              ? null
              : settings.selectedProfileId,
          refAudio: design || settings.selectedProfileId || presetOnly ? null : reference.file,
          refText:
            design || presetOnly
              ? undefined
              : settings.selectedProfileId
                ? manualProfileTranscript(
                    settings.refText,
                    queryClient
                      .getQueryData<Profile[]>(queryKeys.profiles)
                      ?.find((profile) => profile.id === settings.selectedProfileId)?.ref_text,
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
          steps: effectiveSamplingSteps(
            settings.steps,
            queryClient.getQueryData<EnginesResponse>(queryKeys.engines)?.tts?.active,
          ),
          wavBits: settings.wavBits,
          effectPreset: settings.effectPreset,
          presetVoice: activeTts?.preset_voices?.includes(settings.presetVoice)
            ? settings.presetVoice
            : undefined,
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
        const result = await generateClone(input, {
          signal: controller.signal,
          onProgress,
        });
        announceResultNotices(result);
        setLatestOutput(result, design?.text ?? settings.text);
        recordActionBreadcrumb(`generate:${reportMode}:complete`);
        toast.success(tr('tts.generationComplete'), { position: 'top-right' });
        if (!design)
          // Record what was actually rendered: a take sent without emotion (an
          // engine without IndexTTS2 controls, or Off) or without a delivery
          // direction is saved without it, so reusing it later never adds
          // direction the recording did not use.
          rememberTake(result.id, {
            ...settings,
            direction,
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
        if (!cancelledRef.current) {
          recordActionBreadcrumb(`generate:${reportMode}:error`);
          setErrorTopic(failureTopicOf(err) ?? null);
          setError(
            isAbortError(err)
              ? tr('tts_errors.timeout')
              : isModelNotDownloaded(err)
                ? tr('tts_errors.model_not_downloaded')
                : describeError(err),
          );
        }
      } finally {
        finishActivity();
        release();
        activeRef.current = false;
        abortRef.current = null;
        reportModeRef.current = null;
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
      }
    },
    [
      queryClient,
      onProgress,
      blocker,
      designBlockerFor,
      instructVocabulary,
      activeTts,
      presetOnly,
      sendsDirection,
    ],
  );

  const cancel = useCallback(() => {
    if (!abortRef.current) return;
    cancelledRef.current = true;
    const reportMode = reportModeRef.current;
    if (reportMode) recordActionBreadcrumb(`generate:${reportMode}:cancel`);
    abortRef.current.abort();
  }, []);

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
    clearError: () => {
      setError(null);
      setErrorTopic(null);
    },
    canGenerate: blocker === null && !isGenerating,
    canGenerateDesign: designBlocker === null && !isGenerating,
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
