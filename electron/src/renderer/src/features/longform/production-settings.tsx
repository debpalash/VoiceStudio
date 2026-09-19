import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { apiJson } from '@/lib/api/client';
import {
  DEFAULT_OVERRIDES,
  type Overrides,
} from '../../../../../../frontend/src/utils/longformOverrides';
const knobs = [
  { key: 'numStep', label: 'steps', min: 8, max: 64, step: 1, value: 32 },
  { key: 'guidanceScale', label: 'cfg', min: 0, max: 4, step: 0.1, value: 2 },
  { key: 'posTemp', label: 'pos_temp', min: 0, max: 10, step: 0.5, value: 5 },
  { key: 'classTemp', label: 'class_temp', min: 0, max: 2, step: 0.1, value: 0 },
] as const;
// Seamless joins: engine padding is trimmed at every line/paragraph edge and
// these deliberate gaps go in instead. Server defaults shown when unset.
const gaps = [
  { key: 'lineGapMs', label: 'line_gap', max: 2000, value: 250 },
  { key: 'paragraphGapMs', label: 'paragraph_gap', max: 3000, value: 350 },
] as const;
export function ProductionSettings({
  value,
  disabled,
  onChange,
}: {
  value: Overrides;
  disabled: boolean;
  onChange: (value: Overrides) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const engines = useQuery({
    queryKey: ['longform-tts-capabilities'],
    queryFn: ({ signal }) =>
      apiJson<{ active: string; backends: { id: string; supports_emotion?: boolean }[] }>(
        '/engines/tts',
        { signal },
      ),
    enabled: open,
  });
  const emotion = engines.data?.backends.find(
    (engine) => engine.id === engines.data?.active,
  )?.supports_emotion;
  return (
    <details className="space-y-4" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer text-sm font-medium">{t('audiobook.expressive')}</summary>
      {knobs.map((knob) => (
        <label key={knob.key} className="block space-y-2 text-xs">
          <span className="flex justify-between text-muted-foreground">
            <span>{t('clone.' + knob.label)}</span>
            <span className="tabular-nums">{value[knob.key] ?? knob.value}</span>
          </span>
          <input
            aria-label={t('clone.' + knob.label)}
            type="range"
            className="w-full accent-primary"
            min={knob.min}
            max={knob.max}
            step={knob.step}
            value={value[knob.key] ?? knob.value}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, [knob.key]: Number(event.target.value) })}
          />
        </label>
      ))}
      <label className="flex items-center justify-between gap-2 text-xs">
        {t('clone.postprocess')}
        <Switch
          disabled={disabled}
          checked={value.postprocess ?? true}
          onCheckedChange={(postprocess) => onChange({ ...value, postprocess })}
        />
      </label>
      <label
        className="flex items-center justify-between gap-2 text-xs"
        title={t('audiobook.vary_repeats_help')}
      >
        {t('audiobook.vary_repeats')}
        <Switch
          disabled={disabled}
          checked={value.varyRepeats}
          onCheckedChange={(varyRepeats) => onChange({ ...value, varyRepeats })}
        />
      </label>
      <p className="text-xs text-muted-foreground">{t('audiobook.joins_help')}</p>
      {gaps.map((gap) => (
        <label key={gap.key} className="block space-y-2 text-xs">
          <span className="flex justify-between text-muted-foreground">
            <span>{t('audiobook.' + gap.label)}</span>
            <span className="tabular-nums">{value[gap.key] ?? gap.value} ms</span>
          </span>
          <input
            aria-label={t('audiobook.' + gap.label)}
            type="range"
            className="w-full accent-primary"
            min={0}
            max={gap.max}
            step={50}
            value={value[gap.key] ?? gap.value}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, [gap.key]: Number(event.target.value) })}
          />
        </label>
      ))}
      <label
        className="flex items-center justify-between gap-2 text-xs"
        title={t('audiobook.trim_edges_help')}
      >
        {t('audiobook.trim_edges')}
        <Switch
          disabled={disabled}
          checked={value.trimEdges ?? true}
          onCheckedChange={(trimEdges) => onChange({ ...value, trimEdges })}
        />
      </label>
      <label className="block space-y-1 text-xs">
        {t('audiobook.seed')}
        <Input
          type="number"
          step="1"
          value={value.seed ?? ''}
          placeholder={t('audiobook.seed_ph')}
          disabled={disabled}
          onChange={(event) => {
            const raw = event.target.value;
            const seed = Number(raw);
            onChange({ ...value, seed: raw !== '' && Number.isSafeInteger(seed) ? seed : null });
          }}
        />
      </label>
      {emotion && (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">{t('audiobook.emotion_help')}</p>
          <label className="block space-y-1 text-xs">
            {t('audiobook.emotion_text')}
            <Input
              value={value.emoText}
              disabled={disabled}
              onChange={(event) => onChange({ ...value, emoText: event.target.value })}
            />
          </label>
          <label className="block space-y-1 text-xs">
            {t('audiobook.emotion_alpha')}
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              className="w-full accent-primary"
              disabled={disabled}
              value={value.emoAlpha ?? 1}
              onChange={(event) => onChange({ ...value, emoAlpha: Number(event.target.value) })}
            />
          </label>
        </div>
      )}
      <Button
        size="sm"
        variant="ghost"
        disabled={disabled}
        onClick={() => onChange({ ...DEFAULT_OVERRIDES })}
      >
        {t('audiobook.reset')}
      </Button>
    </details>
  );
}
