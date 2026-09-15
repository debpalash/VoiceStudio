import { useRef, useState, type ReactNode } from 'react';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import { useTranslationEngines } from '@/features/settings/translation-settings';
import { Link } from '@tanstack/react-router';
import {
  LanguagesIcon,
  AudioLinesIcon,
  MicIcon,
  BrainCircuitIcon,
  KeyboardIcon,
  UsersRoundIcon,
  ArrowLeftRightIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CpuIcon,
  MemoryStickIcon,
  MonitorUpIcon,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { useTranslation } from 'react-i18next';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { engineFamilyState, useEngines } from '@/hooks/use-engines';
import { useDictationSelection } from '@/hooks/use-dictation-selection';
import { cn } from '@/lib/utils';
import { useAppActivities } from '@/lib/app-activity';
import {
  resolveRemoteRuntime,
  resolveRuntimeHealth,
  type SidebarModelStatus,
} from './status-runtime';
import { PerformanceProfile } from '@/components/performance-profile';
import { ComputeVendorIcon, formatComputeRuntime } from '@/components/compute-vendor-icon';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { ComputeTargetChoices } from '@/components/compute-target-choices';
import { useComputeRuntime, useComputeTarget } from '@/hooks/use-compute-target';
import type { BatchJob } from '../../../../../../frontend/src/api/batch-types';
import {
  IDLE_STATUS_POLL_MS,
  batchStatusPollMs,
  loadedModelsPollMs,
  modelStatusPollMs,
} from '@/lib/status-polling';

type BackendStage = ReturnType<typeof useBackendStatus>['stage'];

const ENGINE_ICONS = {
  tts: AudioLinesIcon,
  asr: MicIcon,
  llm: BrainCircuitIcon,
};

interface LoadedModelStatus {
  id: string;
  checkpoint: string;
  device?: string;
  unloadable: boolean;
  engine_id?: string;
  is_active_engine?: boolean | null;
}

interface DeviceUsage {
  cpu: number;
  cpu_model: string;
  cpu_physical_cores: number;
  cpu_logical_cores: number;
  cpu_frequency_ghz: number;
  ram: number;
  total_ram: number;
  gpu_name: string;
  gpu_utilization: number | null;
  vram: number;
  total_vram: number;
  gpu_active: boolean;
}

function boundedPercent(value: number, total = 100) {
  if (!Number.isFinite(value) || !Number.isFinite(total) || total <= 0) return 0;
  return Math.max(0, Math.min(100, (value / total) * 100));
}

function DeviceMetric({
  Icon,
  label,
  value,
  percent,
  detail,
  meta,
}: {
  Icon: typeof CpuIcon;
  label: string;
  value: string;
  percent: number;
  detail?: string;
  meta?: string;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 text-xs">
        <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="font-medium text-foreground/85">{label}</span>
        <span className="ml-auto tabular-nums text-muted-foreground">{value}</span>
      </div>
      {(detail || meta) && (
        <div className="flex min-w-0 items-center gap-2 text-[10px] leading-none text-muted-foreground">
          {detail && (
            <span className="min-w-0 flex-1 truncate" title={detail}>
              {detail}
            </span>
          )}
          {meta && <span className="shrink-0 tabular-nums">{meta}</span>}
        </div>
      )}
      <div className="h-1 overflow-hidden rounded-full bg-muted/80" aria-hidden="true">
        <span
          className="block h-full rounded-full bg-primary transition-[width] duration-300"
          style={{ width: `${boundedPercent(percent)}%` }}
        />
      </div>
    </div>
  );
}

function engineStateClass(state: string) {
  if (state === 'engineRuntime.loading' || state === 'engineRuntime.working')
    return 'bg-amber-400 shadow-[0_0_7px_rgb(251_191_36/55%)] animate-pulse motion-reduce:animate-none';
  if (state === 'engineRuntime.ready') return 'bg-emerald-400 shadow-[0_0_7px_rgb(52_211_153/45%)]';
  if (state === 'engineRuntime.idle')
    return 'bg-primary shadow-[0_0_7px_color-mix(in_oklab,var(--primary)_50%,transparent)]';
  if (state === 'modelSettings.unavailable') return 'bg-destructive';
  return 'bg-muted-foreground/45';
}

const engineLinkClass =
  'group/engine-icon relative flex h-7 min-w-0 items-center justify-center rounded-md text-muted-foreground outline-none transition-[color,background-color,box-shadow,backdrop-filter] duration-150 hover:bg-sidebar-accent/65 hover:text-foreground hover:backdrop-blur-xl hover:shadow-[inset_0_1px_0_rgb(255_255_255/8%),0_5px_14px_rgb(0_0_0/10%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/60 focus-visible:ring-2 focus-visible:ring-ring';
const engineIconClass =
  'size-4 transition-filter duration-150 group-hover/engine-icon:drop-shadow-[0_1px_3px_rgb(0_0_0/20%)]';

function EngineIconStatus({ Icon, state }: { Icon: typeof AudioLinesIcon; state: string }) {
  return (
    <>
      <Icon className={engineIconClass} aria-hidden="true" />
      <span
        className={cn(
          'absolute inset-x-2 bottom-0.5 h-0.5 rounded-full transition-colors duration-200',
          engineStateClass(state),
        )}
        aria-hidden="true"
      />
    </>
  );
}

const DOT: Record<BackendStage, string> = {
  setup_required: 'bg-warning',
  installing: 'bg-warning animate-pulse motion-reduce:animate-none',
  idle: 'bg-muted-foreground',
  attaching: 'bg-warning animate-pulse motion-reduce:animate-none',
  starting: 'bg-warning animate-pulse motion-reduce:animate-none',
  ready: 'bg-success',
  crashed: 'bg-destructive',
  port_in_use: 'bg-destructive',
  failed: 'bg-destructive',
};

function EngineTip({
  family,
  detail,
  title,
  runtime,
  problem,
  state,
  online,
}: {
  family: string;
  detail: string;
  title?: string | null;
  runtime?: string | null;
  problem?: string | null;
  state: string;
  online: boolean;
}) {
  const { t } = useTranslation();
  const shownState = online ? state : 'engineSidebar.offline';
  return (
    <div className="flex w-full min-w-0 max-w-[calc(100vw-2rem)] flex-col gap-2 py-1">
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold">{t('engineSidebar.' + family)}</span>
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <span
            aria-hidden="true"
            className={cn(
              'size-1.5 shrink-0 rounded-full',
              online ? engineStateClass(state) : 'bg-muted-foreground',
            )}
          />
          {t(shownState)}
        </span>
      </div>
      {title && title !== detail && <p className="text-sm font-medium leading-snug">{title}</p>}
      <div className="space-y-1 border-t border-border/60 pt-2">
        <p className="text-[10px] font-medium text-muted-foreground">
          {t('modelSettings.selected')}
        </p>
        <p className="break-words text-xs leading-relaxed [overflow-wrap:anywhere]">{detail}</p>
      </div>
      {runtime && (
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <ComputeVendorIcon runtime={runtime} className="size-3.5 shrink-0" />
          <span>{formatComputeRuntime(runtime)}</span>
        </div>
      )}
      {problem && (
        <p className="border-t border-border/60 pt-2 text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">
          {problem}
        </p>
      )}
      <p className="border-t border-border/60 pt-2 text-[10px] text-muted-foreground">
        {t('modelSettings.change')}
      </p>
    </div>
  );
}

export function StatusBar({
  compact = false,
  inline = false,
  footerLeading,
}: {
  compact?: boolean;
  inline?: boolean;
  footerLeading?: ReactNode;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [deviceOpen, setDeviceOpen] = useState(false);
  const status = useBackendStatus();
  const computeTarget = useComputeTarget(status.stage === 'ready');
  const activeComputeTarget = computeTarget.data?.active;
  const activeRemoteTarget = activeComputeTarget?.remote
    ? computeTarget.data?.targets.find((item) => item.id === activeComputeTarget.worker_id)
    : undefined;
  const tipAnchor = useRef<HTMLDivElement>(null);
  const activities = useAppActivities();
  const activityCount = Object.values(activities).reduce((total, count) => total + count, 0);
  const deviceUsage = useQuery({
    queryKey: ['sysinfo'],
    enabled: status.stage === 'ready' && deviceOpen && !activeComputeTarget?.remote,
    queryFn: ({ signal }) => apiJson<DeviceUsage>('/sysinfo', { signal }),
    refetchInterval: deviceOpen ? 2_000 : false,
  });
  const { data, isLoading: enginesLoading, isError: enginesError } = useEngines();
  const selectedTtsFamily = engineFamilyState(data, 'tts');
  const selectedTts = selectedTtsFamily?.backends.find(
    (engine) => engine.id === selectedTtsFamily.active,
  );
  const remoteTtsRuntime = useComputeRuntime(
    activeRemoteTarget?.id,
    selectedTts?.id,
    'tts',
    status.stage === 'ready' && Boolean(activeRemoteTarget),
    activityCount > 0,
  );
  const model = useQuery({
    queryKey: ['sidebar-model-status'],
    queryFn: () => apiJson<SidebarModelStatus>('/model/status'),
    enabled: status.stage === 'ready',
    refetchInterval: (query) => modelStatusPollMs(activityCount, query.state.data?.status),
  });
  const translation = useTranslationEngines();
  const selectedTranslation = translation.data?.engines.find(
    (engine) => engine.id === translation.data?.active,
  );
  const dictation = useDictationSelection();
  const modelCatalogue = useQuery({
    queryKey: ['model-catalogue'],
    enabled: status.stage === 'ready',
    staleTime: 30_000,
    queryFn: () =>
      apiJson<{
        models: {
          repo_id: string;
          label: string;
          role: string;
          installed: boolean;
        }[];
      }>('/models'),
  });
  const batchJobs = useQuery({
    queryKey: ['batch-jobs', 'active'],
    enabled: status.stage === 'ready',
    queryFn: ({ signal }) => apiJson<BatchJob[]>('/batch/jobs?status=active&limit=100', { signal }),
    staleTime: 1_000,
    refetchInterval: (query) => batchStatusPollMs(query.state.data?.length ?? 0),
  });
  const runningBatchStages = new Set(
    batchJobs.data
      ?.filter((job) => job.status === 'running')
      .map((job) => job.progress?.stage)
      .filter((stage): stage is string => Boolean(stage)),
  );
  const hasBatchWork = Boolean(batchJobs.data?.length);
  const batchAsrActive = runningBatchStages.has('transcribe');
  const batchTranslationActive = runningBatchStages.has('translate');
  const batchTtsActive = runningBatchStages.has('generate');
  const loadedModels = useQuery({
    queryKey: ['loaded-models'],
    enabled: status.stage === 'ready',
    staleTime: 5_000,
    refetchInterval: loadedModelsPollMs(activityCount > 0 || hasBatchWork),
    queryFn: () =>
      apiJson<{
        models: LoadedModelStatus[];
        count: number;
      }>('/model/loaded'),
  });
  const loadedCapture = loadedModels.data?.models.find((entry) => entry.id === 'capture-asr');
  const loadedTranslation = loadedModels.data?.models.find(
    (entry) => entry.id === `translation:${translation.data?.active}`,
  );
  const diarisationModel = modelCatalogue.data?.models.find(
    (entry) => entry.installed && ['diarisation', 'diarization'].includes(entry.role.toLowerCase()),
  );
  const loadedDiarisation = loadedModels.data?.models.find((entry) => entry.id === 'diarization');
  const diarisation = useQuery({
    queryKey: ['diarisation-status'],
    enabled: status.stage === 'ready',
    refetchInterval: IDLE_STATUS_POLL_MS,
    queryFn: () =>
      apiJson<{
        active: string;
        label: string;
        model: string | null;
        installed: boolean;
        loaded: boolean;
        busy?: boolean;
        reason: string | null;
      }>('/engines/diarisation'),
  });
  const rows = (['tts', 'asr', 'llm'] as const).map((family) => {
    const familyState = engineFamilyState(data, family);
    const selected = familyState?.backends.find((engine) => engine.id === familyState.active);
    const activeModel = familyState?.active_model;
    const resident = loadedModels.data?.models.find((entry) => {
      if (family === 'tts')
        return (
          (entry.id === 'tts' && selected?.id === 'omnivoice') || entry.is_active_engine === true
        );
      if (family === 'asr')
        return (
          (entry.id === 'asr' || entry.id === 'capture-asr') && entry.checkpoint === activeModel
        );
      return entry.id === family;
    });
    const remoteRuntime =
      family === 'tts' && activeRemoteTarget
        ? resolveRemoteRuntime(
            remoteTtsRuntime.data,
            remoteTtsRuntime.isPending,
            remoteTtsRuntime.isError,
            activities.synthesis > 0 || activities.longform > 0 || batchTtsActive,
          )
        : undefined;
    const remoteCapability = remoteRuntime?.capability;
    const remoteProblem =
      remoteRuntime?.state === 'unavailable'
        ? remoteTtsRuntime.data?.reason && remoteTtsRuntime.data.reason !== 'chosen'
          ? remoteTtsRuntime.data.reason
          : t('modelSettings.unavailable')
        : undefined;
    return {
      family,
      name: remoteCapability?.display_name || selected?.display_name,
      model: remoteCapability?.repo_ids?.[0] || remoteCapability?.model_id || activeModel,
      runtime:
        remoteCapability?.backend ||
        resident?.device ||
        selected?.execution_evidence?.actual_execution_device,
      problem: remoteProblem
        ? remoteProblem
        : family === 'tts' && selected?.id === 'omnivoice'
          ? model.data?.error || selected?.routing_reason || selected?.reason || selected?.hint
          : selected?.routing_reason || selected?.reason || selected?.hint,
      state: remoteRuntime
        ? remoteRuntime.state === 'checking'
          ? 'engineRuntime.loading'
          : remoteRuntime.state === 'working'
            ? 'engineRuntime.working'
            : remoteRuntime.state === 'ready'
              ? 'engineRuntime.ready'
              : remoteRuntime.state === 'idle'
                ? 'engineRuntime.idle'
                : 'modelSettings.unavailable'
        : enginesLoading && !data
          ? 'engineRuntime.loading'
          : !selected?.available
            ? 'modelSettings.unavailable'
            : family === 'llm' && selected.id === 'off'
              ? 'engineSidebar.inactive'
              : (family === 'tts' &&
                    (activities.synthesis > 0 || activities.longform > 0 || batchTtsActive)) ||
                  (family === 'asr' && (activities.transcription > 0 || batchAsrActive))
                ? family === 'tts' &&
                  selected.id === 'omnivoice' &&
                  model.data?.status === 'loading'
                  ? 'engineRuntime.loading'
                  : 'engineRuntime.working'
                : family === 'tts' &&
                    selected.id === 'omnivoice' &&
                    (model.isLoading || model.data?.status === 'loading')
                  ? 'engineRuntime.loading'
                  : family === 'tts' &&
                      selected.id === 'omnivoice' &&
                      (model.isError || model.data?.sub_stage === 'error' || model.data?.error)
                    ? 'modelSettings.unavailable'
                    : resident
                      ? 'engineRuntime.ready'
                      : 'engineRuntime.idle',
    };
  });
  const runtimeHealth = resolveRuntimeHealth(
    data,
    enginesLoading,
    enginesError,
    model.data,
    model.isLoading,
    model.isError,
  );
  const stageText =
    status.stage !== 'ready'
      ? status.stage === 'port_in_use'
        ? t('backend.port_in_use_short', { port: status.port })
        : t(`backend.${status.stage}`)
      : runtimeHealth === 'checking'
        ? t('preferences.loading')
        : runtimeHealth === 'unavailable'
          ? `${t('engineSidebar.tts')} · ${t('modelSettings.unavailable')}`
          : runtimeHealth === 'loading'
            ? t('engineRuntime.loading')
            : t('backend.ready');
  const stageDot =
    status.stage !== 'ready'
      ? DOT[status.stage]
      : runtimeHealth === 'ready'
        ? DOT.ready
        : runtimeHealth === 'unavailable'
          ? 'bg-destructive'
          : DOT.starting;
  const deviceDot = activeRemoteTarget
    ? activeRemoteTarget.status === 'ready'
      ? DOT.ready
      : activeRemoteTarget.status === 'busy'
        ? DOT.starting
        : 'bg-destructive'
    : stageDot;
  const deviceLabel = activeComputeTarget?.remote
    ? activeComputeTarget.label
    : status.remote
      ? t('settings.remote_backend_title')
      : t('engineSidebar.localDevice');
  const deviceStageText = activeRemoteTarget
    ? t(
        activeRemoteTarget.status === 'ready'
          ? 'engineRuntime.ready'
          : activeRemoteTarget.status === 'busy'
            ? 'engineRuntime.working'
            : 'engineSidebar.offline',
      )
    : stageText;
  const formatMemory = (used: number, total?: number) =>
    total && total > 0 ? `${used.toFixed(1)} / ${total.toFixed(1)} GB` : `${used.toFixed(1)} GB`;
  const deviceContent = (
    <PopoverContent
      side={compact ? 'right' : 'top'}
      align="start"
      className="w-[min(18rem,calc(100vw-2rem))] space-y-3 p-3"
    >
      <div className="flex items-center gap-2">
        <span className={cn('size-2 shrink-0 rounded-full', deviceDot)} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{deviceLabel}</p>
          <p className="truncate text-[11px] text-muted-foreground">{deviceStageText}</p>
        </div>
        <CpuIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </div>
      <ComputeTargetChoices data={computeTarget.data} />
      {activeRemoteTarget ? (
        <div className="space-y-1 rounded-lg border border-border/55 bg-muted/20 px-3 py-2 text-xs">
          <p className="truncate text-foreground/85" title={activeRemoteTarget.endpoint}>
            {activeRemoteTarget.endpoint}
          </p>
          <div className="flex items-center justify-between gap-3 tabular-nums text-muted-foreground">
            <span>
              {activeRemoteTarget.latency_ms > 0
                ? `${Math.round(activeRemoteTarget.latency_ms)} ms`
                : deviceStageText}
            </span>
            <span>
              {activeRemoteTarget.active_tasks}/{activeRemoteTarget.max_tasks}
            </span>
          </div>
        </div>
      ) : deviceUsage.isError ? (
        <button
          type="button"
          onClick={() => void deviceUsage.refetch()}
          className="w-full rounded-lg border border-border/55 bg-muted/20 px-3 py-2 text-left text-xs text-muted-foreground outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t('common.retry')}
        </button>
      ) : deviceUsage.data ? (
        <div className="space-y-3 rounded-lg border border-border/55 bg-muted/20 p-2.5">
          <DeviceMetric
            Icon={CpuIcon}
            label={t('settings.device_family_cpu')}
            value={`${Math.round(deviceUsage.data.cpu)}%`}
            percent={deviceUsage.data.cpu}
            detail={deviceUsage.data.cpu_model}
            meta={[
              deviceUsage.data.cpu_physical_cores
                ? `${deviceUsage.data.cpu_physical_cores}C`
                : null,
              deviceUsage.data.cpu_logical_cores ? `${deviceUsage.data.cpu_logical_cores}T` : null,
              deviceUsage.data.cpu_frequency_ghz
                ? `${deviceUsage.data.cpu_frequency_ghz.toFixed(1)} GHz`
                : null,
            ]
              .filter(Boolean)
              .join(' / ')}
          />
          <DeviceMetric
            Icon={MemoryStickIcon}
            label={t('about.ram')}
            value={formatMemory(deviceUsage.data.ram, deviceUsage.data.total_ram)}
            percent={boundedPercent(deviceUsage.data.ram, deviceUsage.data.total_ram)}
          />
          <DeviceMetric
            Icon={MonitorUpIcon}
            label={t('settings.device_family_gpu')}
            value={
              deviceUsage.data.gpu_utilization == null
                ? t(deviceUsage.data.gpu_active ? 'about.yes' : 'about.no')
                : `${Math.round(deviceUsage.data.gpu_utilization)}%`
            }
            percent={
              deviceUsage.data.gpu_utilization ??
              (deviceUsage.data.total_vram > 0
                ? boundedPercent(deviceUsage.data.vram, deviceUsage.data.total_vram)
                : deviceUsage.data.gpu_active
                  ? 100
                  : 0)
            }
            detail={deviceUsage.data.gpu_name}
            meta={formatMemory(deviceUsage.data.vram, deviceUsage.data.total_vram)}
          />
        </div>
      ) : (
        <div
          className="space-y-2 rounded-lg border border-border/55 bg-muted/20 p-2.5"
          aria-label={t('preferences.loading')}
        >
          {[72, 88, 64].map((width) => (
            <div
              key={width}
              className="h-3 animate-pulse rounded bg-muted"
              style={{ width: `${width}%` }}
            />
          ))}
        </div>
      )}
      <Link
        to="/settings/workers"
        onClick={() => setDeviceOpen(false)}
        className="group/remote flex items-center gap-2 rounded-lg border border-border/55 px-2.5 py-2 text-xs font-medium outline-none transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <MonitorUpIcon className="size-4 shrink-0 text-primary" aria-hidden="true" />
        <span className="min-w-0 flex-1">{t('engineSidebar.connectRemoteDevice')}</span>
        <ChevronRightIcon
          className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover/remote:translate-x-0.5 motion-reduce:transition-none"
          aria-hidden="true"
        />
      </Link>
    </PopoverContent>
  );
  const engines = [
    ...rows.map((row) => ({
      family: row.family,
      Icon: ENGINE_ICONS[row.family],
      detail:
        row.model ||
        row.name ||
        t(enginesLoading && !data ? 'preferences.loading' : 'engineSidebar.inactive'),
      title: row.name,
      problem: row.problem,
      runtime: row.runtime,
      state: row.state,
    })),
    {
      family: 'translation',
      Icon: LanguagesIcon,
      detail:
        selectedTranslation?.display_name ??
        t(translation.isPending ? 'preferences.loading' : 'engineSidebar.inactive'),
      title: selectedTranslation?.display_name,
      problem:
        (selectedTranslation?.ready ?? selectedTranslation?.installed)
          ? undefined
          : selectedTranslation?.availability_reason,
      runtime: loadedTranslation?.device,
      state: translation.isPending
        ? 'engineRuntime.loading'
        : translation.isError
          ? 'modelSettings.unavailable'
          : (selectedTranslation?.ready ?? selectedTranslation?.installed)
            ? activities.translation > 0 || batchTranslationActive
              ? 'engineRuntime.working'
              : loadedTranslation
                ? 'engineRuntime.ready'
                : 'engineRuntime.idle'
            : 'engineSidebar.inactive',
    },
    {
      family: 'dictation',
      Icon: KeyboardIcon,
      detail:
        dictation.data?.model?.label ??
        t(dictation.isPending ? 'preferences.loading' : 'engineSidebar.inactive'),
      title: dictation.data?.model?.label,
      problem: undefined,
      runtime:
        loadedCapture?.checkpoint === dictation.data?.model_id ? loadedCapture?.device : undefined,
      state: dictation.isPending
        ? 'engineRuntime.loading'
        : dictation.isError
          ? 'modelSettings.unavailable'
          : dictation.data?.enabled && dictation.data.available && dictation.data.model?.installed
            ? activities.dictation > 0
              ? 'engineRuntime.working'
              : loadedCapture?.checkpoint === dictation.data?.model_id
                ? 'engineRuntime.ready'
                : 'engineRuntime.idle'
            : 'engineSidebar.inactive',
    },
    {
      family: 'diarisation',
      Icon: UsersRoundIcon,
      detail:
        diarisation.data?.model ||
        diarisation.data?.label ||
        loadedDiarisation?.checkpoint ||
        diarisationModel?.label ||
        t(modelCatalogue.isLoading ? 'preferences.loading' : 'engineSidebar.inactive'),
      title: diarisation.data?.label || diarisationModel?.label,
      problem: diarisation.data?.reason || undefined,
      runtime:
        diarisation.data?.active === 'audiocpp-sortformer' ? undefined : loadedDiarisation?.device,
      state: diarisation.isPending
        ? 'engineRuntime.loading'
        : diarisation.isError
          ? 'modelSettings.unavailable'
          : diarisation.data?.busy
            ? 'engineRuntime.working'
            : (diarisation.data?.loaded ?? Boolean(loadedDiarisation))
              ? 'engineRuntime.ready'
              : (diarisation.data?.installed ?? Boolean(diarisationModel))
                ? 'engineRuntime.idle'
                : modelCatalogue.isLoading
                  ? 'engineRuntime.loading'
                  : 'modelSettings.unavailable',
    },
  ];
  const iconDevicePopover = (
    <Popover open={deviceOpen} onOpenChange={setDeviceOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={`${deviceLabel}: ${deviceStageText}`}
            className={cn(engineLinkClass, 'h-7 w-full', !compact && 'justify-start gap-2 px-2')}
          />
        }
      >
        <CpuIcon className={engineIconClass} aria-hidden="true" />
        {!compact && <span className="min-w-0 truncate">{deviceLabel}</span>}
        <span
          className={cn(
            compact
              ? 'absolute inset-x-2 bottom-0.5 h-0.5 rounded-full'
              : 'ml-auto size-1.5 shrink-0 rounded-full',
            deviceDot,
          )}
          aria-hidden="true"
        />
      </PopoverTrigger>
      {deviceContent}
    </Popover>
  );
  if (compact) {
    return (
      <footer
        className={cn(
          'shrink-0 text-muted-foreground',
          inline ? 'contents' : 'border-t border-border/50 px-1.5 py-2',
        )}
      >
        {iconDevicePopover}
      </footer>
    );
  }
  return (
    <footer className="@container/engines w-full min-w-0 max-w-full border-t border-border/50 px-3 py-1.5 text-[length:var(--text-caption)] text-muted-foreground">
      <div>
        {!footerLeading && (
          <div className="flex items-center gap-0.5">
            <Popover open={deviceOpen} onOpenChange={setDeviceOpen}>
              <PopoverTrigger
                render={
                  <button
                    type="button"
                    aria-label={`${deviceLabel}: ${deviceStageText}`}
                    className="group/status flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1 text-left outline-none transition-[background-color,box-shadow,backdrop-filter] duration-150 hover:bg-sidebar-accent/65 hover:backdrop-blur-xl hover:shadow-[inset_0_1px_0_rgb(255_255_255/8%),0_5px_14px_rgb(0_0_0/10%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/60 focus-visible:ring-2 focus-visible:ring-ring"
                  />
                }
              >
                <span
                  className={cn('size-1.5 shrink-0 rounded-full', deviceDot)}
                  aria-hidden="true"
                />
                <CpuIcon className="size-3.5 shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate" role="status">
                  {deviceLabel}
                </span>
              </PopoverTrigger>
              {deviceContent}
            </Popover>
            <button
              type="button"
              aria-label={expanded ? t('paneActions.collapse') : t('modelSettings.models')}
              aria-expanded={expanded}
              aria-controls="sidebar-engine-details"
              onClick={() => setExpanded((value) => !value)}
              className="flex size-7 shrink-0 items-center justify-center rounded-md outline-none transition-colors hover:bg-sidebar-accent/65 focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronDownIcon
                className={cn(
                  'size-3.5 transition-transform duration-150 motion-reduce:transition-none',
                  expanded && 'rotate-180',
                )}
                aria-hidden="true"
              />
            </button>
          </div>
        )}
        <div
          ref={tipAnchor}
          className="mt-0.5 grid w-full min-w-0 grid-cols-6 gap-1 rounded-lg border border-border/50 bg-sidebar-accent/25 p-1"
        >
          {engines.map(({ family, Icon, detail, title, problem, runtime, state }) => (
            <Tooltip key={family}>
              <TooltipTrigger
                render={
                  <Link
                    to="/settings/models/$family"
                    params={{ family }}
                    aria-label={t('modelSettings.change') + ' ' + t('engineSidebar.' + family)}
                    className={engineLinkClass}
                  />
                }
              >
                <EngineIconStatus Icon={Icon} state={state} />
              </TooltipTrigger>
              <TooltipContent
                surface="theme"
                anchor={tipAnchor}
                showArrow={false}
                side="top"
                align="start"
                sideOffset={8}
                className="w-[var(--anchor-width)] max-w-none flex-col items-start gap-1"
              >
                <EngineTip
                  family={family}
                  detail={detail}
                  title={title}
                  runtime={runtime}
                  problem={problem}
                  state={state}
                  online={status.stage === 'ready'}
                />
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
        <div
          id="sidebar-engine-details"
          hidden={status.stage !== 'ready' || !expanded}
          className="mt-1.5 space-y-0.5"
        >
          {status.stage === 'ready' &&
            engines.map(({ family, Icon, detail, title, problem, runtime, state }) => (
              <Link
                key={family}
                to="/settings/models/$family"
                params={{ family }}
                aria-label={t('modelSettings.change') + ' ' + t('engineSidebar.' + family)}
                title={[title, detail, runtime, problem, t(state)].filter(Boolean).join(' \u00b7 ')}
                className="group/engine flex min-w-0 items-center gap-2 rounded-md px-2 py-1 outline-none transition-[background-color,box-shadow,backdrop-filter] duration-150 hover:bg-sidebar-accent/65 hover:backdrop-blur-xl hover:shadow-[inset_0_1px_0_rgb(255_255_255/8%),0_5px_14px_rgb(0_0_0/10%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/60 focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Icon
                  className="size-3.5 shrink-0 transition-filter duration-150 group-hover/engine:drop-shadow-[0_1px_3px_rgb(0_0_0/20%)]"
                  aria-hidden="true"
                />
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <p className="min-w-0 basis-[45%] shrink-0 truncate font-medium text-foreground/85">
                    {t('engineSidebar.' + family)}
                  </p>
                  <p className="min-w-0 flex-1 truncate">{detail}</p>
                </div>
                <span
                  className={cn('size-1.5 shrink-0 rounded-full', engineStateClass(state))}
                  title={t(state)}
                  aria-hidden="true"
                />
                <ArrowLeftRightIcon
                  className="size-3.5 shrink-0 opacity-50 transition-opacity duration-150 group-hover/engine:opacity-100"
                  aria-hidden="true"
                />
              </Link>
            ))}
        </div>
        {status.stage === 'ready' && (
          <div className="mt-1.5">
            <PerformanceProfile tooltipAnchor={tipAnchor} />
          </div>
        )}
        {footerLeading && (
          <div className="mt-1.5 flex items-center gap-1 border-t border-border/50 pt-1.5">
            {footerLeading}
            <div className="min-w-0 flex-1">{iconDevicePopover}</div>
            <button
              type="button"
              aria-label={expanded ? t('paneActions.collapse') : t('modelSettings.models')}
              aria-expanded={expanded}
              aria-controls="sidebar-engine-details"
              onClick={() => setExpanded((value) => !value)}
              className="ml-auto flex size-7 shrink-0 items-center justify-center rounded-md outline-none transition-colors hover:bg-sidebar-accent/65 focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronDownIcon
                className={cn(
                  'size-3.5 transition-transform duration-150 motion-reduce:transition-none',
                  expanded && 'rotate-180',
                )}
                aria-hidden="true"
              />
            </button>
          </div>
        )}
      </div>
    </footer>
  );
}
