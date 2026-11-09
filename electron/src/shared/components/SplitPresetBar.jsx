import SearchableSelect from './SearchableSelect';
import { DEFAULT_SPLIT_MAX, SPLIT_MODES } from '../utils/splitStoryText';

const FIELD_CLS =
  'w-[64px] px-[6px] py-[4px] bg-bg-elev-2 border border-border rounded-sm text-fg [font-family:var(--font-mono)] [font-size:var(--text-xs)]';

/**
 * Preset menu + (sentence mode only) character cap for the legacy Paste & Split panel.
 * Controlled: the parent owns `preset` and `cap`.
 */
export default function SplitPresetBar({ t, preset, onPreset, cap, onCap }) {
  const choices = SPLIT_MODES.map((value) => ({ value, label: t(`stories.split_${value}`) }));
  const parseCap = (raw) => {
    const n = parseInt(raw, 10);
    return Number.isNaN(n) || n <= 0 ? DEFAULT_SPLIT_MAX.sentences : n;
  };
  return (
    <>
      <div className="w-[150px]" title={t('stories.splitModeHint')}>
        <SearchableSelect
          value={preset}
          onChange={onPreset}
          options={choices}
          ariaLabel={t('stories.splitMode')}
          menuPortal
          size="sm"
        />
      </div>
      {preset === 'sentences' ? (
        <label className="flex items-center gap-[6px] [font-size:var(--text-xs)] text-fg-muted">
          {t('stories.maxChars')}
          <input
            type="number"
            min={60}
            max={1000}
            step={10}
            value={cap}
            onChange={(e) => onCap(parseCap(e.target.value))}
            name="story-segment-length"
            inputMode="numeric"
            className={FIELD_CLS}
          />
        </label>
      ) : null}
    </>
  );
}
