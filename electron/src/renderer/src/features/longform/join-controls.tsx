import { useTranslation } from 'react-i18next';
import { Switch } from '@/components/ui/switch';
import type { Overrides } from '@shared/utils/longformOverrides';

type GapField = 'lineGapMs' | 'paragraphGapMs';

const GAPS: ReadonlyArray<{ field: GapField; name: string; ceiling: number }> = [
  { field: 'lineGapMs', name: 'audiobook.line_gap', ceiling: 2000 },
  { field: 'paragraphGapMs', name: 'audiobook.paragraph_gap', ceiling: 3000 },
];

/** Silence-between-lines settings for a render: two gap sliders and the edge-trim switch. */
export function JoinControls({
  value,
  disabled,
  onChange,
}: {
  value: Overrides;
  disabled: boolean;
  onChange: (next: Overrides) => void;
}) {
  const { t, i18n } = useTranslation();
  const ms = new Intl.NumberFormat(i18n.language, {
    style: 'unit',
    unit: 'millisecond',
    unitDisplay: 'short',
  });
  return (
    <section className="space-y-3 rounded-lg border border-border/50 p-3">
      <p className="text-xs leading-relaxed text-muted-foreground">{t('audiobook.joins_help')}</p>
      {GAPS.map(({ field, name, ceiling }) => {
        const current = value[field] ?? 0;
        return (
          <div key={field} className="grid gap-1 text-xs">
            <div className="flex items-center justify-between text-muted-foreground">
              <span>{t(name)}</span>
              <span className="tabular-nums">{ms.format(current)}</span>
            </div>
            <input
              type="range"
              className="w-full accent-primary"
              aria-label={t(name)}
              min={0}
              max={ceiling}
              step={50}
              value={current}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, [field]: Number(e.target.value) })}
            />
          </div>
        );
      })}
      <label
        className="flex items-center justify-between gap-2 text-xs"
        title={t('audiobook.trim_edges_help')}
      >
        {t('audiobook.trim_edges')}
        <Switch
          disabled={disabled}
          checked={value.trimEdges ?? false}
          onCheckedChange={(trimEdges) => onChange({ ...value, trimEdges })}
        />
      </label>
    </section>
  );
}
