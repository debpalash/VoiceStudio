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
import { generateClone, sanitizeInstruct } from '@/lib/api/generate';
import type { EnginesResponse, GenerateResult } from '@/lib/api/types';
import { effectiveSamplingSteps } from '@/lib/audio/quality';
import { cachedTtsLanguagesSupported } from '@/lib/language-options';
import { tr } from '@/lib/i18n-text';
import { queryKeys } from '@/lib/query';
import { cloneSettingsStore } from '@/lib/store/clone-settings';
import { setLatestOutput } from '@/lib/store/output';
import { referenceStore } from '@/lib/store/reference';
import { beginAppActivity } from '@/lib/app-activity';
import { useTtsReadiness } from './use-tts-readiness';
import { recordActionBreadcrumb } from '@/lib/report-breadcrumb';

const TIMER_TICK_MS = 100;
const PROGRESS_THROTTLE_MS = 100;
const DROPPED_TOAST_MS = 8000;
const DROPPED_TEXT_PREVIEW_CHARS = 120;
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
  seed: number;
  language?: string;
  profileId?: string | null;
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
  clearError(): void;
  canGenerate: boolean;
  canGenerateDesign: boolean;
  designBlocker: 'engine' | 'loading' | null;
  cloneBlocker: CloneBlocker;
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

function supportsFreeformDesign(engines: EnginesResponse | undefined): boolean {
  const tts = engines?.tts;
  if (!tts) return false;
  if (tts.active === 'voxcpm2') return true;
  return tts.active === 'mlx-audio' && /qwen3|voicedesign/i.test(tts.active_model ?? '');
}

function announceResultNotices(result: GenerateResult): void {
  // Some of the text rendered to no audio: the take is clean but short, and
  // nothing else would ever tell the user — so quote what was lost.
  if (result.dropped) {
    const preview = result.dropped.text.trim().slice(0, DROPPED_TEXT_PREVIEW_CHARS);
    const count = result.dropped.count;
    toast.warning(
      preview
        ? tr('tts.droppedChunksWithText', { count, text: preview })
        : tr('tts.droppedChunks', { count }),
      { duration: DROPPED_TOAST_MS },
    );
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

function useGenerateController(): UseGenerateClone {
  const queryClient = useQueryClient();
  const inputBlocker = useCloneInputsReadiness();
  const designBlocker = useTtsReadiness();
  const cloneEngineBlocker = useTtsReadiness('clone');
  const blocker = cloneEngineBlocker ?? inputBlocker;
  const [error, setError] = useState<string | null>(null);
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
          ? designBlocker || !design.text.trim()
          : blocker ||
            !settings.text.trim() ||
            (!settings.selectedProfileId && !reference.file?.size)
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
        const free = design?.instruct ?? settings.instruct;
        const engines = queryClient.getQueryData<EnginesResponse>(queryKeys.engines);
        const instruct = free.trim()
          ? supportsFreeformDesign(engines)
            ? free.trim()
            : announceInstructWarnings(free)
          : '';
        const input = {
          text: design?.text ?? settings.text,
          seed: design?.seed,
          language: design?.language ?? settings.language,
          profileId: design ? (design.profileId ?? null) : settings.selectedProfileId,
          refAudio: design || settings.selectedProfileId ? null : reference.file,
          refText: design ? undefined : settings.refText,
          instruct,
          steps: effectiveSamplingSteps(
            settings.steps,
            queryClient.getQueryData<EnginesResponse>(queryKeys.engines)?.tts?.active,
          ),
          wavBits: settings.wavBits,
          effectPreset: settings.effectPreset,
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
        toast.success(tr('tts.generationComplete'));
        if (!design) rememberTake(result.id, settings);
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.history }),
          queryClient.invalidateQueries({ queryKey: ['loaded-models'] }),
          queryClient.invalidateQueries({ queryKey: queryKeys.engines }),
        ]).catch(() => {});
      } catch (err) {
        if (!cancelledRef.current) {
          recordActionBreadcrumb(`generate:${reportMode}:error`);
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
    [queryClient, onProgress, blocker, designBlocker],
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
    clearError: () => setError(null),
    canGenerate: blocker === null && !isGenerating,
    canGenerateDesign: designBlocker === null && !isGenerating,
    designBlocker,
    cloneBlocker: blocker,
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
