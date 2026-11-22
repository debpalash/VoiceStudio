import { useId } from 'react';
import { PilcrowIcon, ScissorsIcon, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { setCloneSetting, type CloneSettings } from '@/lib/store/clone-settings';

export type NumericKey =
  | 'cfg'
  | 'speed'
  | 'tShift'
  | 'posTemp'
  | 'classTemp'
  | 'layerPenalty'
  | 'paragraphGapMs';

export interface SliderSpec {
  key: NumericKey;
  labelKey: string;
  icon: LucideIcon;
  min: number;
  max: number;
  step: number;
  suffix?: string;
}

function decimals(step: number): number {
  const text = String(step);
  const dot = text.indexOf('.');
  return dot === -1 ? 0 : text.length - dot - 1;
}

interface SliderRowProps {
  spec: SliderSpec;
  value: number;
}

export function SliderRow({ spec, value }: SliderRowProps) {
  const { t } = useTranslation();
  const labelId = useId();
  const Icon = spec.icon;
  return (
    <div className="flex min-w-0 flex-col gap-2 px-1 py-2">
      <div className="flex items-center justify-between gap-2">
        <span id={labelId} className="inline-flex items-center gap-1.5 text-xs font-medium">
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
  settingKey: 'denoise' | 'postprocess' | 'trimEngineSilence';
  labelKey: string;
  icon: LucideIcon;
  checked: boolean;
}

export function SwitchRow({ settingKey, labelKey, icon: Icon, checked }: SwitchRowProps) {
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

/** Silence between paragraphs, as Audiobook's join control. */
const GAP_SLIDERS: SliderSpec[] = [
  {
    key: 'paragraphGapMs',
    labelKey: 'audiobook.paragraph_gap',
    icon: PilcrowIcon,
    min: 0,
    max: 3000,
    step: 50,
    suffix: ' ms',
  },
];

interface JoinControlsProps {
  settings: CloneSettings;
}

/** Paragraph gap and engine-silence trim: Audiobook's join controls for Generate. */
export function JoinControls({ settings }: JoinControlsProps) {
  const { t } = useTranslation();
  return (
    <div className="border-t border-border/50 py-1.5">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,150px),1fr))] gap-x-4">
        {GAP_SLIDERS.map((spec) => (
          <SliderRow key={spec.key} spec={spec} value={settings[spec.key]} />
        ))}
      </div>
      <SwitchRow
        settingKey="trimEngineSilence"
        labelKey="audiobook.trim_edges"
        icon={ScissorsIcon}
        checked={settings.trimEngineSilence}
      />
      <p className="px-1 text-[11px] text-muted-foreground">{t('voiceControls.pauses_hint')}</p>
    </div>
  );
}
