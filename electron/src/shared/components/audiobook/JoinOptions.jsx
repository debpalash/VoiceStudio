import i18n from '../../i18n';

const SLIDERS = [
  { patchKey: 'lineGapMs', label: 'audiobook.line_gap', top: 2000 },
  { patchKey: 'paragraphGapMs', label: 'audiobook.paragraph_gap', top: 3000 },
];

const BADGE =
  'text-[0.65rem] bg-black/35 px-[5px] py-px rounded-[3px] [border:1px_solid_rgba(255,255,255,0.04)] [font-variant-numeric:tabular-nums]';

/** Gap-between-lines sliders and the trim checkbox of the legacy overrides panel. */
export default function JoinOptions({ t, overrides, onChange }) {
  const fmt = new Intl.NumberFormat(i18n.language, {
    style: 'unit',
    unit: 'millisecond',
    unitDisplay: 'short',
  });
  return (
    <fieldset className="m-0 flex flex-col gap-[8px] border-0 p-0">
      <p className="m-0 text-[0.7rem] text-fg-muted">{t('audiobook.joins_help')}</p>
      {SLIDERS.map(({ patchKey, label, top }) => {
        const amount = overrides[patchKey] ?? 0;
        return (
          <label key={patchKey} className="flex flex-col gap-[2px] text-[0.7rem]">
            <span className="flex items-center justify-between">
              {t(label)}
              <span className={BADGE}>{fmt.format(amount)}</span>
            </span>
            <input
              type="range"
              min="0"
              max={top}
              step="50"
              aria-label={t(label)}
              value={amount}
              onChange={(e) => onChange({ [patchKey]: Number(e.target.value) })}
            />
          </label>
        );
      })}
      <label
        className="flex cursor-pointer items-center gap-[6px] text-[0.72rem]"
        title={t('audiobook.trim_edges_help')}
      >
        <input
          type="checkbox"
          checked={overrides.trimEdges ?? false}
          onChange={(e) => onChange({ trimEdges: e.target.checked })}
        />
        {t('audiobook.trim_edges')}
      </label>
    </fieldset>
  );
}
