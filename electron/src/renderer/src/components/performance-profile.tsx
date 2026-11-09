import { Link } from '@tanstack/react-router';
import {
  CrownIcon,
  GaugeIcon,
  SparkleIcon,
  SparklesIcon,
  WandSparklesIcon,
  CircleCheckIcon,
  CircleGaugeIcon,
  CircleHelpIcon,
  ChevronRightIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useId, useRef, useState, type ReactNode } from 'react';
import { Slider } from '@base-ui/react/slider';
import { DirectionProvider } from '@base-ui/react/direction-provider';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { apiJson, describeError } from '@/lib/api/client';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { useDictationSelection } from '@/hooks/use-dictation-selection';
import { engineFamilyState, useEngines } from '@/hooks/use-engines';
import { Button, buttonVariants } from '@/components/ui/button';
import { useAppActivities } from '@/lib/app-activity';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { LiveDeviceUsage } from '@/components/live-device-usage';
import { cn } from '@/lib/utils';
import {
  performanceTiers,
  usePerformanceProfile,
  type PerformanceFamily,
  type PerformanceChoice,
  type PerformanceProfileState,
} from '@/hooks/use-performance-profile';

export function PerformanceProfile({
  family = null,
  variant = 'compact',
  showHardware = true,
  extra,
  onApplied,
}: {
  family?: PerformanceFamily | null;
  variant?: 'compact' | 'settings';
  /** The hardware-fit row under the compact slider; the simplest sidebar view leaves it out. */
  showHardware?: boolean;
  /** Compact sidebar: show only the slider, and keep this (engines) plus the hardware fit in a popover. */
  extra?: ReactNode;
  onApplied?: (state: PerformanceProfileState) => void;
}) {
  const { t, i18n } = useTranslation();
  const profile = usePerformanceProfile();
  const activities = useAppActivities();
  const backend = useBackendStatus();
  const engines = useEngines();
  const dictation = useDictationSelection();
  const batch = useQuery({
    queryKey: ['batch-jobs', 'active'],
    enabled: isBackendReachable(backend.stage),
    queryFn: ({ signal }) => apiJson<unknown[]>('/batch/jobs?status=active&limit=100', { signal }),
    staleTime: 1_000,
    refetchInterval: (query) => (query.state.data?.length ? 1_000 : 15_000),
  });
  const [failed, setFailed] = useState<PerformanceChoice | null>(null);
  const groupId = useId();
  const [draft, setDraft] = useState<number | null>(null);
  const [hardwareOpen, setHardwareOpen] = useState(false);
  const compactControl = useRef<HTMLDivElement>(null);
  const busy =
    profile.isSaving ||
    !isBackendReachable(backend.stage) ||
    batch.isPending ||
    batch.isError ||
    Boolean(batch.data?.length) ||
    Object.values(activities).some((count) => count > 0);
  const choices: readonly PerformanceChoice[] = family
    ? performanceTiers
    : [...performanceTiers, 'auto'];
  const popoverMode = extra != null && !family && variant !== 'settings';
  const selected = family ? profile.data?.effective[family] : profile.data?.global;
  const applicable = profile.data?.applicable_families ?? profile.data?.implemented_families ?? [];
  const supported = family === null ? true : applicable.includes(family);
  const disabled = busy || !profile.data || !supported;
  const selectedIndex = selected ? choices.indexOf(selected) : -1;
  const position = draft ?? Math.max(0, selectedIndex);
  const accent = [
    'var(--muted-foreground)',
    'color-mix(in oklab, var(--primary) 72%, var(--foreground))',
    'var(--primary)',
    'color-mix(in oklab, var(--primary) 82%, white)',
  ][Math.min(position, 3)];
  const choose = async (tier: PerformanceChoice) => {
    if (disabled) {
      setDraft(null);
      return;
    }
    setDraft(choices.indexOf(tier));
    setFailed(null);
    if (popoverMode) setHardwareOpen(true);
    try {
      const applied = await profile.setTier({ tier, family });
      onApplied?.(applied);
      toast.success(
        t('performanceProfile.applied', {
          tier: t('performanceProfile.' + tier),
        }),
      );
    } catch (error) {
      setFailed(tier);
      toast.error(describeError(error));
    } finally {
      setDraft(null);
    }
  };
  const plan = profile.data?.plan;
  const hardwareSummary = profile.isSaving
    ? t('common.saving')
    : plan
      ? t('performanceHardware.' + (plan.status === 'limited' ? 'adjusted' : plan.status))
      : '';
  const selection = family ? profile.data?.selections?.[family] : null;
  const target = family ? profile.data?.targets?.[family] : null;
  const legacyFamily =
    family === 'tts' || family === 'asr' || family === 'llm'
      ? engineFamilyState(engines.data, family)
      : null;
  const legacyModel =
    family === 'dictation'
      ? dictation.data?.model?.label || dictation.data?.model_id
      : legacyFamily?.active_model || target?.model || target?.engine || legacyFamily?.active;
  const selectedModel = selection?.label || selection?.model || selection?.engine || legacyModel;
  const selectedEngine = selection?.engine || legacyFamily?.active || target?.engine;
  const requiredEngine = target?.engine
    ? {
        'faster-whisper': 'Faster-Whisper',
        'sherpa-onnx': 'Sherpa-ONNX',
        pyannote: 'pyannote',
        'audiocpp-sortformer': 'Sortformer',
        nllb: 'NLLB-200',
      }[target.engine] || target.engine
    : null;
  const targetMetric =
    family === 'tts' && target?.steps
      ? `${target.steps} ${t('clone.steps')}`
      : family === 'asr' && target?.beam_size
        ? `×${target.beam_size}`
        : family === 'dictation' && target?.max_active_paths
          ? `×${target.max_active_paths}`
          : family === 'translation' && target?.num_beams
            ? `×${target.num_beams}`
            : null;
  if (variant === 'settings') {
    return (
      <div className="w-full min-w-0 @2xl:w-[min(100%,34rem)]">
        <div
          role="radiogroup"
          aria-labelledby={groupId}
          aria-busy={profile.isSaving}
          className={cn(
            'grid min-w-0 grid-cols-2 gap-1 rounded-xl border border-border/60 bg-muted/45 p-1 sm:grid-flow-col sm:auto-cols-fr',
            disabled && 'opacity-50',
          )}
        >
          <span id={groupId} className="sr-only">
            {t(supported ? 'performanceProfile.title' : 'modelSettings.unavailable')}
          </span>
          {choices.map((tier, index) => {
            const Icon = [GaugeIcon, SparkleIcon, SparklesIcon, CrownIcon, WandSparklesIcon][index];
            const active = supported && position === index;
            return (
              <button
                key={tier}
                type="button"
                role="radio"
                aria-checked={active}
                data-selected={active}
                disabled={disabled}
                onClick={() => void choose(tier)}
                className={cn(
                  'performance-tier-button inline-flex h-9 min-w-0 items-center justify-center gap-1.5 rounded-lg px-2 text-xs font-medium text-muted-foreground outline-none transition-[background-color,color,box-shadow] duration-150 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 motion-reduce:transition-none',
                  active && 'text-foreground',
                )}
              >
                <Icon
                  className="size-3.5 shrink-0"
                  style={active ? { color: accent } : undefined}
                  aria-hidden="true"
                />
                <span className="truncate">{t('performanceProfile.' + tier)}</span>
              </button>
            );
          })}
        </div>
        {family && (
          <div className="mt-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 px-1 text-xs text-muted-foreground">
            {supported && selectedModel ? (
              <>
                <span className="truncate font-medium text-foreground/80">{selectedModel}</span>
                {selectedEngine && selectedEngine !== selectedModel && (
                  <span className="truncate">{selectedEngine}</span>
                )}
                {targetMetric && <span className="shrink-0 tabular-nums">{targetMetric}</span>}
              </>
            ) : (
              <>
                <span>
                  {requiredEngine
                    ? t('performanceProfile.requiresEngine', { engine: requiredEngine })
                    : t('performanceProfile.noTarget')}
                </span>
                <Link
                  to="/settings/models/$family"
                  params={{ family }}
                  className={buttonVariants({ variant: 'ghost', size: 'xs' })}
                >
                  {t('modelSettings.models')}
                </Link>
              </>
            )}
          </div>
        )}
        {(failed || profile.isError || batch.isError) && (
          <div role="alert" className="mt-1 text-xs text-destructive">
            {t('common.error')}
            <Button
              size="xs"
              variant="ghost"
              disabled={profile.isSaving}
              onClick={() => {
                if (batch.isError) void batch.refetch();
                else if (failed && !busy) void choose(failed);
                else void profile.refetch();
              }}
            >
              {t('common.retry')}
            </Button>
          </div>
        )}
      </div>
    );
  }
  const autoOn = selected === 'auto';
  const tiers = performanceTiers;
  // Auto is a mode, not a fifth notch: show the tier it resolved to as a hint.
  const shownTier = draft != null ? choices[draft] : autoOn ? plan?.resolved : selected;
  const HardwareIcon =
    plan?.status === 'fits'
      ? CircleCheckIcon
      : plan?.status === 'unknown'
        ? CircleHelpIcon
        : CircleGaugeIcon;
  const sliderTier = (shownTier === 'auto' ? plan?.resolved : shownTier) ?? 'balanced';
  const sliderPosition = Math.max(0, tiers.indexOf(sliderTier as (typeof tiers)[number]));
  const TierIcon = [GaugeIcon, SparkleIcon, SparklesIcon, CrownIcon][Math.min(3, sliderPosition)];
  const panel = (
    <PopoverContent
      side="inline-end"
      anchor={popoverMode ? compactControl : undefined}
      className="max-h-[min(42rem,80dvh)] w-80 max-w-[calc(100vw-1rem)] space-y-3 overflow-y-auto p-4 text-xs"
    >
      {extra}
      {plan && !family && (
        <>
          <div>
            <p className="font-medium">
              {t('performanceProfile.max') +
                ' · ' +
                t(
                  'performanceHardware.' +
                    (plan.max_status === 'limited' ? 'reason.memory' : plan.max_status),
                )}
            </p>
            <p className="mt-1 text-muted-foreground">{t('performanceHardware.voiceFirst')}</p>
          </div>
          <LiveDeviceUsage open={hardwareOpen} />
          <div className="-mx-1">
            {(['tts', 'asr', 'translation', 'dictation', 'diarisation'] as const).map((name) => {
              const entry = plan.families[name];
              return (
                <Link
                  key={name}
                  to="/settings/models/$family"
                  params={{ family: name }}
                  className="group flex min-h-7 items-center justify-between gap-3 rounded-md px-1 py-1 outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span>{t('sidebarTools.' + name)}</span>
                  <span className="flex items-center gap-1.5 text-end text-[11px] text-muted-foreground">
                    {t('performanceHardware.reason.' + entry.reason)}
                    <ChevronRightIcon
                      className="size-3 shrink-0 opacity-50 group-hover:opacity-100 rtl:rotate-180"
                      aria-hidden="true"
                    />
                  </span>
                </Link>
              );
            })}
          </div>
          <details className="group/details border-t pt-1 text-muted-foreground">
            <summary className="flex min-h-8 cursor-pointer list-none items-center justify-between gap-2 rounded outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
              {t('sidebarTools.details')}
              <ChevronRightIcon
                className="size-3.5 shrink-0 group-open/details:rotate-90 rtl:rotate-180"
                aria-hidden="true"
              />
            </summary>
            <div className="space-y-2 pt-1 leading-relaxed">
              <p className="text-foreground/80 tabular-nums">
                {t('performanceHardware.specs', {
                  ram:
                    plan.hardware.ram_gb?.toLocaleString(i18n.language, {
                      maximumFractionDigits: 0,
                    }) ?? '—',
                  cores: plan.hardware.cpu_threads,
                })}
                {plan.hardware.vram_gb != null &&
                  ' · ' +
                    t('performanceHardware.vram', {
                      memory: plan.hardware.vram_gb.toLocaleString(i18n.language, {
                        maximumFractionDigits: 0,
                      }),
                    })}
              </p>
              <p>{t('performanceHardware.priority')}</p>
              <p>{t('performanceHardware.estimate')}</p>
            </div>
          </details>
        </>
      )}
    </PopoverContent>
  );
  const tierName = shownTier && shownTier !== 'auto' ? t('performanceProfile.' + shownTier) : null;
  // The current level, beside the slider it reads from. In the sidebar it also opens the details.
  const tierLabel = (
    <>
      <TierIcon className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
      <span className="min-w-0 truncate">{tierName ?? t('performanceProfile.auto')}</span>
    </>
  );
  const body = (
    <div ref={compactControl} data-slot="performance-control" className="min-w-0 space-y-1 py-1.5">
      {/* One labelled row: the control's name, then the Auto mode it can hand over to. */}
      <div className="flex min-h-6 items-center justify-between gap-2 ps-1">
        <span
          id={groupId}
          className="min-w-0 truncate text-[11px] font-medium whitespace-nowrap text-muted-foreground"
        >
          {t(supported ? 'performanceProfile.title' : 'modelSettings.unavailable')}
        </span>
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                type="button"
                data-slot="performance-auto"
                aria-pressed={autoOn}
                aria-describedby={groupId + '-auto'}
                disabled={disabled}
                onClick={() => void choose(autoOn ? (plan?.resolved ?? 'balanced') : 'auto')}
                className={cn(
                  'inline-flex h-6 shrink-0 items-center justify-center gap-1 rounded-md px-1.5 text-[11px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
                  autoOn
                    ? 'bg-primary/12 text-primary'
                    : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
                )}
              />
            }
          >
            <WandSparklesIcon className="size-3" aria-hidden="true" />
            {t('performanceProfile.auto')}
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-56">
            {t('performanceProfile.autoHint')}
          </TooltipContent>
        </Tooltip>
        <span id={groupId + '-auto'} className="sr-only">
          {t('performanceProfile.autoHint')}
        </span>
      </div>
      <div className="flex min-w-0 items-center gap-2 ps-1">
        <DirectionProvider direction={i18n.dir(i18n.language)}>
          <Slider.Root
            min={0}
            max={tiers.length - 1}
            step={1}
            largeStep={1}
            value={sliderPosition}
            disabled={disabled}
            aria-busy={profile.isSaving}
            onValueChange={(value) => setDraft(value)}
            onValueCommitted={(value) => void choose(tiers[value])}
            className={cn('min-w-14 flex-1', disabled && 'opacity-50', autoOn && 'opacity-60')}
          >
            <Slider.Control className="relative flex h-6 w-full min-w-0 touch-none items-center select-none">
              <Slider.Track className="relative h-1 w-full min-w-0 rounded-full bg-sidebar-accent">
                <Slider.Indicator className="rounded-full bg-primary" />
                {/* One tick per tier, so the steps read without labels. */}
                {tiers.map((tier, index) => (
                  <span
                    key={tier}
                    aria-hidden="true"
                    className="pointer-events-none absolute top-1/2 size-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-sidebar-foreground/30 rtl:translate-x-1/2"
                    style={{
                      insetInlineStart: `${(index / Math.max(tiers.length - 1, 1)) * 100}%`,
                    }}
                  />
                ))}
                <Slider.Thumb
                  aria-labelledby={groupId}
                  aria-describedby={groupId + '-help'}
                  getAriaValueText={(_formatted, value) => t('performanceProfile.' + tiers[value])}
                  className="size-3.5 rounded-full bg-primary shadow-[0_1px_4px_rgb(0_0_0/30%)] ring-2 ring-sidebar outline-none focus-within:ring-4 focus-within:ring-ring/40"
                />
              </Slider.Track>
            </Slider.Control>
          </Slider.Root>
        </DirectionProvider>
        {popoverMode ? (
          <PopoverTrigger
            openOnHover
            delay={200}
            render={
              <button
                type="button"
                data-slot="performance-trigger"
                aria-label={
                  hardwareSummary
                    ? `${tierName ?? t('performanceProfile.auto')} · ${hardwareSummary}`
                    : undefined
                }
                className="group/hw flex h-6 max-w-[60%] min-w-0 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium whitespace-nowrap text-foreground/85 outline-none hover:bg-sidebar-accent/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              />
            }
          >
            {tierLabel}
            <ChevronRightIcon
              className="size-3 shrink-0 opacity-50 transition-transform group-hover/hw:translate-x-0.5 rtl:rotate-180 motion-reduce:transition-none"
              aria-hidden="true"
            />
          </PopoverTrigger>
        ) : (
          supported && (
            <span className="flex h-6 max-w-[60%] min-w-0 shrink-0 items-center gap-1 px-1.5 text-xs font-medium whitespace-nowrap text-foreground/85">
              {tierLabel}
            </span>
          )
        )}
      </div>
      {plan && !family && showHardware && (
        <Popover open={hardwareOpen} onOpenChange={setHardwareOpen}>
          <PopoverTrigger
            render={
              <button
                type="button"
                aria-label={hardwareSummary}
                className="group/hw flex min-h-6 w-full items-center gap-1.5 rounded px-1 text-start text-[11px] text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              />
            }
          >
            <HardwareIcon
              className={cn(
                'size-3 shrink-0',
                plan.status === 'fits'
                  ? 'text-success'
                  : plan.status !== 'unknown' && 'text-warning',
              )}
              aria-hidden="true"
            />
            <span className="min-w-0 flex-1 truncate" role="status">
              {hardwareSummary}
            </span>
            <ChevronRightIcon
              className="size-3 shrink-0 opacity-60 transition-transform group-hover/hw:translate-x-0.5 rtl:rotate-180 motion-reduce:transition-none"
              aria-hidden="true"
            />
          </PopoverTrigger>
          {panel}
        </Popover>
      )}
      <p id={groupId + '-help'} className="sr-only">
        {t(
          !supported
            ? 'performanceProfile.noTarget'
            : busy
              ? 'sidebarTools.wait'
              : 'sidebarTools.speedHelp',
        )}
      </p>
      {(failed || profile.isError || batch.isError) && (
        <div role="alert" className="text-xs text-destructive">
          {t('common.error')}
          <Button
            size="xs"
            variant="ghost"
            disabled={profile.isSaving}
            onClick={() => {
              if (batch.isError) void batch.refetch();
              else if (failed && !busy) void choose(failed);
              else void profile.refetch();
            }}
          >
            {t('common.retry')}
          </Button>
        </div>
      )}
    </div>
  );
  return popoverMode ? (
    <Popover
      open={hardwareOpen}
      onOpenChange={(open) => {
        // A save in flight keeps its feedback visible until it settles.
        if (!open && profile.isSaving) return;
        setHardwareOpen(open);
      }}
    >
      {body}
      {panel}
    </Popover>
  ) : (
    body
  );
}
