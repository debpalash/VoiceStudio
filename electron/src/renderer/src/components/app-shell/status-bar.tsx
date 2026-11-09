import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  usePerformanceProfile,
  type PerformanceProfileState,
} from '@/hooks/use-performance-profile';
import { presetEngineFeedback } from './preset-engine-feedback';
import {
  engineDetailLevels,
  useEngineDetailLevel,
  type EngineDetailLevel,
} from './use-engine-detail-level';
import { EngineRow } from './engine-row';
import { useTranslationEngines } from '@/features/settings/translation-settings';
import {
  LanguagesIcon,
  AudioLinesIcon,
  MicIcon,
  BrainCircuitIcon,
  KeyboardIcon,
  UsersRoundIcon,
  ListIcon,
  LayersIcon,
  CodeXmlIcon,
  CpuIcon,
  GaugeIcon,
  MemoryStickIcon,
  MonitorUpIcon,
} from 'lucide-react';
import { useIsFetching, useQuery } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { useTranslation } from 'react-i18next';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { engineFamilyState, useEngines } from '@/hooks/use-engines';
import { useDeviceUsage } from '@/hooks/use-device-usage';
import { useDictationSelection } from '@/hooks/use-dictation-selection';
import { cn } from '@/lib/utils';
import { useAppActivities } from '@/lib/app-activity';
import {
  isResidentAsrModel,
  resolveRuntimeHealth,
  type SidebarModelStatus,
} from './status-runtime';
import { PerformanceProfile } from '@/components/performance-profile';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import type { BatchJob } from '@shared/api/batch-types';
import {
  IDLE_STATUS_POLL_MS,
  batchStatusPollMs,
  loadedModelsPollMs,
  modelStatusPollMs,
  relaxWhenBackendBusy,
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

const DOT: Record<BackendStage, string> = {
  setup_required: 'bg-warning',
  installing: 'bg-warning animate-pulse motion-reduce:animate-none',
  idle: 'bg-muted-foreground',
  attaching: 'bg-warning animate-pulse motion-reduce:animate-none',
  starting: 'bg-warning animate-pulse motion-reduce:animate-none',
  ready: 'bg-success',
  // Alive but busy (#2430): transient and self-recovering, so it pulses as a
  // warning rather than sitting on the app as a destructive red failure.
  unresponsive: 'bg-warning animate-pulse motion-reduce:animate-none',
  crashed: 'bg-destructive',
  port_in_use: 'bg-destructive',
  failed: 'bg-destructive',
};

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
  const { level, chooseLevel } = useEngineDetailLevel();
  const [deviceOpen, setDeviceOpen] = useState(false);
  const profile = usePerformanceProfile();
  const [appliedProfile, setAppliedProfile] = useState<PerformanceProfileState | null>(null);
  const enginesRefreshing = useIsFetching({ queryKey: ['engines'] }) > 0;
  const status = useBackendStatus();
  const [selectedDetail, setSelectedDetail] = useState<string | null>('tts');
  const activities = useAppActivities();
  const activityCount = Object.values(activities).reduce((total, count) => total + count, 0);
  const deviceUsage = useDeviceUsage(deviceOpen);
  const { data, isLoading: enginesLoading, isError: enginesError } = useEngines();
  const model = useQuery({
    queryKey: ['sidebar-model-status'],
    queryFn: () => apiJson<SidebarModelStatus>('/model/status'),
    enabled: isBackendReachable(status.stage),
    refetchInterval: (query) => modelStatusPollMs(activityCount, query.state.data?.status),
  });
  const translation = useTranslationEngines();
  const selectedTranslation = translation.data?.engines.find(
    (engine) => engine.id === translation.data?.active,
  );
  const dictation = useDictationSelection();
  const modelCatalogue = useQuery({
    queryKey: ['model-catalogue'],
    enabled: isBackendReachable(status.stage),
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
    enabled: isBackendReachable(status.stage),
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
    enabled: isBackendReachable(status.stage),
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
    enabled: isBackendReachable(status.stage),
    refetchInterval: () => relaxWhenBackendBusy(IDLE_STATUS_POLL_MS),
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
      if (family === 'asr') return isResidentAsrModel(entry.id) && entry.checkpoint === activeModel;
      return entry.id === family;
    });
    return {
      family,
      name: selected?.display_name,
      model: activeModel,
      runtime: resident?.device || selected?.execution_evidence?.actual_execution_device,
      problem:
        family === 'tts' && selected?.id === 'omnivoice'
          ? model.data?.error || selected?.routing_reason || selected?.reason || selected?.hint
          : selected?.routing_reason || selected?.reason || selected?.hint,
      state:
        enginesLoading && !data
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
        : status.diagnosis === 'remote_unreachable'
          ? t('backend.unresponsive_remote')
          : status.diagnosis === 'unhealthy'
            ? t('backend.unhealthy')
            : status.diagnosis === 'auth_required'
              ? t('backend.auth_required')
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
  const deviceLabel = status.remote
    ? t('settings.remote_backend_title')
    : t('engineSidebar.localShort');
  const formatMemory = (used: number, total?: number) =>
    total && total > 0 ? `${used.toFixed(1)} / ${total.toFixed(1)} GB` : `${used.toFixed(1)} GB`;
  const deviceContent = (
    <PopoverContent
      side={compact ? 'right' : 'top'}
      align="start"
      className="w-[min(18rem,calc(100vw-2rem))] space-y-3 p-3"
    >
      <div className="flex items-center gap-2">
        <span className={cn('size-2 shrink-0 rounded-full', stageDot)} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{deviceLabel}</p>
          <p className="truncate text-[11px] text-muted-foreground">{stageText}</p>
        </div>
        <CpuIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </div>
      {deviceUsage.isError ? (
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
  const refreshing = {
    tts: enginesRefreshing,
    asr: enginesRefreshing,
    llm: enginesRefreshing,
    translation: translation.isFetching,
    dictation: dictation.isFetching,
    diarisation: diarisation.isFetching,
  };
  const presetRefreshing = Object.values(refreshing).some(Boolean);
  const appliedRefreshStarted = useRef(false);
  useEffect(() => {
    if (!appliedProfile) return;
    if (presetRefreshing) appliedRefreshStarted.current = true;
    else if (appliedRefreshStarted.current) {
      appliedRefreshStarted.current = false;
      setAppliedProfile(null);
    }
  }, [presetRefreshing, appliedProfile]);
  const applicable = profile.data?.applicable_families ?? profile.data?.implemented_families ?? [];
  const displayEngines = engines.map((row) =>
    presetEngineFeedback(
      row,
      appliedProfile,
      refreshing[row.family as keyof typeof refreshing],
      profile.isSaving,
      applicable,
    ),
  );
  const engineRows = (rowLevel: EngineDetailLevel) =>
    displayEngines
      .filter(({ family }) => rowLevel !== 'simple' || family === 'tts')
      .map((row) => (
        <EngineRow
          key={row.family}
          row={row}
          level={rowLevel}
          online={isBackendReachable(status.stage)}
          dotClass={engineStateClass(row.state)}
          open={selectedDetail === row.family}
          onToggle={() =>
            setSelectedDetail((current) => (current === row.family ? null : row.family))
          }
        />
      ));
  const viewControl = (
    <div
      role="radiogroup"
      data-slot="engine-view-toggle"
      aria-label={t('sidebarTools.title')}
      className="flex shrink-0 items-center gap-0.5 rounded-md bg-sidebar-accent/35 p-0.5 ring-1 ring-inset ring-sidebar-border/50"
    >
      {engineDetailLevels.map((value) => {
        const label = t(value === 'models' ? 'modelSettings.models' : 'sidebarTools.' + value);
        const Icon = value === 'simple' ? ListIcon : value === 'models' ? LayersIcon : CodeXmlIcon;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={level === value}
            aria-label={label}
            title={label}
            onClick={() => chooseLevel(value)}
            className={cn(
              'grid size-6 place-items-center rounded-[5px] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
              level === value
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
  // The simplest view is icon-only: the status dot and tooltip carry what a label did.
  const deviceIconOnly = level === 'simple';
  const handleApplied = (applied: PerformanceProfileState) => {
    appliedRefreshStarted.current = presetRefreshing;
    setAppliedProfile(applied);
  };
  // The icon rail has no room for the slider, so the gauge opens it as a popover.
  const railProfile = (
    <Popover>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={t('performanceProfile.title')}
            title={t('performanceProfile.title')}
            // In the macOS rail's footer column the gauge leads, above Settings.
            className={cn(engineLinkClass, inline ? 'order-first size-8' : 'h-7 w-full')}
          />
        }
      >
        <GaugeIcon className={engineIconClass} aria-hidden="true" />
      </PopoverTrigger>
      <PopoverContent
        side="right"
        align="end"
        className="max-h-[min(36rem,80dvh)] w-72 space-y-2 overflow-y-auto p-3 text-xs"
      >
        {isBackendReachable(status.stage) && (
          <PerformanceProfile showHardware={false} onApplied={handleApplied} />
        )}
        {engineRows('models')}
      </PopoverContent>
    </Popover>
  );
  const iconDevicePopover = (
    <Popover open={deviceOpen} onOpenChange={setDeviceOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={`${deviceLabel}: ${stageText}`}
            className={cn(
              engineLinkClass,
              inline ? 'relative size-8' : 'relative h-7 w-full',
              !compact && !deviceIconOnly && 'justify-start gap-2 px-2',
            )}
          />
        }
      >
        <CpuIcon className={engineIconClass} aria-hidden="true" />
        {!compact && !deviceIconOnly && <span className="min-w-0 truncate">{deviceLabel}</span>}
        <span
          className={cn(
            compact
              ? 'absolute inset-x-2 bottom-0.5 h-0.5 rounded-full'
              : deviceIconOnly
                ? 'absolute end-1.5 top-1 size-1.5 rounded-full'
                : 'ml-auto size-1.5 shrink-0 rounded-full',
            stageDot,
          )}
          aria-hidden="true"
        />
      </PopoverTrigger>
      {deviceContent}
    </Popover>
  );
  // Status dot, chip and device name: the readable form, used wherever there is room for text.
  const labeledDevicePopover = (
    <Popover open={deviceOpen} onOpenChange={setDeviceOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={`${deviceLabel}: ${stageText}`}
            title={`${deviceLabel}: ${stageText}`}
            className="group/status flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 text-left outline-none transition-[background-color,box-shadow,backdrop-filter] duration-150 hover:bg-sidebar-accent/65 hover:backdrop-blur-xl hover:shadow-[inset_0_1px_0_rgb(255_255_255/8%),0_5px_14px_rgb(0_0_0/10%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/60 focus-visible:ring-2 focus-visible:ring-ring"
          />
        }
      >
        {/* Status dot as a badge on the chip, so the icon lines up with the
            rows below it. */}
        <span className="relative shrink-0" aria-hidden="true">
          <CpuIcon className="size-3.5" />
          <span
            className={cn(
              'absolute -end-0.5 -bottom-0.5 size-1.5 rounded-full ring-2 ring-sidebar',
              stageDot,
            )}
          />
        </span>
        <span className="min-w-0 flex-1 truncate" role="status">
          {deviceLabel}
        </span>
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
        {railProfile}
        {iconDevicePopover}
      </footer>
    );
  }
  return (
    <footer className="@container/engines max-h-[55dvh] w-full min-w-0 max-w-full overflow-y-auto overscroll-contain border-t border-border/50 px-3 py-1.5 text-[length:var(--text-caption)] text-muted-foreground">
      <div>
        {!footerLeading && (
          <div className="flex items-center gap-0.5">
            {labeledDevicePopover}
            {level !== 'simple' && viewControl}
          </div>
        )}
        {isBackendReachable(status.stage) && (
          <PerformanceProfile
            showHardware={level !== 'simple'}
            extra={
              level === 'simple' ? (
                <>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-muted-foreground">{t('sidebarTools.title')}</span>
                    {viewControl}
                  </div>
                  {engineRows('models')}
                </>
              ) : undefined
            }
            onApplied={handleApplied}
          />
        )}
        {level !== 'simple' && (
          <div
            id="sidebar-engine-details"
            data-detail-level={level}
            className="max-h-[32dvh] min-w-0 space-y-0.5 overflow-y-auto overscroll-contain"
          >
            {/* With the device in the footer beside Settings there is no room
                for the view switch there, so it heads the rows it controls. */}
            {footerLeading && <div className="flex justify-end">{viewControl}</div>}
            {engineRows(level)}
          </div>
        )}

        {footerLeading && (
          <div className="sticky bottom-0 mt-1.5 flex items-center gap-1 border-t border-border/50 bg-sidebar pt-1.5">
            {footerLeading}
            {/* Device beside Settings; on a narrow pane Settings drops to its
                icon and the device name truncates, so neither is pushed out. */}
            <div className="flex min-w-0 flex-1 items-center gap-0.5">{labeledDevicePopover}</div>
          </div>
        )}
      </div>
    </footer>
  );
}
