import { Menu } from '@base-ui/react/menu';
import { useVirtualizer } from '@tanstack/react-virtual';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { PipelineFailure } from '@/components/pipeline-failure';
import { EngineNotice } from '@/components/engine-notice';
import { AgentFixButton } from '@/components/agent-fix-button';
import { getBridge } from '@/components/bridge';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Switch } from '@/components/ui/switch';
import { MAX_COOKIE_EXPORT_BYTES } from '../../../../../../frontend/src/utils/cookieExport';
import {
  hasCompleteTranslation,
  multiLangTargets,
} from '../../../../../../frontend/src/utils/multiLang';
import { segmentGenInputs } from '../../../../../../frontend/src/utils/segments';
import { clampSegmentEdit } from '../../../../../../frontend/src/utils/timeline';
import {
  dialectLabel,
  dialectMatchesLang,
  dialectOptionsFor,
} from '../../../../../../frontend/src/api/dialects';
import { DubExportPanel } from './dub-export-panel';
import { DubTimeline } from './dub-timeline';
import { PasteTranslation } from './paste-translation';
import { GlossaryPanel } from './glossary-panel';
import { CastingBoard } from './casting-board';
import { DubbingDemo } from './dubbing-demo';
import { CheckpointBanner, type CheckpointStage } from './checkpoint-banner';
import { ConfirmDialog } from '../clone/confirm-dialog';
import { useDubOnsets } from './use-dub-onsets';
import { useDubLivePreview } from './use-dub-live-preview';
import { setDubQuality, setDubProduction, setDubTranslationOptions } from './dub-session';
import { DEFAULT_REPAIR_AGENT_KEY } from '@/lib/repair-agent-events';
import type { RepairAgentId, RepairAgentInfo } from '../../../../preload/index.d';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { runRendererTask } from '@/lib/global-error-recovery';
import { canCreateStoryFromDub, loadDubIntoStories, storiesDraftOccupied } from './dub-to-story';
import { useLongformSession } from '../longform/longform-session';
import { useTranslation } from 'react-i18next';
import {
  AlertCircleIcon,
  BookOpenIcon,
  CheckCheckIcon,
  ChevronDownIcon,
  ClipboardPasteIcon,
  Clock3Icon,
  AudioLinesIcon,
  FilmIcon,
  GaugeIcon,
  HeadphonesIcon,
  LanguagesIcon,
  LoaderCircleIcon,
  MergeIcon,
  MoreHorizontalIcon,
  PlayIcon,
  PlusIcon,
  Redo2Icon,
  RotateCcwIcon,
  ScissorsIcon,
  Settings2Icon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  Trash2Icon,
  Undo2Icon,
  UploadIcon,
  UsersRoundIcon,
  WandSparklesIcon,
  XIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { VideoPlayer } from '@/components/video-player';
import { WaveformPlayer } from '@/components/waveform-player';
import {
  videoSource as nativeVideoSource,
  type MediaPlayerInstance,
} from '@/components/media-player';
import { LanguagePicker, MultiLanguagePicker } from '@/features/clone/language-picker';
import { useTranslationEngines } from '@/features/settings/translation-settings';
import { agentFitSkillsReady, useLlmSkills } from '@/features/settings/llm-skills';
import { useModelCatalogue } from '@/features/settings/model-catalogue-query';
import { useProfiles } from '@/hooks/use-profiles';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';
import { useReviewMode } from '@/hooks/use-review-mode';
import { apiFetch, apiJson, apiPath, describeError, isAbortError } from '@/lib/api/client';
import { createObjectUrl, revokeObjectUrl } from '@/lib/audio/object-url';
import { requestPlaybackSeek } from '@/lib/audio/playback-clock';
import { beginAppActivity } from '@/lib/app-activity';
import { LANG_CODES } from '../../../../../../frontend/src/utils/languages';
import { cn } from '@/lib/utils';
import {
  useDubSession,
  useDubCancelling,
  uploadDub,
  translateDub,
  translateDubBatch,
  translateDubWithAgent,
  translateDubBatchWithAgent,
  generateDub,
  generateDubBatch,
  cancelDub,
  deleteDubSegment,
  deleteDubSegments,
  editDubSegment,
  editDubSegments,
  insertDubSegment,
  mergeDubSegment,
  moveResizeDubSegment,
  redoDubEdit,
  resumeDub,
  discardDubRecovery,
  resetDubSession,
  dismissDubError,
  applyDubQc,
  applyDubTranslationRows,
  assignDubSpeakerProfile,
  dubSession,
  DUB_STOP_FAILED,
  DUB_AGENT_UNAVAILABLE,
  setDubTarget,
  setDubMultiTargets,
  splitDubSegment,
  undoDubEdit,
  useDubEditHistory,
  importDubSubtitles,
  ingestDubUrl,
  isDubUrl,
  cleanupDubSegments,
  restoreDubSegments,
  skipFailedDubTranslations,
  planDubIncremental,
} from './dub-session';

const DEFAULT_TRANSLATION_AGENT_KEY = 'voicestudio.defaultTranslationAgent';
const LIVE_PREVIEW_KEY = 'voicestudio.dubLivePreview';
const targetOptions = LANG_CODES.map((item) => item.label);
const sentenceEnd = /[.!?\u3002\uff01\uff1f]/;

type ArgosPackStatus = {
  source_lang: string;
  pairs: Array<{
    source_lang: string;
    target_lang: string;
    installed: boolean;
  }>;
};

function compactSourceLabel(source: string): string {
  if (!source) return '';
  try {
    return new URL(source).hostname.replace(/^www\./, '');
  } catch {
    return source;
  }
}

function preparationStageKey(eventType: unknown) {
  const type = typeof eventType === 'string' ? eventType : '';
  if (type.startsWith('download')) return 'dub.prep_download';
  if (type.startsWith('extract')) return 'dub.prep_extract';
  if (type.startsWith('demucs')) return 'dub.prep_demucs';
  if (type.startsWith('scene')) return 'dub.prep_scene';
  if (type === 'cached') return 'dub.prep_cached';
  return 'dub.prep_preparing';
}

function bestSplitPoint(text: string) {
  const middle = Math.floor(text.length / 2);
  let best = -1;
  let distance = Number.POSITIVE_INFINITY;
  for (let index = 0; index < text.length; index += 1) {
    if (!sentenceEnd.test(text[index])) continue;
    const current = Math.abs(index + 1 - middle);
    if (current < distance) {
      best = index + 1;
      distance = current;
    }
  }
  if (best > 0 && best < text.length) return best;
  for (let radius = 0; radius < text.length; radius += 1) {
    for (const index of [middle - radius, middle + radius]) {
      if (index > 0 && index < text.length && /\s/.test(text[index])) return index;
    }
  }
  return middle;
}

export function fingerprintRevision(values?: Record<string, string>) {
  if (!values) return '';
  let hash = 2166136261;
  for (const [key, value] of Object.entries(values).sort(([left], [right]) =>
    left.localeCompare(right),
  )) {
    const text = `${key}:${value};`;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
  }
  return (hash >>> 0).toString(36);
}

export function DubPage() {
  const { t, i18n } = useTranslation();
  const reviewMode = useReviewMode();
  const session = useDubSession();
  const cancelling = useDubCancelling();
  const editHistory = useDubEditHistory();
  const input = useRef<HTMLInputElement>(null);
  const subtitles = useRef<HTMLInputElement>(null);
  const cursors = useRef(new Map<string, number>());
  const mainScroll = useRef<HTMLDivElement>(null);
  const segmentList = useRef<HTMLDivElement>(null);
  const [segmentScrollMargin, setSegmentScrollMargin] = useState(0);
  const target = session.target;
  const setTarget = setDubTarget;
  const [url, setUrl] = useState('');
  const [dragging, setDragging] = useState(false);
  const [cookieFile, setCookieFile] = useState<File>();
  const [cookieError, setCookieError] = useState(false);
  const [fetchSubs, setFetchSubs] = useState(false);
  const [removeVideoOpen, setRemoveVideoOpen] = useState(false);
  const [preview, setPreview] = useState('original');
  const [segmentPreview, setSegmentPreview] = useState<{
    id: string;
    url: string;
  } | null>(null);
  const [previewingSegmentId, setPreviewingSegmentId] = useState<string | null>(null);
  const [qcRunning, setQcRunning] = useState(false);
  const [pasteTranslationOpen, setPasteTranslationOpen] = useState(false);
  const [glossaryOpen, setGlossaryOpen] = useState(false);
  const [glossaryCount, setGlossaryCount] = useState(0);
  const [recoveringAsr, setRecoveringAsr] = useState(false);
  const [installingArgosPacks, setInstallingArgosPacks] = useState(false);
  const [translationAgents, setTranslationAgents] = useState<RepairAgentInfo[]>([]);
  const [translationAgent, setTranslationAgent] = useState<RepairAgentId>('codex');
  const [translationAgentsLoading, setTranslationAgentsLoading] = useState(true);
  const [livePreviewEnabled, setLivePreviewEnabled] = useState(() => {
    try {
      return localStorage.getItem(LIVE_PREVIEW_KEY) === '1';
    } catch {
      return false;
    }
  });
  const lastEngineQuality = useRef<Exclude<typeof session.quality, 'agent'>>(
    session.quality === 'agent' ? 'fast' : session.quality,
  );
  const [incrementalPlan, setIncrementalPlan] = useState<{
    stale: string[];
    fresh: string[];
  } | null>(null);
  const [demoDismissed, setDemoDismissed] = useState(() => {
    try {
      return localStorage.getItem('omnivoice.dubbingDemoDismissed') === '1';
    } catch {
      return false;
    }
  });
  const [dismissedCheckpoints, setDismissedCheckpoints] = useState<Set<string>>(() => new Set());
  const segmentPreviewAbort = useRef<AbortController | null>(null);
  const previewPlayer = useRef<MediaPlayerInstance>(null);
  const previewHandoff = useRef<{ time: number; play: boolean } | null>(null);
  const warmedPreviewUrls = useRef(new Set<string>());
  const [preparingPreviews, setPreparingPreviews] = useState<Set<string>>(() => new Set());
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null);
  const [selectedSegmentIds, setSelectedSegmentIds] = useState<Set<string>>(() => new Set());
  const [expandedSegmentId, setExpandedSegmentId] = useState<string | null>(null);
  const [editingSegmentId, setEditingSegmentId] = useState<string | null>(null);
  const engines = useTranslationEngines();
  const llmSkills = useLlmSkills();
  const modelCatalogue = useModelCatalogue();
  const profiles = useProfiles();
  const navigate = useNavigate();
  const longform = useLongformSession();
  const [storyOpen, setStoryOpen] = useState(false);
  // A dub already knows who says what: diarisation grouped the segments and the
  // Cast strip gave each speaker a voice. Rebuilding that as a Story by hand
  // means retyping every line, so offer it once there are segments to carry.
  const storyFromDub = canCreateStoryFromDub(session);
  // Loading replaces whatever is in Stories, so ask first — but only when there
  // is something to lose.
  const storyOccupied = storiesDraftOccupied(longform.drafts.stories);
  const createStoryFromDub = () => {
    if (!profiles.isSuccess) return;
    const loaded = loadDubIntoStories(session.segments, {
      profiles: profiles.data,
      unknownSpeakerLabel: t('dubWorkspace.storySpeaker'),
    });
    // Nothing was loaded — a render started while the confirm was open, say.
    // Navigating would show the old script and look like the action worked.
    if (loaded) runRendererTask('Create Story from dub', () => navigate({ to: '/stories' }));
  };
  // The current remote Dubbing producer still prepares a local fallback
  // before dispatch, so do not promise remote-only readiness yet.
  const ttsBlocker = useTtsReadiness('dub');
  const llmAgentReady = agentFitSkillsReady(llmSkills.data);
  const availableTranslationAgents = translationAgents.filter((agent) => agent.available);
  const selectedTranslationAgent = availableTranslationAgents.find(
    (agent) => agent.id === translationAgent,
  );
  const busy = !['idle', 'editing', 'done'].includes(session.phase);
  const livePreview = useDubLivePreview({
    enabled: livePreviewEnabled && !busy && !session.recovery,
    language: target,
  });
  const timelineOnsets = useDubOnsets(
    session.jobId,
    session.segments.length > 0 && ['editing', 'done'].includes(session.phase),
  );
  const segmentVirtualizer = useVirtualizer({
    count: session.segments.length,
    getScrollElement: () => mainScroll.current,
    estimateSize: () => 88,
    overscan: 12,
    getItemKey: (index) => session.segments[index]?.id ?? index,
    scrollMargin: segmentScrollMargin,
  });
  useLayoutEffect(() => {
    const list = segmentList.current;
    if (!list) return;
    const measure = () => setSegmentScrollMargin(list.offsetTop);
    measure();
    const observer = new ResizeObserver(measure);
    if (list.parentElement) observer.observe(list.parentElement);
    return () => observer.disconnect();
  }, [session.segments.length]);
  useLayoutEffect(() => {
    requestAnimationFrame(() => segmentVirtualizer.measure());
  }, [editingSegmentId, expandedSegmentId, segmentVirtualizer]);
  const code = LANG_CODES.find((item) => item.label === target)?.code;
  const batchTargets = multiLangTargets(target, code || '', session.multiTargets || []);
  const agentBatchReady = Boolean(
    session.agentCli &&
    batchTargets.length > 1 &&
    batchTargets.every((item) => hasCompleteTranslation(session.segments, item.code)),
  );
  useEffect(() => {
    const repair = getBridge()?.repair;
    if (!repair) {
      setTranslationAgentsLoading(false);
      return;
    }
    void repair
      .list()
      .then((agents) => {
        setTranslationAgents(agents);
        const available = agents.filter((agent) => agent.available);
        let remembered: RepairAgentId | null = null;
        try {
          remembered = (localStorage.getItem(DEFAULT_TRANSLATION_AGENT_KEY) ||
            localStorage.getItem(DEFAULT_REPAIR_AGENT_KEY)) as RepairAgentId | null;
        } catch {
          // Storage can be unavailable in hardened renderer contexts.
        }
        const next = available.find((agent) => agent.id === remembered) || available[0];
        if (next) setTranslationAgent(next.id);
      })
      .catch(() => setTranslationAgents([]))
      .finally(() => setTranslationAgentsLoading(false));
  }, []);
  const switchEditingLanguage = useCallback(
    (next: { lang: string; code: string }) => {
      if (!code || next.code === code) return;
      setDubMultiTargets([
        ...(session.multiTargets || []).filter((item) => item.code !== next.code),
        { lang: target, code },
      ]);
      setTarget(next.lang, next.code);
    },
    [code, session.multiTargets, target],
  );
  useEffect(() => {
    if (session.quality !== 'agent') lastEngineQuality.current = session.quality;
  }, [session.quality]);
  const additionalLanguages = (session.multiTargets || [])
    .filter((item) => item.code !== code)
    .map((item) => item.lang);
  const activeTranslatedCount = code
    ? session.segments.filter((segment) => Boolean(segment.translations?.[code]?.trim())).length
    : 0;
  const provider = engines.data?.engines.find((item) => item.id === engines.data?.active);
  const argosTargets = batchTargets.map((item) => item.code);
  const needsArgosPackCheck = Boolean(
    provider?.id === 'argos' && session.jobId && session.segments.length && argosTargets.length,
  );
  const argosPackRequest = {
    source_lang:
      (session.sourceLang || session.sourceLanguage || '').toLowerCase() === 'auto'
        ? undefined
        : session.sourceLang || session.sourceLanguage || undefined,
    target_langs: argosTargets,
    job_id: session.jobId || undefined,
  };
  const argosPacks = useQuery({
    enabled: needsArgosPackCheck,
    queryKey: [
      'argos-language-packs',
      argosPackRequest.source_lang,
      argosPackRequest.job_id,
      ...argosTargets,
    ],
    queryFn: ({ signal }) =>
      apiJson<ArgosPackStatus>('/engines/translation/argos/packs/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(argosPackRequest),
        signal,
      }),
    staleTime: 30_000,
  });
  const missingArgosPacks = argosPacks.data?.pairs.filter((pair) => !pair.installed) || [];
  const argosPackBlocked = Boolean(
    needsArgosPackCheck && (argosPacks.isPending || argosPacks.isError || missingArgosPacks.length),
  );
  const translationReady = Boolean((provider?.ready ?? provider?.installed) && !argosPackBlocked);
  const hasTranslations = session.segments.some(
    (segment) => segment.translations && Object.keys(segment.translations).length > 0,
  );
  const failedTranslationCount = code
    ? session.segments.filter(
        (segment) =>
          segment.translate_errors?.[code] ||
          (!segment.translate_errors && segment.translate_error),
      ).length
    : 0;
  const canRestoreOriginal = session.segments.some(
    (segment) =>
      Boolean(segment.text_original && segment.text_original !== segment.text) ||
      Boolean(segment.translate_error || segment.translate_degraded || segment.translation_skipped),
  );
  const checkpointStage: CheckpointStage | null =
    session.phase === 'editing'
      ? hasTranslations
        ? 'translate'
        : 'asr'
      : session.phase === 'done'
        ? 'done'
        : null;
  const checkpointId = checkpointStage ? `${session.jobId || 'draft'}:${checkpointStage}` : '';
  const showCheckpoint = Boolean(
    reviewMode === 'on' &&
    checkpointStage &&
    session.segments.length &&
    !dismissedCheckpoints.has(checkpointId),
  );
  const installedAsrModel = modelCatalogue.data?.models.find(
    (model) =>
      model.installed &&
      (model.repo_id.startsWith('Systran/faster-') ||
        model.repo_id === 'deepdml/faster-whisper-large-v3-turbo-ct2'),
  );
  const diarisationModel = modelCatalogue.data?.models.find((model) =>
    ['diarisation', 'diarization'].includes(model.role.toLowerCase()),
  );
  const diarisationReady = Boolean(
    diarisationModel?.installed &&
    diarisationModel.supported !== false &&
    !diarisationModel.incomplete,
  );
  const job = encodeURIComponent(session.jobId || '');
  const previewRevision = useMemo(
    () =>
      preview === 'original' ? '' : fingerprintRevision(session.fingerprintsByLang?.[preview]),
    [preview, session.fingerprintsByLang],
  );
  const warmPreviewPaths = useMemo(
    () =>
      session.inputType === 'video' && session.jobId
        ? session.tracks.map((track) => {
            const revision = fingerprintRevision(session.fingerprintsByLang?.[track]);
            return {
              track,
              path: `/dub/preview-video/${job}?mix=surgical2&lang=${encodeURIComponent(track)}${revision ? `&v=${revision}` : ''}`,
            };
          })
        : [],
    [job, session.fingerprintsByLang, session.inputType, session.jobId, session.tracks],
  );
  const source = useMemo(
    () =>
      session.inputType === 'audio'
        ? apiPath(
            preview === 'original'
              ? `/dub/audio/${job}`
              : `/dub/download-audio/${job}?lang=${encodeURIComponent(preview)}`,
          )
        : apiPath(
            preview === 'original'
              ? `/dub/media/${job}`
              : `/dub/preview-video/${job}?mix=surgical2&lang=${encodeURIComponent(preview)}${previewRevision ? `&v=${previewRevision}` : ''}`,
          ),
    [job, preview, previewRevision, session.inputType],
  );
  // Vidstack treats a new object identity as a source update. Keep this
  // stable while unrelated transcript/progress state changes so the native
  // provider does not re-open a multi-hundred-megabyte video.
  const previewVideoSource = useMemo(
    () =>
      preview === 'original'
        ? nativeVideoSource(source, session.filename, 'video/mp4')
        : { src: source, type: 'video/mp4' as const },
    [preview, session.filename, source],
  );
  const switchPreview = useCallback((next: string) => {
    const player = previewPlayer.current;
    previewHandoff.current = player
      ? {
          time: Number.isFinite(player.currentTime) ? player.currentTime : 0,
          play: !player.paused,
        }
      : null;
    setPreview(next);
  }, []);
  const restorePreviewPosition = useCallback(() => {
    const player = previewPlayer.current;
    const handoff = previewHandoff.current;
    if (!player || !handoff) return;
    previewHandoff.current = null;
    const duration = Number.isFinite(player.duration) ? player.duration : handoff.time;
    player.currentTime = Math.max(0, Math.min(handoff.time, duration));
    if (handoff.play) void player.play().catch(() => {});
  }, []);
  const stage = session.agentPass
    ? session.agentPass.stage === 'measuring'
      ? 'dub.agent_measuring'
      : session.agentPass.stage === 'adapting'
        ? 'dub.agent_adapting'
        : 'dub.agent_rerendering'
    : session.phase === 'preparing'
      ? preparationStageKey(session.event?.type)
      : session.phase === 'transcribing'
        ? 'dub_workflow.transcribing_audio'
        : session.phase === 'translating'
          ? 'dub.translating'
          : session.phase === 'cleaning'
            ? 'dub.clean_up'
            : session.phase === 'generating'
              ? 'dub_workflow.generating_dub'
              : 'common.loading';
  const eventCurrent = Number(session.event?.current);
  const eventTotal = Number(session.event?.total);
  const progressPercent =
    typeof session.event?.percent === 'number'
      ? session.event.percent
      : Number.isFinite(eventCurrent) && Number.isFinite(eventTotal) && eventTotal > 0
        ? (eventCurrent / eventTotal) * 100
        : null;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.isComposing ||
        event.repeat ||
        !(event.ctrlKey || event.metaKey) ||
        busy ||
        session.recovery
      )
        return;
      const key = event.key.toLowerCase();
      if (key === 'z') {
        event.preventDefault();
        if (event.shiftKey) redoDubEdit();
        else undoDubEdit();
        return;
      }
      const field = event.target;
      if (!(field instanceof HTMLTextAreaElement)) return;
      const id = field.dataset.segmentId;
      if (!id) return;
      if (key === 'd') {
        event.preventDefault();
        splitDubSegment(id, field.selectionStart);
      } else if (key === 'm') {
        event.preventDefault();
        mergeDubSegment(id, event.shiftKey ? 'prev' : 'next');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [busy, session.recovery]);
  useEffect(() => {
    if (selectedSegmentId && !session.segments.some((segment) => segment.id === selectedSegmentId))
      setSelectedSegmentId(null);
  }, [selectedSegmentId, session.segments]);
  useEffect(() => {
    setSelectedSegmentIds(
      (current) =>
        new Set([...current].filter((id) => session.segments.some((segment) => segment.id === id))),
    );
  }, [session.segments]);
  useEffect(() => {
    if (session.phase !== 'done' || !code || !session.fingerprintsByLang?.[code]) {
      setIncrementalPlan(null);
      return;
    }
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      void planDubIncremental(code, controller.signal)
        .then(setIncrementalPlan)
        .catch((error) => {
          if (!isAbortError(error)) console.warn('Could not plan incremental dub:', error);
        });
    }, 250);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [code, session.fingerprintsByLang, session.phase, session.segments, session.voiceMatch]);
  useEffect(() => () => segmentPreviewAbort.current?.abort(), []);
  useEffect(() => () => revokeObjectUrl(segmentPreview?.url), [segmentPreview?.url]);
  useEffect(() => {
    if (!warmPreviewPaths.length) return;
    let disposed = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        // Build one preview at a time: this hides first-open latency without
        // launching several ffmpeg jobs that compete for the same disk.
        for (const item of warmPreviewPaths) {
          if (disposed || warmedPreviewUrls.current.has(item.path)) continue;
          warmedPreviewUrls.current.add(item.path);
          setPreparingPreviews((current) => new Set(current).add(item.track));
          try {
            await apiFetch(item.path, { headers: { Range: 'bytes=0-0' } });
          } catch {
            warmedPreviewUrls.current.delete(item.path);
          } finally {
            if (!disposed)
              setPreparingPreviews((current) => {
                const next = new Set(current);
                next.delete(item.track);
                return next;
              });
          }
        }
      })();
    }, 250);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [warmPreviewPaths]);

  const removeVideo = () => {
    if (busy || cancelling || session.recovery || !resetDubSession()) return;
    livePreview.stop();
    segmentPreviewAbort.current?.abort();
    segmentPreviewAbort.current = null;
    setSegmentPreview(null);
    setPreviewingSegmentId(null);
    setPreview('original');
    setUrl('');
    setCookieFile(undefined);
    setCookieError(false);
  };

  const previewDubSegment = async (segment: (typeof session.segments)[number]) => {
    if (!session.jobId || previewingSegmentId) return;
    segmentPreviewAbort.current?.abort();
    const controller = new AbortController();
    const finishActivity = beginAppActivity('synthesis');
    segmentPreviewAbort.current = controller;
    setPreviewingSegmentId(segment.id);
    requestPlaybackSeek('dub-preview', segment.start);
    try {
      const inputs = segmentGenInputs(segment);
      const response = await apiFetch('/dub/preview-segment/' + encodeURIComponent(session.jobId), {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          segment_id: segment.id,
          text: inputs.text,
          language: inputs.target_lang || code || target,
          instruct: inputs.instruct || session.instruct || undefined,
          profile_id: inputs.profile_id || undefined,
          speed: inputs.speed || session.speed || 1,
          duration:
            (session.timingStrategy || 'strict_slot') === 'strict_slot'
              ? Math.max(0.3, segment.end - segment.start)
              : undefined,
        }),
      });
      const url = createObjectUrl(await response.blob());
      if (!url) throw new Error(t('player.unavailable'));
      if (controller.signal.aborted) {
        revokeObjectUrl(url);
        return;
      }
      setSegmentPreview({ id: segment.id, url });
    } catch (error) {
      if (!isAbortError(error))
        toast.error(t('tts_errors.error_prefix', { message: describeError(error) }));
    } finally {
      finishActivity();
      if (segmentPreviewAbort.current === controller) {
        segmentPreviewAbort.current = null;
        setPreviewingSegmentId(null);
      }
    }
  };
  const verifyDub = async () => {
    if (!session.jobId || qcRunning || !session.tracks.length) return;
    const language = preview === 'original' ? session.tracks[0] : preview;
    const revision = JSON.stringify(
      session.segments.map((segment) => [segment.id, segment.text, segment.start, segment.end]),
    );
    setQcRunning(true);
    const finishActivity = beginAppActivity('transcription');
    if (preview === 'original') setPreview(language);
    try {
      const result = await apiJson<{
        total: number;
        flagged_count: number;
        segments: Array<{
          seg_id: string;
          drift: number;
          flagged: boolean;
          recognized_text: string;
          measured_start?: number | null;
          measured_end?: number | null;
        }>;
      }>(`/dub/qc/${encodeURIComponent(session.jobId)}?lang=${encodeURIComponent(language)}`, {
        method: 'POST',
      });
      const currentRevision = JSON.stringify(
        dubSession.state.segments.map((segment) => [
          segment.id,
          segment.text,
          segment.start,
          segment.end,
        ]),
      );
      if (revision !== currentRevision) return;
      applyDubQc(result.segments);
      if (result.flagged_count)
        toast.warning(
          t('dub.qc_result', {
            flagged: result.flagged_count,
            total: result.total,
          }),
        );
      else toast.success(t('dub.qc_clean', { total: result.total }));
    } catch (error) {
      toast.error(t('dub.qc_failed', { message: describeError(error) }));
    } finally {
      finishActivity();
      setQcRunning(false);
    }
  };
  const translateTargets = async (forceAgent = false) => {
    const agentMode = forceAgent || session.quality === 'agent';
    if (agentMode && selectedTranslationAgent && code) {
      const skipped =
        batchTargets.length <= 1
          ? (await translateDubWithAgent(code, selectedTranslationAgent.id, target))
            ? []
            : [target]
          : await translateDubBatchWithAgent(batchTargets, selectedTranslationAgent.id);
      if (skipped.length) toast.error(t('dub.multi_lang_skipped', { langs: skipped.join(', ') }));
      else
        toast.success(
          t('dub.agent_translation_complete', { agent: selectedTranslationAgent.label }),
        );
      return;
    }
    if (!provider || !translationReady || !code) return;
    if (agentMode && !llmAgentReady) return;
    if (forceAgent) setDubQuality('agent');
    if (batchTargets.length <= 1) {
      await translateDub(code, provider.id);
      return;
    }
    const skipped = await translateDubBatch(batchTargets, provider.id);
    if (skipped.length) toast.error(t('dub.multi_lang_skipped', { langs: skipped.join(', ') }));
  };
  const installArgosLanguagePacks = async () => {
    if (!missingArgosPacks.length || installingArgosPacks) return;
    setInstallingArgosPacks(true);
    try {
      await apiJson<ArgosPackStatus>('/engines/translation/argos/packs/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(argosPackRequest),
      });
      await argosPacks.refetch();
    } catch (error) {
      toast.error(describeError(error));
    } finally {
      setInstallingArgosPacks(false);
    }
  };
  const generateTargets = async (regenOnly?: string[]) => {
    if (!code || ttsBlocker !== null) return;
    if (batchTargets.length <= 1) {
      await generateDub(target, code, {
        regenOnly,
        preview: Boolean(regenOnly?.length),
      });
      return;
    }
    if ((!provider || !translationReady) && !agentBatchReady) return;
    const skipped = await generateDubBatch(batchTargets, provider?.id);
    if (skipped.length) toast.error(t('dub.multi_lang_skipped', { langs: skipped.join(', ') }));
  };

  const splitAtCursor = (id: string, text: string) => {
    const remembered = cursors.current.get(id);
    splitDubSegment(
      id,
      remembered && remembered > 0 && remembered < text.length ? remembered : bestSplitPoint(text),
    );
  };
  const errorMessage = session.asrModelMissing
    ? null
    : session.error === DUB_AGENT_UNAVAILABLE
      ? t('dub_workflow.cinematic_no_llm')
      : session.error === DUB_STOP_FAILED || session.error === 'Cancellation could not be confirmed'
        ? t('dub_workflow.stop_failed')
        : session.error;
  const recoverWithInstalledAsr = async () => {
    if (!installedAsrModel || recoveringAsr) return;
    setRecoveringAsr(true);
    try {
      await apiJson('/engines/select', {
        method: 'POST',
        body: JSON.stringify({
          family: 'asr',
          backend_id: 'faster-whisper',
          model_id: installedAsrModel.repo_id,
        }),
      });
      await resumeDub();
    } catch (error) {
      toast.error(describeError(error));
    } finally {
      setRecoveringAsr(false);
    }
  };
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('dubWorkspace.title')}</h1>
        <Link to="/projects" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>
          {t('projects.title')}
        </Link>
        <span
          className="min-w-0 flex-1 truncate text-xs text-muted-foreground"
          title={session.filename}
        >
          {compactSourceLabel(session.filename)}
        </span>
        {storyFromDub && (
          <Button
            variant="ghost"
            size="sm"
            // editLongform is a no-op while a longform render is running, so the
            // action would navigate to Stories having loaded nothing.
            disabled={Boolean(longform.active) || !profiles.isSuccess}
            title={
              longform.active
                ? t('dubWorkspace.storyBusy')
                : !profiles.isSuccess
                  ? t(profiles.isError ? 'common.error' : 'common.loading')
                  : undefined
            }
            onClick={() => (storyOccupied ? setStoryOpen(true) : createStoryFromDub())}
          >
            <AudioLinesIcon />
            {t('dubWorkspace.createStory')}
          </Button>
        )}
      </WorkspaceHeader>
      <ConfirmDialog
        open={storyOpen}
        onOpenChange={setStoryOpen}
        title={t('dubWorkspace.createStory')}
        description={t('dubWorkspace.createStoryConfirm')}
        confirmLabel={t('dubWorkspace.createStory')}
        destructive
        onConfirm={createStoryFromDub}
      />
      <div className="flex min-h-0 flex-1 @max-[40rem]:flex-col">
        <SecondarySidebar
          title={t('dubWorkspace.title')}
          icon={FilmIcon}
          size="spacious"
          variant="controls"
          className="flex flex-col gap-2"
        >
          <section
            className={cn(
              'space-y-3 rounded-xl border border-border/60 bg-muted/15 p-3 transition-colors',
              session.jobId && !['idle', 'preparing'].includes(session.phase) && 'rounded-b-md',
              dragging && 'border-primary/60 bg-primary/5',
            )}
            onDragEnter={(event) => {
              event.preventDefault();
              if (!busy && !session.recovery) setDragging(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null))
                setDragging(false);
            }}
            onDrop={(event: DragEvent<HTMLElement>) => {
              event.preventDefault();
              setDragging(false);
              const file = event.dataTransfer.files[0];
              if (file && !busy && !session.recovery) {
                setPreview('original');
                void uploadDub(file);
              }
            }}
          >
            <h2 className="flex items-center gap-2 text-sm font-medium">
              <span className="grid size-5 place-items-center rounded-full bg-primary/12 text-[11px] font-semibold text-primary">
                1
              </span>
              {t('dub.upload_transcribe')}
            </h2>
            <input
              ref={input}
              type="file"
              accept="audio/*,video/*,.mkv,.mov,.mp4,.wav,.mp3"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) {
                  setPreview('original');
                  void uploadDub(file);
                }
              }}
            />
            {!session.jobId ? (
              <>
                <Button
                  className="w-full"
                  disabled={busy || Boolean(session.recovery)}
                  onClick={() => input.current?.click()}
                >
                  <UploadIcon />
                  {t('settings.audio_tools_choose_file')}
                </Button>
                <p className="text-center text-xs text-muted-foreground">{t('dub.drop_here')}</p>
                <form
                  className="space-y-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!busy && !session.recovery && isDubUrl(url)) {
                      setPreview('original');
                      void ingestDubUrl(url, cookieFile, fetchSubs);
                      setCookieFile(undefined);
                    }
                  }}
                >
                  <div className="flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <Input
                        type="url"
                        value={url}
                        onChange={(event) => setUrl(event.target.value)}
                        aria-label={t('dub.paste_url')}
                        placeholder={t('dub.paste_url')}
                        disabled={busy || Boolean(session.recovery)}
                      />
                    </div>
                    {url && (
                      <Button
                        type="button"
                        size="xs"
                        variant="ghost"
                        aria-label={t('common.clear')}
                        disabled={busy || Boolean(session.recovery)}
                        onClick={() => {
                          setUrl('');
                          setCookieFile(undefined);
                          setCookieError(false);
                        }}
                      >
                        {t('common.clear')}
                      </Button>
                    )}
                  </div>
                  {url && (
                    <details className="space-y-2">
                      <summary className="cursor-pointer text-xs text-muted-foreground">
                        {t('dub.advanced')}
                      </summary>
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span>{t('dub.pull_captions')}</span>
                        <Switch
                          aria-label={t('dub.pull_captions')}
                          checked={fetchSubs}
                          onCheckedChange={setFetchSubs}
                          disabled={busy || Boolean(session.recovery)}
                        />
                      </div>
                      <p className="text-xs text-muted-foreground">{t('dub.youtube_auth')}</p>
                      <Input
                        type="file"
                        accept=".txt,text/plain"
                        aria-label={t('dub.youtube_cookie_file')}
                        disabled={busy || Boolean(session.recovery)}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          event.target.value = '';
                          setCookieError(Boolean(file && file.size > MAX_COOKIE_EXPORT_BYTES));
                          setCookieFile(
                            file && file.size <= MAX_COOKIE_EXPORT_BYTES ? file : undefined,
                          );
                        }}
                      />
                      {cookieFile && (
                        <div className="flex items-center gap-2">
                          <span className="truncate text-xs">{cookieFile.name}</span>
                          <Button
                            type="button"
                            size="xs"
                            variant="ghost"
                            aria-label={t('dub.remove_cookie_file')}
                            onClick={() => setCookieFile(undefined)}
                          >
                            {t('common.cancel')}
                          </Button>
                        </div>
                      )}
                      {cookieError && (
                        <p role="alert" className="text-xs text-destructive">
                          {t('dub.cookie_size_error')}
                        </p>
                      )}
                    </details>
                  )}
                  {url && (
                    <Button
                      type="submit"
                      variant="outline"
                      className="w-full"
                      disabled={busy || Boolean(session.recovery) || !isDubUrl(url)}
                    >
                      {t('dub.ingest')}
                    </Button>
                  )}
                </form>
              </>
            ) : (
              <div className="flex min-w-0 items-center gap-2 rounded-lg bg-background/35 px-2.5 py-2">
                <FilmIcon className="size-4 shrink-0 text-primary" />
                <span className="min-w-0 flex-1 truncate text-xs" title={session.filename}>
                  {compactSourceLabel(session.filename)}
                </span>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={busy || Boolean(session.recovery)}
                  onClick={() => input.current?.click()}
                >
                  {t('dub.change_file')}
                </Button>
                <ConfirmDialog
                  open={removeVideoOpen}
                  onOpenChange={setRemoveVideoOpen}
                  title={t('dub.remove_video')}
                  description={t('dub.remove_video_confirm')}
                  confirmLabel={t('dub.remove_video')}
                  onConfirm={removeVideo}
                />
                <Button
                  size="xs"
                  variant="ghost"
                  aria-label={t('dub.remove_video')}
                  disabled={busy || cancelling || Boolean(session.recovery)}
                  onClick={() => {
                    if (session.segments.length > 0 || editHistory.undoDepth > 0)
                      setRemoveVideoOpen(true);
                    else removeVideo();
                  }}
                >
                  {t('dub.remove_video')}
                </Button>
              </div>
            )}
            <details className="group space-y-3 border-t border-border/50 pt-2">
              <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium text-muted-foreground">
                <UsersRoundIcon className="size-3.5" />
                {t('dub.source_language')}
                <ChevronDownIcon className="ml-auto size-3.5 transition-transform group-open:rotate-180" />
              </summary>
              <LanguagePicker
                value={
                  LANG_CODES.find((item) => item.code === session.sourceLanguage)?.label ||
                  t('dub.num_speakers_auto')
                }
                options={[t('dub.num_speakers_auto'), ...targetOptions]}
                disabled={busy || Boolean(session.recovery)}
                onValueChange={(value) =>
                  setDubProduction({
                    sourceLanguage: LANG_CODES.find((item) => item.label === value)?.code,
                  })
                }
              />
              <label className="block space-y-2 text-xs">
                <span>{t('dub.num_speakers_label')}</span>
                <Input
                  type="number"
                  min={1}
                  max={20}
                  step={1}
                  placeholder={t('dub.num_speakers_auto')}
                  value={session.numSpeakers ?? ''}
                  disabled={busy || Boolean(session.recovery)}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    setDubProduction({
                      numSpeakers:
                        Number.isInteger(value) && value >= 1 && value <= 20 ? value : undefined,
                    });
                  }}
                />
              </label>
              <p className="text-xs leading-5 text-muted-foreground">
                {t('dub.num_speakers_help')}
              </p>
              {modelCatalogue.isSuccess && (
                <div
                  role="status"
                  className="flex items-center gap-2 rounded-lg border border-border/60 bg-background/35 px-2.5 py-2 text-xs"
                >
                  <UsersRoundIcon
                    className={cn(
                      'size-3.5 shrink-0',
                      diarisationReady ? 'text-emerald-500' : 'text-muted-foreground',
                    )}
                  />
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {t('engineSidebar.diarisation')} ·{' '}
                    {t(
                      diarisationReady ? 'modelMaintenance.installed' : 'modelSettings.unavailable',
                    )}
                  </span>
                  {!diarisationReady && (
                    <Link
                      to="/settings/models/$family"
                      params={{ family: 'diarisation' }}
                      className={buttonVariants({
                        variant: 'outline',
                        size: 'xs',
                      })}
                    >
                      <Settings2Icon />
                      {t('modelMaintenance.install')}
                    </Link>
                  )}
                </div>
              )}
            </details>
            <input
              ref={subtitles}
              type="file"
              accept=".srt,application/x-subrip"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) {
                  setPreview('original');
                  void importDubSubtitles(file);
                }
              }}
            />
            {session.jobId && (
              <Button
                variant="ghost"
                disabled={busy || Boolean(session.recovery)}
                onClick={() => subtitles.current?.click()}
              >
                <UploadIcon />
                {t('dub.import_srt')}
              </Button>
            )}
            {session.subtitleImport && (
              <p role="status" className="text-xs leading-5 text-muted-foreground">
                {t('dub_workflow.imported_cues', {
                  count: session.subtitleImport.stats.imported,
                  file: session.subtitleImport.file,
                })}
                {(['skipped_malformed', 'dropped_overlap', 'clamped_to_duration'] as const).map(
                  (key) =>
                    session.subtitleImport!.stats[key] ? (
                      <span key={key} className="block">
                        {t('dub_workflow.' + key, {
                          count: session.subtitleImport!.stats[key],
                        })}
                      </span>
                    ) : null,
                )}
              </p>
            )}
          </section>
          {session.jobId && !['idle', 'preparing'].includes(session.phase) && (
            <section className="-mt-1 space-y-3 rounded-xl rounded-t-md border border-border/60 bg-muted/15 p-3">
              <h2 className="flex items-center gap-2 text-sm font-medium">
                <PlayIcon className="size-4 text-muted-foreground" />
                {t('dub.preview')}
              </h2>
              {session.inputType === 'audio' ? (
                <WaveformPlayer
                  src={source}
                  source="dub-preview"
                  compact
                  showWaveform={false}
                  playerRef={previewPlayer}
                  onCanPlay={restorePreviewPosition}
                />
              ) : (
                <VideoPlayer
                  src={previewVideoSource}
                  source="dub-preview"
                  load="visible"
                  poster={apiPath(`/dub/thumb/${job}`)}
                  playerRef={previewPlayer}
                  onCanPlay={restorePreviewPosition}
                />
              )}
              <div className="flex flex-wrap gap-1 rounded-lg bg-background/35 p-1">
                <Button
                  size="xs"
                  className="min-w-0 flex-1"
                  variant={preview === 'original' ? 'secondary' : 'ghost'}
                  aria-pressed={preview === 'original'}
                  onClick={() => switchPreview('original')}
                >
                  {t('dub.original_audio')}
                </Button>
                {session.tracks.map((track) => (
                  <Button
                    key={track}
                    size="xs"
                    className="min-w-0 flex-1"
                    variant={preview === track ? 'secondary' : 'ghost'}
                    aria-pressed={preview === track}
                    onClick={() => switchPreview(track)}
                  >
                    {preparingPreviews.has(track) && (
                      <LoaderCircleIcon
                        className="animate-spin motion-reduce:animate-none"
                        aria-label={t('common.loading')}
                      />
                    )}
                    {t('dub.dubbed_audio', { code: track })}
                  </Button>
                ))}
              </div>
              {segmentPreview && (
                <div className="space-y-2 border-t border-border/50 pt-3">
                  <div className="flex items-center gap-2 text-xs font-medium">
                    <HeadphonesIcon className="size-3.5 text-primary" />
                    <span className="mr-auto">{t('dub.live_preview')}</span>
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      aria-label={t('common.close')}
                      onClick={() => setSegmentPreview(null)}
                    >
                      <XIcon />
                    </Button>
                  </div>
                  <WaveformPlayer
                    key={segmentPreview.url}
                    src={segmentPreview.url}
                    source="dub-segment-preview"
                    compact
                  />
                </div>
              )}
            </section>
          )}
          <section className="space-y-3 rounded-xl border border-border/60 bg-muted/15 p-3">
            <h2 className="flex items-center gap-2 text-sm font-medium">
              <span className="grid size-5 place-items-center rounded-full bg-primary/12 text-[11px] font-semibold text-primary">
                2
              </span>
              {t('dub.target_language')}
            </h2>
            <LanguagePicker
              value={target}
              onValueChange={(language) => {
                const selectedCode = LANG_CODES.find((item) => item.label === language)?.code;
                setTarget(language, selectedCode);
                if (!dialectMatchesLang(session.dialect, selectedCode))
                  setDubTranslationOptions({ dialect: undefined });
                if (selectedCode)
                  setDubMultiTargets(
                    (session.multiTargets || []).filter((item) => item.code !== selectedCode),
                  );
              }}
              options={targetOptions}
              disabled={busy || Boolean(session.recovery)}
            />
            <MultiLanguagePicker
              selected={additionalLanguages}
              options={targetOptions.filter((language) => language !== target)}
              disabled={busy || Boolean(session.recovery)}
              onChange={(languages) =>
                setDubMultiTargets(
                  languages.flatMap((language) => {
                    const item = LANG_CODES.find((candidate) => candidate.label === language);
                    return item ? [{ lang: item.label, code: item.code }] : [];
                  }),
                )
              }
            />
            {dialectOptionsFor(code).length > 0 && (
              <details className="group space-y-2 border-t border-border/50 pt-2">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium text-muted-foreground">
                  <LanguagesIcon className="size-3.5" />
                  {t('dub.dialect_label')}
                  <ChevronDownIcon className="ml-auto size-3.5 transition-transform group-open:rotate-180" />
                </summary>
                <p className="text-xs leading-5 text-muted-foreground">{t('dub.dialect_title')}</p>
                <div className="flex flex-wrap gap-1">
                  <Button
                    size="xs"
                    variant={!session.dialect ? 'secondary' : 'ghost'}
                    disabled={busy || Boolean(session.recovery)}
                    onClick={() => setDubTranslationOptions({ dialect: undefined })}
                  >
                    {t('dub.dialect_default')}
                  </Button>
                  {dialectOptionsFor(code).map((dialect) => (
                    <Button
                      key={dialect}
                      size="xs"
                      variant={session.dialect === dialect ? 'secondary' : 'ghost'}
                      disabled={busy || Boolean(session.recovery)}
                      onClick={() => setDubTranslationOptions({ dialect })}
                    >
                      {dialectLabel(dialect, i18n.language)}
                    </Button>
                  ))}
                </div>
              </details>
            )}
            <div className="space-y-2 border-t border-border/50 pt-2">
              <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <LanguagesIcon className="size-3.5" />
                {t('about.translator')}
              </p>
              <div
                role="group"
                aria-label={t('about.translator')}
                className="grid grid-cols-2 gap-1 rounded-lg bg-muted/30 p-1"
              >
                <Button
                  size="sm"
                  variant={session.quality === 'agent' ? 'secondary' : 'ghost'}
                  aria-pressed={session.quality === 'agent'}
                  disabled={
                    busy ||
                    Boolean(session.recovery) ||
                    (!selectedTranslationAgent && !llmAgentReady)
                  }
                  className="h-auto min-w-0 justify-start px-2.5 py-2"
                  title={
                    selectedTranslationAgent
                      ? t('dub.agent_cli_desc', { agent: selectedTranslationAgent.label })
                      : t('dub.agent_quality_desc')
                  }
                  onClick={() => setDubQuality('agent')}
                >
                  <WandSparklesIcon className="shrink-0" />
                  <span className="min-w-0 truncate">{t('dub.translate_with_agent')}</span>
                </Button>
                <Button
                  size="sm"
                  variant={session.quality !== 'agent' ? 'secondary' : 'ghost'}
                  aria-pressed={session.quality !== 'agent'}
                  disabled={busy || Boolean(session.recovery) || !provider}
                  className="h-auto min-w-0 justify-start px-2.5 py-2"
                  title={provider?.display_name}
                  onClick={() => setDubQuality(lastEngineQuality.current)}
                >
                  <LanguagesIcon className="shrink-0" />
                  <span className="min-w-0 truncate">
                    {provider?.display_name || t('engineSidebar.translation')}
                  </span>
                </Button>
              </div>
              {session.quality === 'agent' && (
                <p className="px-1 text-xs leading-5 text-muted-foreground">
                  {selectedTranslationAgent?.label
                    ? t('dub.agent_cli_desc', { agent: selectedTranslationAgent.label })
                    : t('dub.agent_quality_desc')}
                </p>
              )}
            </div>
            {session.quality === 'agent' && (
              <div className="space-y-2 border-t border-border/50 pt-3">
                <label htmlFor="dub-translation-instructions" className="text-xs font-medium">
                  {t('dubStyle.label')}
                </label>
                <textarea
                  id="dub-translation-instructions"
                  aria-describedby="dub-translation-instructions-help"
                  rows={4}
                  maxLength={5000}
                  value={session.translationInstructions || ''}
                  disabled={busy || Boolean(session.recovery)}
                  onChange={(event) =>
                    setDubTranslationOptions({ translationInstructions: event.target.value })
                  }
                  className="w-full resize-y rounded-lg border border-input bg-background/40 px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                />
                <p
                  id="dub-translation-instructions-help"
                  className="text-xs leading-5 text-muted-foreground"
                >
                  {t('dubStyle.help')}
                </p>
              </div>
            )}
            {session.quality !== 'agent' && (
              <details className="group space-y-2 border-t border-border/50 pt-2">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium text-muted-foreground">
                  <GaugeIcon className="size-3.5" />
                  {t('settings.translate_quality')}
                  <ChevronDownIcon className="ml-auto size-3.5 transition-transform group-open:rotate-180" />
                </summary>
                <p className="text-xs text-muted-foreground">
                  {t('settings.translate_quality_desc')}
                </p>
                <div className="flex flex-wrap gap-1">
                  {(['fast', 'autofit', 'cinematic'] as const).map((quality) => (
                    <Button
                      key={quality}
                      size="sm"
                      variant={session.quality === quality ? 'secondary' : 'ghost'}
                      aria-pressed={session.quality === quality}
                      disabled={busy || Boolean(session.recovery)}
                      onClick={() => setDubQuality(quality)}
                    >
                      {t('settings.translate_' + quality)}
                    </Button>
                  ))}
                </div>
                <div className="space-y-2 border-t border-border/50 pt-2">
                  {provider?.id === 'openai' && (
                    <>
                      <label className="flex items-start justify-between gap-3 text-xs">
                        <span>
                          <span className="block font-medium">{t('dub.auto_glossary_label')}</span>
                          <span className="mt-0.5 block leading-5 text-muted-foreground">
                            {t('dub.auto_glossary_title')}
                          </span>
                        </span>
                        <Switch
                          checked={session.autoGlossary ?? true}
                          disabled={busy || Boolean(session.recovery)}
                          onCheckedChange={(autoGlossary) =>
                            setDubTranslationOptions({ autoGlossary })
                          }
                        />
                      </label>
                      <label className="flex items-start justify-between gap-3 text-xs">
                        <span>
                          <span className="block font-medium">{t('dub.reflect_label')}</span>
                          <span className="mt-0.5 block leading-5 text-muted-foreground">
                            {t('dub.reflect_title')}
                          </span>
                        </span>
                        <Switch
                          checked={session.reflectPass ?? true}
                          disabled={busy || Boolean(session.recovery)}
                          onCheckedChange={(reflectPass) =>
                            setDubTranslationOptions({ reflectPass })
                          }
                        />
                      </label>
                    </>
                  )}
                  <label className="flex items-start justify-between gap-3 text-xs">
                    <span>
                      <span className="block font-medium">{t('dub.condense_label')}</span>
                      <span className="mt-0.5 block leading-5 text-muted-foreground">
                        {t('dub.condense_title')}
                      </span>
                    </span>
                    <Switch
                      checked={session.condenseSuggest ?? false}
                      disabled={busy || Boolean(session.recovery)}
                      onCheckedChange={(condenseSuggest) =>
                        setDubTranslationOptions({ condenseSuggest })
                      }
                    />
                  </label>
                </div>
              </details>
            )}
            {session.translationFallback && (
              <div role="status" className="space-y-2 text-xs text-muted-foreground">
                <p>{t('dub_workflow.cinematic_no_llm')}</p>
                <Link to="/settings/models/$family" params={{ family: 'llm' }}>
                  {t('engineSidebar.llm')}
                </Link>
              </div>
            )}
            <Link
              to="/settings/models/$family"
              params={{ family: 'translation' }}
              className="flex items-center gap-2 text-sm text-muted-foreground"
            >
              <Settings2Icon className="size-4" />
              <span>{provider?.display_name || t('engineSidebar.translation')}</span>
            </Link>
            {provider && (
              <p className="text-xs text-muted-foreground">
                {t('modelMaintenance.' + provider.category)}
              </p>
            )}
            {provider?.id === 'argos' && argosPacks.isError && (
              <PipelineFailure
                className="text-xs"
                fallback={describeError(argosPacks.error)}
                action={
                  <Button size="xs" variant="ghost" onClick={() => void argosPacks.refetch()}>
                    <RotateCcwIcon />
                    {t('backend.retry')}
                  </Button>
                }
              />
            )}
            {provider?.id === 'argos' && missingArgosPacks.length > 0 && (
              <PipelineFailure
                className="text-xs"
                fallback={`${t('modelMaintenance.install')} · ${provider.display_name} · ${missingArgosPacks
                  .map((pair) => `${pair.source_lang} → ${pair.target_lang}`)
                  .join(', ')}`}
                action={
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={installingArgosPacks}
                    onClick={() => void installArgosLanguagePacks()}
                  >
                    {installingArgosPacks ? (
                      <LoaderCircleIcon className="animate-spin" />
                    ) : (
                      <LanguagesIcon />
                    )}
                    {t(installingArgosPacks ? 'common.loading' : 'modelMaintenance.install')}
                  </Button>
                }
              />
            )}
            {provider && !(provider.ready ?? provider.installed) && (
              <PipelineFailure
                className="text-xs"
                fallback={provider.availability_reason || t('modelSettings.unavailable')}
              />
            )}
          </section>
          <CastingBoard
            segments={session.segments}
            profiles={profiles.data || []}
            disabled={busy || Boolean(session.recovery)}
            onAssign={assignDubSpeakerProfile}
          />
          <details className="group rounded-xl border border-border/60 bg-muted/20 p-3">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium">
              <Clock3Icon className="size-4 text-muted-foreground" />
              {t('dub.timing_label')}
              <ChevronDownIcon className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-3 space-y-3">
              <div className="flex flex-wrap gap-1">
                {(['concise', 'smart_fit', 'stretch_video', 'strict_slot'] as const).map(
                  (value) => (
                    <Button
                      key={value}
                      size="xs"
                      disabled={busy || Boolean(session.recovery)}
                      aria-pressed={(session.timingStrategy || 'strict_slot') === value}
                      variant={
                        (session.timingStrategy || 'strict_slot') === value ? 'secondary' : 'ghost'
                      }
                      onClick={() => setDubProduction({ timingStrategy: value })}
                    >
                      {t('dub.timing_' + (value === 'strict_slot' ? 'lip_sync' : value))}
                    </Button>
                  ),
                )}
              </div>
              {['smart_fit', 'stretch_video'].includes(session.timingStrategy || '') && (
                <p className="text-xs text-muted-foreground">{t('exportModal.retime_note')}</p>
              )}
              <p className="text-xs font-medium text-muted-foreground">{t('dub.voice_match')}</p>
              <p className="text-xs text-muted-foreground">{t('dub.voice_match_title')}</p>
              <div className="flex flex-wrap gap-1">
                {(['per_line', 'consistent'] as const).map((value) => (
                  <Button
                    key={value}
                    size="xs"
                    disabled={busy || Boolean(session.recovery)}
                    aria-pressed={(session.voiceMatch || 'per_line') === value}
                    variant={(session.voiceMatch || 'per_line') === value ? 'secondary' : 'ghost'}
                    onClick={() => setDubProduction({ voiceMatch: value })}
                  >
                    {t('dub.voice_match_' + value)}
                  </Button>
                ))}
              </div>
            </div>
          </details>
          <details className="group rounded-xl border border-border/60 bg-muted/20 p-3">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium">
              <SlidersHorizontalIcon className="size-4 text-muted-foreground" />
              {t('clone.production_overrides')}
              <ChevronDownIcon className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-3 space-y-3">
              {(
                [
                  {
                    key: 'steps',
                    label: 'steps',
                    min: 8,
                    max: 64,
                    step: 1,
                    defaultValue: 16,
                  },
                  {
                    key: 'guidance',
                    label: 'cfg',
                    min: 0,
                    max: 4,
                    step: 0.1,
                    defaultValue: 2,
                  },
                  {
                    key: 'speed',
                    label: 'speed',
                    min: 0.5,
                    max: 2,
                    step: 0.05,
                    defaultValue: 1,
                  },
                ] as const
              ).map((knob) => (
                <label key={knob.key} className="block space-y-2 text-xs">
                  <span className="flex justify-between">
                    <span>{t('clone.' + knob.label)}</span>
                    <span className="tabular-nums">{session[knob.key] ?? knob.defaultValue}</span>
                  </span>
                  <input
                    type="range"
                    className="w-full accent-primary"
                    aria-label={t('clone.' + knob.label)}
                    min={knob.min}
                    max={knob.max}
                    step={knob.step}
                    value={session[knob.key] ?? knob.defaultValue}
                    disabled={busy || Boolean(session.recovery)}
                    onChange={(event) =>
                      setDubProduction({
                        [knob.key]: Number(event.target.value),
                      })
                    }
                  />
                </label>
              ))}
              <label className="block space-y-2 text-xs">
                <span>{t('dub.style')}</span>
                <Input
                  value={session.instruct || ''}
                  placeholder={t('dub.style_placeholder')}
                  disabled={busy || Boolean(session.recovery)}
                  onChange={(event) => setDubProduction({ instruct: event.target.value })}
                />
              </label>
              <Button
                size="xs"
                variant="ghost"
                disabled={busy || Boolean(session.recovery)}
                onClick={() =>
                  setDubProduction({
                    steps: undefined,
                    guidance: undefined,
                    speed: undefined,
                    instruct: undefined,
                  })
                }
              >
                {t('dub.reset')}
              </Button>
            </div>
          </details>
          <DubExportPanel
            key={session.jobId || session.inputType}
            session={session}
            disabled={busy || Boolean(session.recovery)}
          />
        </SecondarySidebar>
        <section data-slot="dub-transcript" className="flex min-w-0 flex-1 flex-col">
          <div
            ref={mainScroll}
            className={cn(
              'studio-scrollbar min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 pb-5 scroll-smooth motion-reduce:scroll-auto',
              session.segments.length > 0 ? 'pt-0' : 'pt-5',
            )}
          >
            {session.recovery && !busy && (
              <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-warning/20 bg-warning/5 p-3">
                <div className="mr-auto min-w-48">
                  <p className="text-sm font-medium">
                    {session.asrModelMissing ? t('asr_missing.message') : t('dub.pipeline')}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {session.asrModelMissing && installedAsrModel
                      ? `${installedAsrModel.label} · ${t('asr_missing.installed')}`
                      : t(
                          session.recovery === 'transcribing'
                            ? 'dub.retry_transcription'
                            : session.recovery === 'generating'
                              ? 'dub_workflow.generating_dub'
                              : 'dub.prep_preparing',
                        )}
                  </p>
                </div>
                {session.asrModelMissing ? (
                  installedAsrModel ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={recoveringAsr}
                      onClick={() => void recoverWithInstalledAsr()}
                    >
                      {recoveringAsr && (
                        <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                      )}
                      {t('asr_missing.use', { label: installedAsrModel.label })}
                    </Button>
                  ) : (
                    <div className="flex flex-wrap items-center gap-2">
                      <Link
                        to="/settings/models/$family"
                        params={{ family: 'asr' }}
                        className={buttonVariants({
                          variant: 'outline',
                          size: 'sm',
                        })}
                      >
                        {t('asr_missing.choose')}
                      </Link>
                      <AgentFixButton request="Restore local ASR readiness for this Dubbing job. Install and activate the best supported Faster-Whisper model through VoiceStudio's setup interfaces, preserve the prepared media, and leave transcription ready to retry." />
                    </div>
                  )
                ) : (
                  <Button size="sm" variant="outline" onClick={() => void resumeDub()}>
                    {t(session.recovery === 'transcribing' ? 'common.retry' : 'common.resume')}
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={() => void cancelDub()}>
                  {t('common.cancel')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    discardDubRecovery();
                    input.current?.click();
                  }}
                >
                  {t('dub.change_file')}
                </Button>
              </div>
            )}
            {errorMessage && (
              <PipelineFailure
                className="mb-4"
                failure={session.failure}
                fallback={errorMessage}
                onDismiss={dismissDubError}
              />
            )}
            {!session.segments.length && (!session.recovery || busy) && (
              <div className="mx-auto flex min-h-[24rem] w-full max-w-5xl flex-col items-center justify-center px-6 text-center">
                <div className="mb-3 flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                  {busy ? (
                    <LoaderCircleIcon className="size-6 animate-spin motion-reduce:animate-none" />
                  ) : (
                    <FilmIcon className="size-6" />
                  )}
                </div>
                <h2 className="text-lg font-semibold">{t('dub.video_dubbing_studio')}</h2>
                {busy ? (
                  <p className="mt-2 text-sm text-muted-foreground" role="status">
                    {t(stage)}
                  </p>
                ) : (
                  <>
                    <p className="mt-2 text-sm text-muted-foreground">
                      {t('dub.supported_formats')}
                    </p>
                    {!session.jobId && !demoDismissed && (
                      <div className="mt-4 w-full">
                        <DubbingDemo
                          onTry={() => input.current?.click()}
                          onEdit={async ({ path, filename }) => {
                            try {
                              const response = await apiFetch(path);
                              const blob = await response.blob();
                              setPreview('original');
                              await uploadDub(
                                new File([blob], filename, {
                                  type: blob.type || 'video/mp4',
                                }),
                              );
                            } catch (error) {
                              toast.error(describeError(error));
                            }
                          }}
                          onDismiss={() => {
                            setDemoDismissed(true);
                            try {
                              localStorage.setItem('omnivoice.dubbingDemoDismissed', '1');
                            } catch {
                              // The demo still dismisses for this session when storage is unavailable.
                            }
                          }}
                        />
                      </div>
                    )}
                    <ol className="mt-4 grid w-full grid-cols-3 gap-2 text-left max-sm:grid-cols-1">
                      {[
                        ['1', 'dub.upload_transcribe'],
                        ['2', 'dub.translate'],
                        ['3', 'dub.generate_dub'],
                      ].map(([number, label]) => (
                        <li
                          key={number}
                          className="flex items-center gap-2 rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-xs"
                        >
                          <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 font-medium text-primary">
                            {number}
                          </span>
                          {t(label)}
                        </li>
                      ))}
                    </ol>
                  </>
                )}
              </div>
            )}
            <div className="w-full space-y-4">
              {session.segments.length > 0 && (
                <div
                  className="sticky top-0 z-30 -mx-6 grid min-h-12 w-[calc(100%+3rem)] min-w-0 isolate grid-cols-[auto_minmax(0,1fr)_auto] items-center overflow-hidden border-y border-border/60 px-2 py-1 shadow-sm max-[1100px]:grid-cols-[minmax(0,1fr)_auto]"
                  style={{ backgroundColor: 'var(--background)' }}
                >
                  <div className="col-start-1 row-start-1 flex min-w-0 items-center gap-1.5">
                    <span className="shrink-0 rounded-md bg-muted/35 px-2 py-1 text-xs font-medium tabular-nums text-muted-foreground">
                      {session.segments.length} {t('dub.segments')}
                    </span>
                    <Button
                      data-slot="dub-live-preview-toggle"
                      size="sm"
                      variant={livePreviewEnabled ? 'secondary' : 'ghost'}
                      aria-pressed={livePreviewEnabled}
                      aria-label={t('dub.live_preview')}
                      title={t('dub.live_preview_title')}
                      disabled={busy || Boolean(session.recovery)}
                      onClick={() => {
                        const next = !livePreviewEnabled;
                        setLivePreviewEnabled(next);
                        setSegmentPreview(null);
                        try {
                          localStorage.setItem(LIVE_PREVIEW_KEY, next ? '1' : '0');
                        } catch {
                          // The toggle still applies for this session.
                        }
                      }}
                    >
                      <HeadphonesIcon />
                      <span className="hidden @5xl:inline">{t('dub.live_preview')}</span>
                    </Button>
                  </div>
                  <div className="contents">
                    {batchTargets.length > 1 && (
                      <div className="col-start-3 row-start-1 ml-2 flex min-w-0 items-center gap-2 max-[1100px]:col-start-2">
                        <span className="hidden text-xs text-muted-foreground @3xl:inline">
                          {t('dub.target_language')}
                        </span>
                        <LanguagePicker
                          value={target}
                          options={batchTargets.map((item) => item.lang)}
                          onValueChange={(language) => {
                            const next = batchTargets.find((item) => item.lang === language);
                            if (next) switchEditingLanguage(next);
                          }}
                          className="w-40 bg-muted/30 sm:w-48"
                        />
                        <span
                          className="shrink-0 rounded-md bg-muted/40 px-2 py-1 text-xs tabular-nums text-muted-foreground"
                          title={`${activeTranslatedCount}/${session.segments.length}`}
                        >
                          {activeTranslatedCount}/{session.segments.length}
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="col-start-2 row-start-1 flex min-w-0 items-center justify-center gap-1 overflow-x-auto px-2 [scrollbar-width:none] max-[1100px]:col-span-2 max-[1100px]:col-start-1 max-[1100px]:row-start-2 max-[1100px]:mt-1 max-[1100px]:justify-start max-[1100px]:border-t max-[1100px]:border-border/50 max-[1100px]:pt-1 [&::-webkit-scrollbar]:hidden">
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={t('projects.all')}
                      title={t('projects.all')}
                      aria-pressed={selectedSegmentIds.size === session.segments.length}
                      onClick={() =>
                        setSelectedSegmentIds((current) =>
                          current.size === session.segments.length
                            ? new Set()
                            : new Set(session.segments.map((segment) => segment.id)),
                        )
                      }
                    >
                      <CheckCheckIcon />
                      {t('projects.all')}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={t('segmentEditing.undo')}
                      title={t('segmentEditing.undo')}
                      disabled={busy || Boolean(session.recovery) || editHistory.undoDepth === 0}
                      onClick={undoDubEdit}
                    >
                      <Undo2Icon />
                      <span className="hidden @6xl:inline">{t('segmentEditing.undo')}</span>
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={t('segmentEditing.redo')}
                      title={t('segmentEditing.redo')}
                      disabled={busy || Boolean(session.recovery) || editHistory.redoDepth === 0}
                      onClick={redoDubEdit}
                    >
                      <Redo2Icon />
                      <span className="hidden @6xl:inline">{t('segmentEditing.redo')}</span>
                    </Button>
                    {failedTranslationCount > 0 && code && (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={busy || Boolean(session.recovery) || !translationReady}
                        onClick={() =>
                          void translateDub(code, provider?.id || 'argos', {
                            retryFailed: true,
                          })
                        }
                      >
                        <RotateCcwIcon />
                        {t('dub.retry_failed', {
                          count: failedTranslationCount,
                        })}
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant={pasteTranslationOpen ? 'secondary' : 'ghost'}
                      disabled={busy || Boolean(session.recovery) || !code}
                      aria-expanded={pasteTranslationOpen}
                      aria-label={t('dub.paste_translation_btn')}
                      title={t('dub.paste_translation_btn')}
                      onClick={() => {
                        setPasteTranslationOpen((open) => !open);
                        setGlossaryOpen(false);
                      }}
                    >
                      <ClipboardPasteIcon />
                      <span className="hidden @5xl:inline">{t('dub.paste_translation_btn')}</span>
                    </Button>
                    <Button
                      size="sm"
                      variant={glossaryOpen ? 'secondary' : 'ghost'}
                      disabled={busy || Boolean(session.recovery) || !code || !session.jobId}
                      aria-expanded={glossaryOpen}
                      aria-label={t('dub.glossary_btn', {
                        count: glossaryCount,
                      })}
                      title={t('dub.glossary_btn', { count: glossaryCount })}
                      onClick={() => {
                        setGlossaryOpen((open) => !open);
                        setPasteTranslationOpen(false);
                      }}
                    >
                      <BookOpenIcon />
                      <span className="hidden @6xl:inline">
                        {t('dub.glossary_btn', { count: glossaryCount })}
                      </span>
                    </Button>
                    <Menu.Root>
                      <Menu.Trigger
                        disabled={busy || Boolean(session.recovery)}
                        aria-label={t('segmentEditing.more')}
                        className={buttonVariants({
                          variant: 'ghost',
                          size: 'icon-sm',
                        })}
                      >
                        {session.phase === 'cleaning' ? (
                          <LoaderCircleIcon className="animate-spin" />
                        ) : (
                          <MoreHorizontalIcon />
                        )}
                      </Menu.Trigger>
                      <Menu.Portal>
                        <Menu.Positioner sideOffset={6} align="end" className="z-50">
                          <Menu.Popup className="min-w-52 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg outline-none">
                            <Menu.Item
                              disabled={!session.jobId}
                              onClick={() =>
                                void cleanupDubSegments().then((removed) => {
                                  if (removed === null) {
                                    toast.error(
                                      t('dub_workflow.cleanup_failed', {
                                        message: dubSession.state.error || t('common.error'),
                                      }),
                                    );
                                    return;
                                  }
                                  const valid = new Set(
                                    dubSession.state.segments.map((item) => item.id),
                                  );
                                  setSelectedSegmentIds(
                                    (current) =>
                                      new Set([...current].filter((id) => valid.has(id))),
                                  );
                                  toast.success(
                                    removed
                                      ? t('dub_workflow.cleaned', {
                                          count: removed,
                                        })
                                      : t('dub_workflow.segments_clean'),
                                  );
                                })
                              }
                              className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-disabled:opacity-40 data-highlighted:bg-accent"
                            >
                              <WandSparklesIcon className="size-4" />
                              {t('dub.clean_up')}
                            </Menu.Item>
                            <Menu.Item
                              disabled={!canRestoreOriginal}
                              onClick={restoreDubSegments}
                              className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-disabled:opacity-40 data-highlighted:bg-accent"
                            >
                              <RotateCcwIcon className="size-4" />
                              {t('dub.restore')}
                            </Menu.Item>
                            {failedTranslationCount > 0 && code && (
                              <Menu.Item
                                onClick={() => skipFailedDubTranslations(code)}
                                className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-highlighted:bg-accent"
                              >
                                <XIcon className="size-4" />
                                {t('dub.skip_failed')}
                              </Menu.Item>
                            )}
                          </Menu.Popup>
                        </Menu.Positioner>
                      </Menu.Portal>
                    </Menu.Root>
                    {session.phase === 'done' && session.tracks.length > 0 && (
                      <Button
                        size="sm"
                        variant="ghost"
                        title={t('dub.qc_btn')}
                        disabled={qcRunning || busy || Boolean(session.recovery)}
                        aria-busy={qcRunning}
                        onClick={() => void verifyDub()}
                      >
                        {qcRunning ? (
                          <LoaderCircleIcon className="animate-spin" />
                        ) : (
                          <ShieldCheckIcon />
                        )}
                        {t('dub.verify')}
                      </Button>
                    )}
                  </div>
                </div>
              )}
              {showCheckpoint && checkpointStage && (
                <CheckpointBanner
                  stage={checkpointStage}
                  timingWarnings={
                    session.segments.filter((segment) => segment.fit_status?.status === 'overflows')
                      .length
                  }
                  disabled={
                    checkpointStage === 'asr'
                      ? !code || !translationReady
                      : checkpointStage === 'translate'
                        ? !code || ttsBlocker !== null
                        : false
                  }
                  onContinue={
                    checkpointStage === 'asr'
                      ? () => void translateTargets()
                      : checkpointStage === 'translate'
                        ? () => void generateTargets()
                        : undefined
                  }
                  onDismiss={() =>
                    setDismissedCheckpoints((current) => new Set(current).add(checkpointId))
                  }
                />
              )}
              {pasteTranslationOpen && code && session.segments.length > 0 && (
                <PasteTranslation
                  segments={session.segments}
                  disabled={busy || Boolean(session.recovery)}
                  onClose={() => setPasteTranslationOpen(false)}
                  onApply={(rows) => applyDubTranslationRows(code, rows)}
                />
              )}
              {glossaryOpen && code && session.jobId && session.segments.length > 0 && (
                <GlossaryPanel
                  projectId={session.jobId}
                  sourceLang={session.sourceLang || session.sourceLanguage || 'auto'}
                  targetLang={code}
                  segments={session.segments}
                  disabled={busy || Boolean(session.recovery)}
                  onCountChange={setGlossaryCount}
                  onClose={() => setGlossaryOpen(false)}
                />
              )}
              {selectedSegmentIds.size > 0 && (
                <div className="flex flex-wrap items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 p-2.5">
                  <span className="mr-auto flex items-center gap-2 px-1 text-sm font-medium text-primary">
                    <CheckCheckIcon className="size-4" />
                    {t('dub.selected_count', {
                      count: selectedSegmentIds.size,
                    })}
                  </span>
                  <Menu.Root>
                    <Menu.Trigger
                      disabled={busy || Boolean(session.recovery)}
                      className={buttonVariants({
                        variant: 'outline',
                        size: 'sm',
                      })}
                    >
                      {t('dub.set_voice')}
                      <ChevronDownIcon />
                    </Menu.Trigger>
                    <Menu.Portal>
                      <Menu.Positioner sideOffset={6} align="end" className="z-50">
                        <Menu.Popup className="max-h-72 min-w-52 overflow-y-auto rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg outline-none">
                          <Menu.Item
                            onClick={() =>
                              editDubSegments(selectedSegmentIds, {
                                profile_id: undefined,
                              })
                            }
                            className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-highlighted:bg-accent"
                          >
                            <RotateCcwIcon className="size-4" />
                            {t('dub.clear_voice')}
                          </Menu.Item>
                          {profiles.data?.map((profile) => (
                            <Menu.Item
                              key={profile.id}
                              onClick={() =>
                                editDubSegments(selectedSegmentIds, {
                                  profile_id: profile.id,
                                })
                              }
                              className="cursor-default rounded-md px-3 py-2 text-sm outline-none data-highlighted:bg-accent"
                            >
                              {profile.name}
                            </Menu.Item>
                          ))}
                        </Menu.Popup>
                      </Menu.Positioner>
                    </Menu.Portal>
                  </Menu.Root>
                  <LanguagePicker
                    value={t('dub.set_lang')}
                    options={targetOptions}
                    disabled={busy || Boolean(session.recovery)}
                    onValueChange={(language) => {
                      const lang = LANG_CODES.find((item) => item.label === language);
                      if (lang)
                        editDubSegments(selectedSegmentIds, {
                          target_lang: lang.code,
                        });
                    }}
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy || Boolean(session.recovery)}
                    onClick={() =>
                      editDubSegments(selectedSegmentIds, {
                        target_lang: undefined,
                      })
                    }
                  >
                    <RotateCcwIcon />
                    {t('dub.default_lang')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy || Boolean(session.recovery)}
                    onClick={() => {
                      deleteDubSegments(selectedSegmentIds);
                      setSelectedSegmentIds(new Set());
                    }}
                  >
                    <Trash2Icon />
                    {t('dub.delete_selected')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setSelectedSegmentIds(new Set())}
                  >
                    <XIcon />
                    {t('dub.clear_selection')}
                  </Button>
                </div>
              )}
              {session.segments.length > 0 && (
                <DubTimeline
                  segments={session.segments}
                  disabled={busy || Boolean(session.recovery)}
                  mediaDuration={session.duration}
                  onsets={timelineOnsets.onsets}
                  peaks={timelineOnsets.peaks}
                  previewingId={previewingSegmentId}
                  selectedId={selectedSegmentId}
                  onSelect={(id) => {
                    setSelectedSegmentId(id);
                    const index = session.segments.findIndex((segment) => segment.id === id);
                    if (index >= 0)
                      requestAnimationFrame(() =>
                        segmentVirtualizer.scrollToIndex(index, {
                          align: 'center',
                        }),
                      );
                  }}
                  onPreviewSegment={(segment) => void previewDubSegment(segment)}
                />
              )}
              {session.segments.length > 0 && (
                <div
                  ref={segmentList}
                  className="relative w-full"
                  style={{ height: segmentVirtualizer.getTotalSize() }}
                >
                  {segmentVirtualizer.getVirtualItems().map((row) => {
                    const index = row.index;
                    const segment = session.segments[index];
                    if (!segment) return null;
                    return (
                      <div
                        key={row.key}
                        ref={segmentVirtualizer.measureElement}
                        data-index={row.index}
                        className="absolute top-0 left-0 w-full [contain:layout_style] [will-change:transform]"
                        style={{
                          transform: `translate3d(0, ${row.start - segmentScrollMargin}px, 0)`,
                        }}
                      >
                        <article
                          data-dub-row-id={segment.id}
                          onFocusCapture={() => setSelectedSegmentId(segment.id)}
                          className={cn(
                            'group/segment relative isolate grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-2 px-2 py-3.5 transition-[background-color,box-shadow] after:pointer-events-none after:absolute after:right-2 after:bottom-0 after:left-[4.25rem] after:h-px after:bg-gradient-to-r after:from-border/65 after:via-border/35 after:to-transparent after:transition-opacity hover:bg-muted/[0.12] hover:after:opacity-100 focus-within:bg-muted/[0.16] focus-within:after:from-primary/45',
                            selectedSegmentId === segment.id &&
                              'bg-primary/[0.05] shadow-[inset_2px_0_0_color-mix(in_oklab,var(--primary)_55%,transparent)] after:from-primary/55 after:via-primary/20',
                          )}
                        >
                          <div
                            className={cn(
                              'flex items-start gap-1.5 pt-1 font-mono text-[11px] text-muted-foreground/55 transition-colors group-hover/segment:text-muted-foreground/75 group-focus-within/segment:text-muted-foreground',
                              selectedSegmentId === segment.id && 'text-muted-foreground/90',
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={selectedSegmentIds.has(segment.id)}
                              disabled={busy || Boolean(session.recovery)}
                              aria-label={`${t('modelSettings.select')} ${index + 1}`}
                              className="mt-0.5 size-4 accent-primary"
                              onChange={() =>
                                setSelectedSegmentIds((current) => {
                                  const next = new Set(current);
                                  if (next.has(segment.id)) next.delete(segment.id);
                                  else next.add(segment.id);
                                  return next;
                                })
                              }
                            />
                            <span>
                              {Number.isFinite(segment.start) ? segment.start.toFixed(1) : '?'}s
                            </span>
                          </div>
                          <div className="min-w-0">
                            <div className="flex min-h-6 min-w-0 items-center gap-1">
                              <Button
                                data-segment-options-trigger
                                size="sm"
                                variant="ghost"
                                className={cn(
                                  'h-6 min-w-0 max-w-[14rem] justify-start px-1 text-[11px] font-medium tracking-[0.01em] text-muted-foreground/55 hover:text-foreground group-hover/segment:text-muted-foreground/75 group-focus-within/segment:text-foreground/85',
                                  selectedSegmentId === segment.id && 'text-foreground/85',
                                )}
                                aria-expanded={expandedSegmentId === segment.id}
                                aria-controls={`dub-segment-options-${index}`}
                                onClick={() => {
                                  setExpandedSegmentId((current) =>
                                    current === segment.id ? null : segment.id,
                                  );
                                  requestAnimationFrame(() => segmentVirtualizer.measure());
                                }}
                              >
                                <ChevronDownIcon
                                  className={cn(
                                    'size-3.5 transition-transform',
                                    expandedSegmentId === segment.id && 'rotate-180',
                                  )}
                                />
                                <span className="truncate">
                                  {segment.speaker_id || t('clone.voice_source')} &middot;{' '}
                                  {profiles.data?.find(
                                    (profile) => profile.id === segment.profile_id,
                                  )?.name ||
                                    (segment.profile_id?.startsWith('auto:')
                                      ? t('clone.auto')
                                      : segment.profile_id) ||
                                    t('clone.auto')}
                                </span>
                              </Button>
                              {segment.text_original && segment.text_original !== segment.text ? (
                                <p
                                  className={cn(
                                    'min-w-0 flex-1 truncate px-1 text-xs text-muted-foreground/50 transition-colors group-hover/segment:text-muted-foreground/70 group-focus-within/segment:text-foreground/70',
                                    selectedSegmentId === segment.id && 'text-foreground/72',
                                  )}
                                  title={segment.text_original}
                                >
                                  {segment.text_original}
                                </p>
                              ) : (
                                <span className="min-w-0 flex-1" />
                              )}
                              {(segment.translate_error ||
                                segment.translate_degraded ||
                                segment.plan?.status === 'tight' ||
                                segment.plan?.status === 'impossible' ||
                                segment.fit_status?.status === 'overflows') && (
                                <div className="ml-auto flex shrink-0 flex-wrap justify-end gap-1">
                                  {segment.translate_error && (
                                    <span
                                      className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive"
                                      title={t('segment.translate_error_title', {
                                        error: segment.translate_error,
                                      })}
                                    >
                                      <AlertCircleIcon className="size-3" />
                                      {t('common.error')}
                                    </span>
                                  )}
                                  {segment.translate_degraded && (
                                    <span
                                      className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning"
                                      title={t('segment.translate_degraded_title', {
                                        reason: segment.translate_degraded,
                                      })}
                                    >
                                      <AlertCircleIcon className="size-3" />
                                      {t('dub.fast_quality')}
                                    </span>
                                  )}
                                  {segment.plan?.status === 'tight' && (
                                    <span
                                      className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning"
                                      title={t('segment.plan_tight_title', {
                                        est: segment.plan.est_dur_s.toFixed(1),
                                        avail: segment.plan.available_s.toFixed(1),
                                      })}
                                    >
                                      <Clock3Icon className="size-3" />
                                      {t('segment.plan_tight')}
                                    </span>
                                  )}
                                  {segment.plan?.status === 'impossible' && (
                                    <span
                                      className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive"
                                      title={t('segment.plan_impossible_title', {
                                        est: segment.plan.est_dur_s.toFixed(1),
                                        avail: segment.plan.available_s.toFixed(1),
                                        seconds: segment.plan.est_overrun_s.toFixed(1),
                                      })}
                                    >
                                      <AlertCircleIcon className="size-3" />
                                      {t('segment.plan_impossible', {
                                        seconds: segment.plan.est_overrun_s.toFixed(1),
                                      })}
                                    </span>
                                  )}
                                  {segment.fit_status?.status === 'overflows' && (
                                    <span
                                      className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive"
                                      title={t('segment.fit_overflows_title', {
                                        seconds: (segment.fit_status.overflow_s || 0).toFixed(1),
                                      })}
                                    >
                                      <ScissorsIcon className="size-3" />
                                      {t('segment.fit_overflows', {
                                        seconds: (segment.fit_status.overflow_s || 0).toFixed(1),
                                      })}
                                    </span>
                                  )}
                                </div>
                              )}
                              {segment.qc_flagged && (
                                <span
                                  className="inline-flex shrink-0 items-center gap-1 rounded-full bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning"
                                  title={segment.qc_recognized || t('dub.qc_btn')}
                                >
                                  <AlertCircleIcon className="size-3" />
                                  {t('dub.verify')}
                                </span>
                              )}
                              <div className="ml-auto flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/segment:opacity-100 group-focus-within/segment:opacity-100">
                                <Button
                                  size="icon-xs"
                                  variant="ghost"
                                  disabled={
                                    busy ||
                                    Boolean(session.recovery) ||
                                    (!livePreviewEnabled && Boolean(previewingSegmentId))
                                  }
                                  aria-label={t('dub.live_preview')}
                                  aria-busy={
                                    previewingSegmentId === segment.id ||
                                    livePreview.liveSegmentId === segment.id
                                  }
                                  aria-pressed={livePreview.liveSegmentId === segment.id}
                                  className={cn(
                                    livePreview.liveSegmentId === segment.id &&
                                      'bg-primary/10 text-primary',
                                  )}
                                  onClick={() =>
                                    livePreviewEnabled
                                      ? livePreview.onToggle(segment)
                                      : void previewDubSegment(segment)
                                  }
                                >
                                  {previewingSegmentId === segment.id ||
                                  livePreview.liveSegmentId === segment.id ? (
                                    <LoaderCircleIcon className="animate-spin" />
                                  ) : (
                                    <HeadphonesIcon />
                                  )}
                                </Button>
                                <Menu.Root>
                                  <Menu.Trigger
                                    disabled={busy || Boolean(session.recovery)}
                                    aria-label={t('segmentEditing.more')}
                                    className={buttonVariants({
                                      variant: 'ghost',
                                      size: 'icon-xs',
                                    })}
                                  >
                                    <MoreHorizontalIcon />
                                  </Menu.Trigger>
                                  <Menu.Portal>
                                    <Menu.Positioner sideOffset={6} align="end" className="z-50">
                                      <Menu.Popup className="min-w-52 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg outline-none">
                                        <Menu.Item
                                          disabled={segment.text.trim().length < 2}
                                          onClick={() => splitAtCursor(segment.id, segment.text)}
                                          className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-disabled:opacity-40 data-highlighted:bg-accent"
                                        >
                                          <ScissorsIcon className="size-4" />
                                          {t('segmentEditing.split')}
                                        </Menu.Item>
                                        <Menu.Item
                                          disabled={index === 0}
                                          onClick={() => mergeDubSegment(segment.id, 'prev')}
                                          className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-disabled:opacity-40 data-highlighted:bg-accent"
                                        >
                                          <MergeIcon className="size-4" />
                                          {t('segmentEditing.mergePrevious')}
                                        </Menu.Item>
                                        <Menu.Item
                                          disabled={index === session.segments.length - 1}
                                          onClick={() => mergeDubSegment(segment.id)}
                                          className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-disabled:opacity-40 data-highlighted:bg-accent"
                                        >
                                          <MergeIcon className="size-4" />
                                          {t('segmentEditing.mergeNext')}
                                        </Menu.Item>
                                        <Menu.Item
                                          onClick={() => insertDubSegment(segment.id)}
                                          className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-highlighted:bg-accent"
                                        >
                                          <PlusIcon className="size-4" />
                                          {t('segmentEditing.insert')}
                                        </Menu.Item>
                                      </Menu.Popup>
                                    </Menu.Positioner>
                                  </Menu.Portal>
                                </Menu.Root>
                                <Button
                                  size="icon-xs"
                                  variant="ghost"
                                  disabled={busy || Boolean(session.recovery)}
                                  aria-label={t('common.delete')}
                                  onClick={() => deleteDubSegment(segment.id)}
                                >
                                  <Trash2Icon />
                                </Button>
                              </div>
                            </div>
                            {editingSegmentId === segment.id ? (
                              <textarea
                                data-segment-id={segment.id}
                                aria-label={t('clone.text_label') + ' ' + (index + 1)}
                                rows={1}
                                autoFocus
                                className="min-h-8 max-h-32 w-full resize-y overflow-y-auto rounded-md border border-input bg-background/35 px-2 py-1 text-sm leading-5 outline-none transition-[background-color,border-color,box-shadow] [field-sizing:content] focus-visible:ring-2 focus-visible:ring-ring"
                                disabled={busy || Boolean(session.recovery)}
                                value={segment.text}
                                onBlur={() => setEditingSegmentId(null)}
                                onKeyDown={(event) => {
                                  if (event.key === 'Escape') event.currentTarget.blur();
                                }}
                                onSelect={(event) =>
                                  cursors.current.set(
                                    segment.id,
                                    event.currentTarget.selectionStart,
                                  )
                                }
                                onChange={(event) => {
                                  const text = event.target.value;
                                  if (code && typeof segment.translations?.[code] === 'string')
                                    editDubSegment(
                                      segment.id,
                                      {
                                        text,
                                        translations: {
                                          ...segment.translations,
                                          [code]: text,
                                        },
                                      },
                                      { historyGroup: `text:${segment.id}` },
                                    );
                                  else
                                    editDubSegment(
                                      segment.id,
                                      {
                                        text,
                                        text_original: text,
                                        translations: undefined,
                                      },
                                      { historyGroup: `text:${segment.id}` },
                                    );
                                  livePreview.onEdit(segment, text);
                                }}
                              />
                            ) : (
                              <button
                                type="button"
                                data-segment-id={segment.id}
                                disabled={busy || Boolean(session.recovery)}
                                className={cn(
                                  'block min-h-10 w-full rounded-lg bg-transparent px-2.5 py-1.5 text-left text-[15px] leading-6 text-foreground/90 outline-none transition-[color,background-color,box-shadow] hover:bg-background/25 hover:text-foreground focus-visible:bg-background/35 focus-visible:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:opacity-60',
                                  selectedSegmentId === segment.id &&
                                    'bg-background/25 text-foreground shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--border)_45%,transparent)]',
                                )}
                                onClick={() => {
                                  setSelectedSegmentId(segment.id);
                                  setEditingSegmentId(segment.id);
                                }}
                              >
                                <span className="line-clamp-2">{segment.text}</span>
                              </button>
                            )}
                            {segment.plan?.suggested_text && (
                              <div className="flex items-center gap-2 rounded-md border border-warning/20 bg-warning/5 p-1.5 text-xs">
                                <span className="min-w-0 flex-1 truncate text-muted-foreground">
                                  {segment.plan.suggested_text}
                                </span>
                                <Button
                                  size="xs"
                                  variant="outline"
                                  disabled={busy || Boolean(session.recovery)}
                                  title={t('segment.plan_apply_title', {
                                    text: segment.plan.suggested_text,
                                  })}
                                  onClick={() => {
                                    const text = segment.plan?.suggested_text;
                                    if (!text) return;
                                    editDubSegment(segment.id, {
                                      text,
                                      translations: code
                                        ? {
                                            ...segment.translations,
                                            [code]: text,
                                          }
                                        : segment.translations,
                                    });
                                  }}
                                >
                                  {t('segment.plan_apply')}
                                </Button>
                              </div>
                            )}
                            <div className="text-xs">
                              {expandedSegmentId === segment.id && (
                                <div
                                  id={`dub-segment-options-${index}`}
                                  className="mt-1.5 rounded-lg border border-border/50 bg-muted/20 p-2.5"
                                >
                                  <div className="mt-2 flex flex-wrap gap-1">
                                    {profiles.data?.map((profile) => (
                                      <Button
                                        key={profile.id}
                                        size="xs"
                                        variant={
                                          segment.profile_id === profile.id ? 'secondary' : 'ghost'
                                        }
                                        disabled={busy || Boolean(session.recovery)}
                                        onClick={() =>
                                          editDubSegment(segment.id, {
                                            profile_id: profile.id,
                                          })
                                        }
                                      >
                                        {profile.name}
                                      </Button>
                                    ))}
                                  </div>
                                  <div className="mt-3 grid grid-cols-2 gap-2">
                                    <label className="space-y-1">
                                      <span>{t('player.volume')}</span>
                                      <Input
                                        type="number"
                                        min={0}
                                        max={2}
                                        step={0.1}
                                        value={segment.gain ?? 1}
                                        disabled={busy || Boolean(session.recovery)}
                                        onChange={(event) => {
                                          const gain = Number(event.target.value);
                                          if (Number.isFinite(gain) && gain >= 0 && gain <= 2)
                                            editDubSegment(segment.id, {
                                              gain,
                                            });
                                        }}
                                      />
                                    </label>
                                    <label className="space-y-1">
                                      <span>{t('clone.speed')}</span>
                                      <Input
                                        type="number"
                                        min={0.5}
                                        max={2}
                                        step={0.05}
                                        placeholder={String(session.speed ?? 1)}
                                        value={segment.speed ?? ''}
                                        disabled={busy || Boolean(session.recovery)}
                                        onChange={(event) => {
                                          const raw = event.target.value;
                                          const speed = Number(raw);
                                          if (
                                            raw === '' ||
                                            (Number.isFinite(speed) && speed >= 0.5 && speed <= 2)
                                          )
                                            editDubSegment(segment.id, {
                                              speed: raw === '' ? undefined : speed,
                                            });
                                        }}
                                      />
                                    </label>
                                    <label className="col-span-2 space-y-1">
                                      <span>{t('tools.direction')}</span>
                                      <Input
                                        value={segment.direction || ''}
                                        disabled={busy || Boolean(session.recovery)}
                                        onChange={(event) =>
                                          editDubSegment(segment.id, {
                                            direction: event.target.value,
                                          })
                                        }
                                      />
                                    </label>
                                  </div>
                                  <div className="mt-3 flex gap-2">
                                    <Input
                                      key={`start:${segment.start}`}
                                      type="number"
                                      step="0.01"
                                      min={0}
                                      aria-label={t('dubWorkspace.start')}
                                      defaultValue={segment.start.toFixed(2)}
                                      disabled={busy || Boolean(session.recovery)}
                                      onKeyDown={(event) => {
                                        if (event.key === 'Enter') event.currentTarget.blur();
                                        if (event.key === 'Escape') {
                                          event.currentTarget.value = segment.start.toFixed(2);
                                          event.currentTarget.blur();
                                        }
                                      }}
                                      onBlur={(event) => {
                                        const start = Number(event.target.value);
                                        if (!Number.isFinite(start)) {
                                          event.currentTarget.value = segment.start.toFixed(2);
                                          return;
                                        }
                                        const timing = clampSegmentEdit(
                                          session.segments,
                                          index,
                                          'start',
                                          { start, end: segment.end },
                                          {
                                            duration: session.duration || undefined,
                                          },
                                        );
                                        event.currentTarget.value = timing.start.toFixed(2);
                                        moveResizeDubSegment(segment.id, timing);
                                      }}
                                    />
                                    <Input
                                      key={`end:${segment.end}`}
                                      type="number"
                                      step="0.01"
                                      min={segment.start}
                                      aria-label={t('dubWorkspace.end')}
                                      defaultValue={segment.end.toFixed(2)}
                                      disabled={busy || Boolean(session.recovery)}
                                      onKeyDown={(event) => {
                                        if (event.key === 'Enter') event.currentTarget.blur();
                                        if (event.key === 'Escape') {
                                          event.currentTarget.value = segment.end.toFixed(2);
                                          event.currentTarget.blur();
                                        }
                                      }}
                                      onBlur={(event) => {
                                        const end = Number(event.target.value);
                                        if (!Number.isFinite(end)) {
                                          event.currentTarget.value = segment.end.toFixed(2);
                                          return;
                                        }
                                        const timing = clampSegmentEdit(
                                          session.segments,
                                          index,
                                          'end',
                                          { start: segment.start, end },
                                          {
                                            duration: session.duration || undefined,
                                          },
                                        );
                                        event.currentTarget.value = timing.end.toFixed(2);
                                        moveResizeDubSegment(segment.id, timing);
                                      }}
                                    />
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        </article>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
          <EngineNotice operation="dub" />
          {(busy || session.segments.length > 0) && (
            <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border/50 px-6 py-4">
              {busy ? (
                <>
                  <span role="status" className="mr-auto text-sm text-muted-foreground">
                    {session.batchProgress
                      ? `${session.batchProgress.current}/${session.batchProgress.total} · ${session.batchProgress.language} · `
                      : ''}
                    {t(stage)}
                    {progressPercent !== null
                      ? ` ${Math.max(0, Math.min(100, Math.round(progressPercent)))}%`
                      : ''}
                  </span>
                  <Button
                    variant="outline"
                    disabled={session.phase === 'importing'}
                    onClick={() => void cancelDub()}
                  >
                    {t('common.cancel')}
                  </Button>
                </>
              ) : (
                <>
                  {session.phase === 'done' && incrementalPlan && (
                    <span className="mr-auto text-xs text-muted-foreground">
                      {incrementalPlan.stale.length
                        ? t('dub.segments_changed', {
                            count: incrementalPlan.stale.length,
                          })
                        : t('dub.all_up_to_date', {
                            count: incrementalPlan.fresh.length,
                          })}
                    </span>
                  )}
                  {session.quality !== 'agent' && (
                    <Button
                      variant="outline"
                      disabled={
                        Boolean(session.recovery) ||
                        !session.segments.length ||
                        !code ||
                        !translationReady
                      }
                      onClick={() => void translateTargets()}
                    >
                      <LanguagesIcon />
                      {t('dub.translate_all')}
                    </Button>
                  )}
                  {selectedTranslationAgent ? (
                    <div className="flex items-center">
                      <Button
                        data-slot="translate-with-agent"
                        variant={
                          session.agentCli === selectedTranslationAgent.id ? 'default' : 'outline'
                        }
                        className="rounded-r-none border-primary/25 bg-primary/[0.06] text-foreground hover:bg-primary/10"
                        disabled={
                          Boolean(session.recovery) || !session.segments.length || !code || busy
                        }
                        title={t('dub.agent_cli_desc', {
                          agent: selectedTranslationAgent.label,
                        })}
                        onClick={() => void translateTargets(true)}
                      >
                        <WandSparklesIcon />
                        {t('dub.translate_with_agent')}
                      </Button>
                      <Menu.Root>
                        <Menu.Trigger
                          aria-label={t('dub.choose_translation_agent')}
                          title={selectedTranslationAgent.label}
                          className={cn(
                            buttonVariants({
                              variant:
                                session.agentCli === selectedTranslationAgent.id
                                  ? 'default'
                                  : 'outline',
                              size: 'icon',
                            }),
                            'rounded-l-none border-l-0 border-primary/25 bg-primary/[0.06] hover:bg-primary/10',
                          )}
                        >
                          <ChevronDownIcon />
                        </Menu.Trigger>
                        <Menu.Portal>
                          <Menu.Positioner side="top" sideOffset={6} align="end" className="z-50">
                            <Menu.Popup className="cn-menu-target cn-menu-translucent min-w-44 rounded-lg p-1 surface-glass text-popover-foreground shadow-lg ring-1 ring-border outline-none">
                              {availableTranslationAgents.map((agent) => (
                                <Menu.Item
                                  key={agent.id}
                                  onClick={() => {
                                    setTranslationAgent(agent.id);
                                    try {
                                      localStorage.setItem(DEFAULT_TRANSLATION_AGENT_KEY, agent.id);
                                    } catch {
                                      // The selection remains active for this session.
                                    }
                                  }}
                                  className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-highlighted:bg-accent"
                                >
                                  <span
                                    className={cn(
                                      'size-1.5 rounded-full',
                                      agent.id === translationAgent
                                        ? 'bg-primary'
                                        : 'bg-muted-foreground/40',
                                    )}
                                  />
                                  <span>{agent.label}</span>
                                  <span className="ml-auto max-w-32 truncate text-xs text-muted-foreground">
                                    {agent.version}
                                  </span>
                                </Menu.Item>
                              ))}
                            </Menu.Popup>
                          </Menu.Positioner>
                        </Menu.Portal>
                      </Menu.Root>
                    </div>
                  ) : translationAgentsLoading || llmSkills.isPending ? (
                    <Button data-slot="translate-with-agent" variant="outline" disabled>
                      <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                      {t('dub.translate_with_agent')}
                    </Button>
                  ) : llmAgentReady ? (
                    <Button
                      data-slot="translate-with-agent"
                      variant={session.quality === 'agent' ? 'default' : 'outline'}
                      className="border-primary/25 bg-primary/[0.06] text-foreground hover:bg-primary/10"
                      disabled={
                        Boolean(session.recovery) || !session.segments.length || !code || busy
                      }
                      title={t('dub.agent_quality_desc')}
                      onClick={() => void translateTargets(true)}
                    >
                      <WandSparklesIcon />
                      {t('dub.translate_with_agent')}
                    </Button>
                  ) : (
                    <Button
                      data-slot="translate-with-agent"
                      variant="outline"
                      disabled
                      title={t('dub.no_local_agents')}
                    >
                      <WandSparklesIcon />
                      {t('dub.translate_with_agent')}
                    </Button>
                  )}
                  <Button
                    disabled={
                      Boolean(session.recovery) ||
                      ttsBlocker !== null ||
                      !session.segments.length ||
                      !code ||
                      (batchTargets.length > 1 && !translationReady && !agentBatchReady) ||
                      session.segments.some((segment) => !segment.text.trim())
                    }
                    onClick={() => void generateTargets()}
                  >
                    <PlayIcon />
                    {batchTargets.length > 1
                      ? t('dub.generate_dub_multi', {
                          count: batchTargets.length,
                        })
                      : t('dub.generate_dub')}
                  </Button>
                  {session.phase === 'done' && Boolean(incrementalPlan?.stale.length) && (
                    <Button
                      variant="secondary"
                      disabled={Boolean(session.recovery) || ttsBlocker !== null || !code}
                      onClick={() => void generateTargets(incrementalPlan?.stale)}
                    >
                      <PlayIcon />
                      {t('dub.regen_changed', {
                        count: incrementalPlan?.stale.length || 0,
                      })}
                    </Button>
                  )}
                </>
              )}
            </footer>
          )}
        </section>
      </div>
    </div>
  );
}
