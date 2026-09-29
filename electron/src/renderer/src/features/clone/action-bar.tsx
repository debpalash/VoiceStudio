import { PipelineFailure } from '@/components/pipeline-failure';
import { openTake } from '@/lib/store/takes';
import { setWorkspace } from '@/lib/store/workspace';
import { Progress as ProgressPrimitive } from '@base-ui/react/progress';
import {
  AudioLinesIcon,
  ChevronDownIcon,
  ClockIcon,
  FocusIcon,
  GaugeIcon,
  LayersIcon,
  PlayIcon,
  LoaderCircleIcon,
  RotateCcwIcon,
  Settings2Icon,
  ShuffleIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  ThermometerIcon,
  TimerIcon,
  XIcon,
  type LucideIcon,
} from 'lucide-react';
import { useId, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { Button, buttonVariants } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { Input } from '@/components/ui/input';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
import { isMac } from '@/components/bridge';
import { Label } from '@/components/ui/label';
import { ProgressIndicator, ProgressTrack } from '@/components/ui/progress';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { useGenerateClone } from '@/hooks/use-generate';
import {
  resetVoiceControls,
  setCloneSetting,
  useCloneSettings,
  type CloneSettings,
} from '@/lib/store/clone-settings';
import { cn } from '@/lib/utils';
import { EngineLanguagePicker } from './engine-language-picker';
import { QualityControls } from './quality-controls';
import { CloneDemoAction } from './clone-demo';
import { useCloneDemo } from '@/hooks/use-clone-demo';

type NumericKey = 'steps' | 'cfg' | 'speed' | 'tShift' | 'posTemp' | 'classTemp' | 'layerPenalty';

interface SliderSpec {
  key: NumericKey;
  labelKey: string;
  icon: LucideIcon;
  min: number;
  max: number;
  step: number;
  suffix?: string;
}

const SLIDERS: SliderSpec[] = [
  {
    key: 'steps',
    labelKey: 'clone.steps',
    icon: SlidersHorizontalIcon,
    min: 8,
    max: 64,
    step: 1,
  },
  {
    key: 'cfg',
    labelKey: 'voiceControls.guidance',
    icon: FocusIcon,
    min: 1,
    max: 4,
    step: 0.1,
  },
  {
    key: 'speed',
    labelKey: 'clone.speed',
    icon: GaugeIcon,
    min: 0.5,
    max: 2,
    step: 0.1,
    suffix: '×',
  },
  {
    key: 'tShift',
    labelKey: 'voiceControls.timing',
    icon: TimerIcon,
    min: 0,
    max: 1,
    step: 0.05,
  },
  {
    key: 'posTemp',
    labelKey: 'voiceControls.order',
    icon: ThermometerIcon,
    min: 0,
    max: 10,
    step: 0.5,
  },
  {
    key: 'classTemp',
    labelKey: 'voiceControls.variation',
    icon: ShuffleIcon,
    min: 0,
    max: 2,
    step: 0.1,
  },
  {
    key: 'layerPenalty',
    labelKey: 'voiceControls.balance',
    icon: LayersIcon,
    min: 0,
    max: 10,
    step: 0.5,
  },
];

function decimals(step: number): number {
  const text = String(step);
  const dot = text.indexOf('.');
  return dot === -1 ? 0 : text.length - dot - 1;
}

interface SliderRowProps {
  spec: SliderSpec;
  value: number;
}

function SliderRow({ spec, value }: SliderRowProps) {
  const { t } = useTranslation();
  const labelId = useId();
  const Icon = spec.icon;
  return (
    <div className="flex min-w-0 flex-col gap-2 px-1 py-2">
      <div className="flex items-center justify-between gap-2">
        <span
          id={labelId}
          className="inline-flex items-center gap-1.5 text-xs font-medium"
        >
          <Icon className="size-3.5 text-muted-foreground" aria-hidden="true" />
          {t(spec.labelKey)}
        </span>
        <output className="text-xs text-primary tabular-nums">
          {value.toFixed(decimals(spec.step))}
          {spec.suffix ?? ''}
        </output>
      </div>
      <Slider
        thumbProps={{ 'aria-labelledby': labelId }}
        min={spec.min}
        max={spec.max}
        step={spec.step}
        value={[value]}
        onValueChange={(next) => {
          const n = Array.isArray(next) ? next[0] : next;
          if (typeof n === 'number') setCloneSetting(spec.key, n);
        }}
      />
    </div>
  );
}

interface SwitchRowProps {
  settingKey: 'denoise' | 'postprocess';
  labelKey: string;
  icon: LucideIcon;
  checked: boolean;
}

function SwitchRow({ settingKey, labelKey, icon: Icon, checked }: SwitchRowProps) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div className="flex items-center justify-between gap-3 px-1 py-1.5">
      <Label htmlFor={id} className="gap-1.5 text-xs font-medium">
        <Icon className="size-3.5 text-muted-foreground" aria-hidden="true" />
        {t(labelKey)}
      </Label>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={(next) => setCloneSetting(settingKey, next)}
      />
    </div>
  );
}

interface OverridesProps {
  settings: CloneSettings;
}

function Overrides({ settings }: OverridesProps) {
  const { t } = useTranslation();
  const durationId = useId();
  return (
    <div className="flex flex-col">
      <div className="space-y-1 pb-3">
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,150px),1fr))] gap-x-4">
          {SLIDERS.filter((spec) => spec.key === 'speed').map((spec) => (
            <SliderRow key={spec.key} spec={spec} value={settings[spec.key]} />
          ))}
          <div className="flex min-w-0 flex-col gap-2 px-1 py-2">
            <Label htmlFor={durationId} className="gap-1.5 text-xs font-medium">
              <ClockIcon className="size-3.5 text-muted-foreground" aria-hidden="true" />
              {t('clone.duration')}
            </Label>
            <Input
              id={durationId}
              inputMode="decimal"
              value={settings.duration}
              onChange={(event) => setCloneSetting('duration', event.target.value)}
              placeholder={t('clone.auto')}
              className="h-7 text-xs tabular-nums"
            />
          </div>
        </div>
        <p className="px-1 text-[11px] text-muted-foreground">{t('voiceControls.basics')}</p>
      </div>
      <div className="border-t border-border/50 py-1.5">
        <SwitchRow
          settingKey="denoise"
          labelKey="voiceControls.denoise"
          icon={AudioLinesIcon}
          checked={settings.denoise}
        />
        <SwitchRow
          settingKey="postprocess"
          labelKey="voiceControls.polish"
          icon={SparklesIcon}
          checked={settings.postprocess}
        />
      </div>
      <details className="group border-t border-border/50 pt-2.5">
        <summary className="flex cursor-pointer list-none items-center justify-between rounded px-1 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          {t('voiceControls.advanced')}
          <ChevronDownIcon
            className="size-3.5 transition-transform group-open:rotate-180"
            aria-hidden="true"
          />
        </summary>
        <p className="mt-2 px-1 text-[11px] text-muted-foreground">{t('voiceControls.hint')}</p>
        <div className="mt-1 grid gap-x-4 sm:grid-cols-2">
          {SLIDERS.filter((spec) => spec.key !== 'speed' && spec.key !== 'steps').map((spec) => (
            <SliderRow key={spec.key} spec={spec} value={settings[spec.key]} />
          ))}
        </div>
      </details>
    </div>
  );
}

function ResetVoiceControls() {
  const { t } = useTranslation();
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      aria-label={t('clone.reset_overrides')}
      title={t('clone.reset_overrides')}
      onClick={resetVoiceControls}
    >
      <RotateCcwIcon aria-hidden="true" />
    </Button>
  );
}

export function ProductionSettings() {
  const settings = useCloneSettings();
  return (
    <div className="pt-3">
      <Overrides settings={settings} />
      <div className="flex justify-end pt-2">
        <ResetVoiceControls />
      </div>
    </div>
  );
}

/** Composer button that opens voice controls in the same popover style as audio quality. */
export function VoiceControls({ size = 'icon-lg' }: { size?: 'icon-sm' | 'icon-lg' }) {
  const { t } = useTranslation();
  const settings = useCloneSettings();
  const id = useId();
  // Local, not persisted: a remembered open state would pop the panel over other pages.
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={t('clone.production_overrides')}
        className={cn(
          buttonVariants({ variant: 'ghost', size }),
          'text-muted-foreground hover:text-foreground data-popup-open:bg-secondary data-popup-open:text-foreground',
        )}
      >
        <Settings2Icon />
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={10}
        aria-labelledby={id}
        className="flex max-h-[min(640px,var(--available-height))] w-[min(420px,calc(100vw-32px))] flex-col p-0"
      >
        <header className="flex shrink-0 items-center justify-between gap-3 px-4 pt-3 pb-1">
          <h3 id={id} className="text-sm font-semibold">
            {t('clone.production_overrides')}
          </h3>
          <ResetVoiceControls />
        </header>
        <div className="min-h-0 overflow-y-auto overscroll-contain px-3 pb-3">
          <Overrides settings={settings} />
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function ActionBar() {
  const { t } = useTranslation();
  const demo = useCloneDemo();
  const mac = isMac();
  const readinessId = useId();
  const {
    generate,
    cancel,
    isGenerating,
    elapsedSeconds,
    progress,
    stage,
    modelStage,
    modelProgress,
    error,
    clearError,
    cloneBlocker: blocker,
  } = useGenerateClone();
  const generationLabel = isGenerating
    ? t(
        stage === 'loading'
          ? modelStage
            ? `synthesisState.${modelStage}`
            : 'synthesisState.loading'
          : stage === 'receiving'
            ? 'synthesisState.receiving'
            : stage === 'preparing'
              ? 'synthesisState.preparing'
              : 'clone.generating_status',
      )
    : t('clone.synthesize');

  return (
    <section className="@container/composer">
      {blocker && !isGenerating && (
        <div
          id={readinessId}
          className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1 text-sm text-muted-foreground"
        >
          <span role="status">
            {t(
              blocker === 'cloning'
                ? 'convert.cloning_required'
                : blocker === 'engine'
                ? demo
                  ? 'demo.prerendered_chip'
                  : 'engines.none_ready_title'
                : blocker === 'reference'
                  ? 'tts_errors.upload_or_select'
                  : blocker === 'text'
                    ? 'tts_errors.enter_text'
                    : 'preferences.loading',
            )}
          </span>
          {blocker === 'reference' && (
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="xs"
                onClick={() => {
                  openTake(null);
                  setWorkspace({ panel: 'voice' });
                }}
              >
                {t('clone.reference_audio')}
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setWorkspace({ libraryOpen: true, libraryTab: 'voices' })}
              >
                {t('clone.saved_profiles')}
              </Button>
            </div>
          )}
          {(blocker === 'engine' || blocker === 'cloning') && !demo && (
            <Link
              to="/settings/models/$family"
              params={{ family: 'tts' }}
              className={buttonVariants({ variant: 'ghost', size: 'xs' })}
            >
              {t('engineSidebar.tts')}
            </Link>
          )}
        </div>
      )}
      {error && <PipelineFailure className="mb-3" fallback={error} onDismiss={clearError} />}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-lg bg-background/50 p-1 ring-1 ring-border/50">
          <EngineLanguagePicker />
          <span aria-hidden="true" className="mx-1 h-4 w-px bg-border" />
          <QualityControls disabled={isGenerating} />
          <VoiceControls />
        </div>

        <div className="relative ml-auto flex shrink-0 gap-2">
          {demo ? (
            <CloneDemoAction />
          ) : (
            <Button
              size="lg"
              className="h-10 w-60 shrink-0 justify-between overflow-hidden rounded-lg ps-4 pe-2.5 shadow-sm transition-colors"
              onClick={() => void generate()}
              disabled={isGenerating || blocker !== null}
              aria-describedby={blocker ? readinessId : undefined}
              aria-busy={isGenerating}
              aria-label={generationLabel}
              aria-keyshortcuts="Control+Enter Meta+Enter"
            >
              <span className="flex min-w-0 items-center gap-2">
                {isGenerating ? (
                  <LoaderCircleIcon className="shrink-0 animate-spin motion-reduce:animate-none" />
                ) : (
                  <PlayIcon className="shrink-0" />
                )}
                <span className="min-w-0 truncate" role={isGenerating ? 'status' : undefined}>
                  {generationLabel}
                </span>
              </span>
              {isGenerating ? (
                <span className="shrink-0 text-xs font-normal tabular-nums opacity-80">
                  {`${modelProgress != null && stage === 'loading' ? `${Math.round(modelProgress)}% · ` : ''}${elapsedSeconds.toFixed(1)}s`}
                </span>
              ) : (
                <KbdGroup aria-hidden="true" className="shrink-0">
                  {[mac ? '⌘' : 'Ctrl', '↵'].map((key) => (
                    <Kbd
                      key={key}
                      className="h-5 min-w-5 rounded-[5px] bg-primary-foreground/15 px-1 text-[11px] text-primary-foreground/85 shadow-[inset_0_-1px_0_rgb(0_0_0/18%)]"
                    >
                      {key}
                    </Kbd>
                  ))}
                </KbdGroup>
              )}
            </Button>
          )}
          {isGenerating && !demo ? (
            <Button
              size="icon-lg"
              className="size-10 shrink-0"
              variant="outline"
              onClick={() => cancel()}
              aria-label={t('clone.cancel_generation')}
            >
              <XIcon data-icon="inline-start" />
            </Button>
          ) : null}
          {isGenerating ? (
            <ProgressPrimitive.Root
              value={stage === 'loading' ? modelProgress : progress}
              aria-label={t('clone.generating_status')}
              className="absolute inset-x-1 -bottom-2"
            >
              <ProgressTrack>
                <ProgressIndicator
                  className={cn(
                    (stage === 'loading' ? modelProgress : progress) == null &&
                      'w-full animate-pulse motion-reduce:animate-none',
                  )}
                />
              </ProgressTrack>
            </ProgressPrimitive.Root>
          ) : null}
        </div>
      </div>
    </section>
  );
}
