import { useId, useRef, type KeyboardEvent } from 'react';
import { AudioWaveformIcon, CheckIcon, ChevronDownIcon, RotateCcwIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { Button, buttonVariants } from '@/components/ui/button';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { useEngines } from '@/hooks/use-engines';
import { effectiveSamplingSteps, samplingStepRange } from '@/lib/audio/quality';
import { resetAudioQuality, setCloneSetting, useCloneSettings } from '@/lib/store/clone-settings';
import { cn } from '@/lib/utils';

const precisions = [16, 24, 32] as const;

/** Composer pill that opens the next take's export quality, refinement and mastering. */
export function QualityControls({
  disabled = false,
  size = 'lg',
}: {
  disabled?: boolean;
  size?: 'sm' | 'lg';
}) {
  const { t } = useTranslation();
  const settings = useCloneSettings();
  const { activeTts } = useEngines();
  const id = useId();
  const presetRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const bits = settings.wavBits;
  const rate = activeTts?.output_sample_rate;
  const channels = activeTts?.output_channels;
  const estimatedSize =
    rate && rate > 0 && channels && channels > 0
      ? ((rate * channels * 60 * bits) / 8 / 1000000).toFixed(1)
      : null;
  const stepRange = samplingStepRange(activeTts?.id);
  const steps = effectiveSamplingSteps(settings.steps, activeTts?.id);
  const mastering = settings.effectPreset === 'broadcast';

  const choose = (index: number) => {
    const next = (index + precisions.length) % precisions.length;
    setCloneSetting('wavBits', precisions[next]);
    presetRefs.current[next]?.focus();
  };
  const onPresetKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    const current = precisions.indexOf(bits);
    const moves: Record<string, number> = {
      ArrowRight: current + 1,
      ArrowDown: current + 1,
      ArrowLeft: current - 1,
      ArrowUp: current - 1,
      Home: 0,
      End: precisions.length - 1,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    choose(moves[event.key]);
  };

  return (
    <Popover>
      <PopoverTrigger
        aria-label={`${t('cloneQuality.title')}: ${t(`cloneQuality.preset${bits}`)}`}
        className={cn(
          buttonVariants({ variant: 'ghost', size }),
          'gap-1.5 font-normal text-muted-foreground hover:text-foreground data-popup-open:bg-secondary data-popup-open:text-foreground',
        )}
      >
        <AudioWaveformIcon aria-hidden="true" />
        <span className="max-w-28 truncate">{t(`cloneQuality.preset${bits}`)}</span>
        {mastering && (
          <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-primary" />
        )}
        <ChevronDownIcon aria-hidden="true" className="size-3 opacity-70" />
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={10}
        aria-labelledby={id}
        className="w-[min(360px,calc(100vw-32px))] p-0"
      >
        <header className="flex items-center justify-between gap-3 px-4 pt-3.5 pb-2">
          <h3 id={id} className="text-sm font-semibold">
            {t('cloneQuality.title')}
          </h3>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground">{t('cloneQuality.nextTake')}</span>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t('clone.reset_overrides')}
              title={t('clone.reset_overrides')}
              disabled={disabled}
              onClick={resetAudioQuality}
            >
              <RotateCcwIcon aria-hidden="true" />
            </Button>
          </div>
        </header>

        <div className="px-3 pb-3">
          <div
            role="radiogroup"
            aria-labelledby={id}
            aria-describedby={`${id}-size`}
            aria-disabled={disabled || undefined}
            onKeyDown={onPresetKey}
            className="grid grid-cols-3 gap-1 rounded-lg bg-muted/40 p-1 ring-1 ring-border/50"
          >
            {precisions.map((value, index) => {
              const selected = value === bits;
              return (
                <button
                  key={value}
                  ref={(node) => {
                    presetRefs.current[index] = node;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  tabIndex={selected ? 0 : -1}
                  disabled={disabled}
                  onClick={() => choose(index)}
                  className={cn(
                    'relative flex min-w-0 flex-col items-start gap-0.5 rounded-md px-2.5 py-2 text-left outline-none transition-[background-color,box-shadow,color] duration-150 focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:opacity-60',
                    selected
                      ? 'bg-background text-foreground shadow-sm ring-1 ring-border'
                      : 'text-muted-foreground hover:bg-background/60 hover:text-foreground',
                  )}
                >
                  <span className="flex w-full items-center justify-between gap-1 text-xs font-medium">
                    <span className="truncate">{t(`cloneQuality.preset${value}`)}</span>
                    {selected && <CheckIcon aria-hidden="true" className="size-3 shrink-0 text-primary" />}
                  </span>
                  <span className="truncate font-mono text-[10px] tabular-nums text-muted-foreground">
                    {t(`cloneQuality.bits${value}`)}
                  </span>
                </button>
              );
            })}
          </div>
          <p className="mt-2 px-1 text-xs text-muted-foreground">{t(`cloneQuality.hint${bits}`)}</p>
          <p id={`${id}-size`} className="mt-0.5 px-1 text-[11px] text-muted-foreground/80 tabular-nums">
            {estimatedSize
              ? t('cloneQuality.size', { size: estimatedSize })
              : t('cloneQuality.sizeUnknown')}
          </p>
        </div>

        <div className="space-y-4 border-t border-border/50 px-4 py-3.5">
          {stepRange && (
            <div className="space-y-2">
              <div className="flex justify-between gap-2 text-xs">
                <span id={`${id}-steps`} className="font-medium">
                  {t('cloneQuality.effort')}
                </span>
                <output className="text-primary tabular-nums">{steps}</output>
              </div>
              <Slider
                thumbProps={{
                  'aria-labelledby': `${id}-steps`,
                  'aria-describedby': `${id}-effort`,
                }}
                min={stepRange[0]}
                max={stepRange[1]}
                step={1}
                value={[steps]}
                disabled={disabled}
                onValueChange={(value) =>
                  setCloneSetting('steps', Array.isArray(value) ? value[0] : value)
                }
              />
              <p id={`${id}-effort`} className="text-[11px] text-muted-foreground">
                {t('cloneQuality.effortHint')}
              </p>
            </div>
          )}
          <div className="flex items-start justify-between gap-3 text-xs">
            <span>
              <span className="font-medium">{t('cloneQuality.mastering')}</span>
              <span className="mt-0.5 block text-[11px] text-muted-foreground">
                {t('cloneQuality.masteringHint')}
              </span>
            </span>
            <Switch
              aria-label={t('cloneQuality.mastering')}
              checked={mastering}
              disabled={disabled}
              onCheckedChange={(checked) =>
                setCloneSetting('effectPreset', checked ? 'broadcast' : 'raw')
              }
            />
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
