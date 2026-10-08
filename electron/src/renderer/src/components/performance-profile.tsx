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
import { useId, useRef, useState, type KeyboardEvent } from 'react';
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
  onApplied,
}: {
  family?: PerformanceFamily | null;
  variant?: 'compact' | 'settings';
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
  const tierRefs = useRef<(HTMLButtonElement | null)[]>([]);
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
  const moveTier = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = Math.max(0, tiers.indexOf((shownTier ?? 'balanced') as (typeof tiers)[number]));
    const forward = i18n.dir(i18n.language) === 'rtl' ? 'ArrowLeft' : 'ArrowRight';
    const back = forward === 'ArrowRight' ? 'ArrowLeft' : 'ArrowRight';
    const next =
      event.key === forward || event.key === 'ArrowDown'
        ? current + 1
        : event.key === back || event.key === 'ArrowUp'
          ? current - 1
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? tiers.length - 1
              : null;
    if (next == null || disabled) return;
    event.preventDefault();
    const tier = tiers[Math.min(tiers.length - 1, Math.max(0, next))];
    tierRefs.current[tiers.indexOf(tier)]?.focus();
    void choose(tier);
  };
  return (
    <div className="min-w-0 space-y-1.5 py-1.5">
      <div className="flex items-center justify-between gap-2 px-1">
        <span id={groupId} className="text-[11px] text-muted-foreground">
          {t(supported ? 'performanceProfile.title' : 'modelSettings.unavailable')}
        </span>
        <button
          type="button"
          aria-pressed={autoOn}
          disabled={disabled}
          onClick={() => void choose(autoOn ? (plan?.resolved ?? 'balanced') : 'auto')}
          className={cn(
            'inline-flex h-6 items-center gap-1 rounded-full px-2 text-[11px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
            autoOn
              ? 'bg-primary/15 text-primary ring-1 ring-inset ring-primary/30'
              : 'text-muted-foreground ring-1 ring-inset ring-border/60 hover:text-foreground',
          )}
        >
          <WandSparklesIcon className="size-3" aria-hidden="true" />
          {t('performanceProfile.auto')}
        </button>
      </div>
      <div
        role="radiogroup"
        aria-labelledby={groupId}
        aria-describedby={groupId + '-help'}
        aria-busy={profile.isSaving}
        onKeyDown={moveTier}
        className={cn(
          'flex gap-0.5 rounded-lg bg-sidebar-accent/35 p-0.5 ring-1 ring-inset ring-sidebar-border/50',
          disabled && 'opacity-50',
        )}
      >
        {tiers.map((tier, index) => {
          const picked = shownTier === tier;
          const checked = !autoOn && picked;
          return (
            <button
              key={tier}
              ref={(node) => {
                tierRefs.current[index] = node;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={picked || (!shownTier && index === 1) ? 0 : -1}
              disabled={disabled}
              onClick={() => void choose(tier)}
              className={cn(
                'h-7 min-w-0 flex-auto truncate rounded-md px-1.5 text-[11px] font-medium outline-none transition-[background-color,color,box-shadow] duration-150 focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none',
                checked
                  ? 'bg-background text-foreground shadow-sm ring-1 ring-border/70'
                  : picked
                    ? 'text-foreground outline-1 -outline-offset-1 outline-primary/50 outline-dashed'
                    : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {t('performanceProfile.' + tier)}
            </button>
          );
        })}
      </div>
      {plan && !family && (
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
                plan.status === 'fits' ? 'text-success' : plan.status !== 'unknown' && 'text-warning',
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
          <PopoverContent
            side="right"
            className="max-h-[min(42rem,80dvh)] w-80 max-w-[calc(100vw-1rem)] space-y-3 overflow-y-auto p-4 text-xs"
          >
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
          </PopoverContent>
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
}
