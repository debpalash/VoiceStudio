import { useState } from 'react';
import { ChevronUp, ChevronDown, Save } from 'lucide-react';
import { Button, Input } from '../../ui';
import { PRESETS, CATEGORIES } from '../../utils/constants';
import {
  PRESET_ICONS,
  PERSONALITY_ICONS,
  FALLBACK_VOICE_ICON,
  FALLBACK_PERSONALITY_ICON,
  stripVoiceEmoji,
} from '../../utils/voiceIcons';
import { buildDesignInstruct } from '../../utils/voiceInstruct';

// Chip / personality-chip class families migrated from index.css to Tailwind
// utilities (shadcn P4). The token utilities reference the same --chrome-* vars
// the old `.personality-chip` / `.chip-group .chip` rules used, so the look is
// unchanged and still recolors with every [data-theme]. Active = chrome accent
// (pink), matching the rest of the app's selection accent.
// `focus-visible` ring matches the studio's shared 10x a11y rule (index.css):
// an opaque accent outline at 1px offset, on top of the app's global ring.
const CHIP_FOCUS =
  'focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:1px]';
const PCHIP_BASE = `inline-flex items-center gap-[5px] px-[12px] py-[5px] font-[var(--font-sans)] text-[0.72rem] font-medium rounded-[var(--chrome-radius-pill)] border bg-transparent flex-none cursor-pointer transition-colors duration-[120ms] ${CHIP_FOCUS}`;
const PCHIP_INACTIVE =
  'border-transparent text-[var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg)] hover:border-transparent hover:text-[var(--chrome-fg)]';
const PCHIP_ACTIVE =
  'bg-[var(--chrome-accent-bg)] border-[var(--chrome-accent-border)] text-[var(--chrome-accent)]';
const CHIP_BASE = `font-[var(--font-sans)] font-medium text-[0.68rem] px-[10px] py-[3px] rounded-[var(--chrome-radius-pill)] border bg-transparent whitespace-nowrap cursor-pointer transition-colors duration-[120ms] ${CHIP_FOCUS}`;
const CHIP_INACTIVE =
  'border-transparent text-[var(--chrome-fg-muted)] hover:text-[var(--chrome-fg)] hover:bg-[var(--chrome-hover-bg)] hover:border-transparent';

// Voice-design redesign (#1771 follow-up): Gender/Age/Pitch/Style are each a
// single-value pick, so a <select> is the honest control (was a 12-row
// always-open chip block that printed every value three times over). Laid
// out as a 2x2 grid; kept as one array so the grid and its labels stay in
// sync if a category is ever added/removed.
const SELECT_CATEGORIES = ['Gender', 'Age', 'Pitch', 'Style'];
// English accent and Chinese dialect are two independent CATEGORIES entries
// (the engine's exclusivity rule lives in voiceInstruct.js's EXCLUSIVE_GROUPS)
// but only one can ever apply, so the picker merges them into ONE <select>
// with two <optgroup>s. onAccentDialectChange below decides which CATEGORIES
// key a picked value belongs to and routes the change through the shared
// onVdChange -> applyVdState path — it never reimplements the exclusivity
// rule itself.
const ACCENT_OPTIONS = CATEGORIES.EnglishAccent.filter((opt) => opt !== 'Auto');
const DIALECT_OPTIONS = CATEGORIES.ChineseDialect.filter((opt) => opt !== 'Auto');
// "Starting points" chips (#1771 follow-up item 5): only the first N of the
// COMBINED lane show by default — the rest reveal in place via a dynamic
// "{{count}} more…" chip, so the row never hardcodes names or a count. The
// lane renders two sources (personalities from the backend + the hardcoded
// PRESETS below) sharing one row, so both must be sliced together — slicing
// only chipPersonalities left PRESETS permanently visible after the overflow
// chip and made the "N more…" count wrong (only counted the hidden
// personalities, not the hidden presets too).
const VISIBLE_CHIP_COUNT = 5;

export default function DesignMethodPanel({
  t,
  describeText,
  onDescribeChange,
  describeMatchedAny,
  describeUnmatched,
  chipPersonalities,
  activePersonality,
  applyPersonality,
  applyPreset,
  identityOpen,
  setIdentityOpen,
  identityRecipe,
  vdStates,
  onVdChange,
  onChipKeyDown,
  resetToDescription,
  showSaveProfile,
  setShowSaveProfile,
  profileName,
  setProfileName,
  handleSaveDesignProfile,
  instruct,
  language,
}) {
  const [chipsExpanded, setChipsExpanded] = useState(false);

  // #983: a profile/localStorage-restored vdStates can carry a partial shape
  // (missing category keys), and a saved profile's ChineseDialect value can
  // be any of the ~600 CosyVoice speaker-id style entries, not just the
  // curated CATEGORIES list — guard before .replace() rather than crash;
  // 'Auto' matches how the rest of the component treats an unset category.
  const optLabel = (val) => {
    if (typeof val !== 'string' || !val) return t('clone.opt_Auto');
    const tKey = `clone.opt_${val.replace(/[ -]/g, '_')}`;
    const tl = t(tKey);
    return tl !== tKey ? tl : val;
  };

  const accentValue = vdStates.EnglishAccent;
  const dialectValue = vdStates.ChineseDialect;
  const accentDialectValue =
    accentValue && accentValue !== 'Auto'
      ? accentValue
      : dialectValue && dialectValue !== 'Auto'
        ? dialectValue
        : 'Auto';

  const onAccentDialectChange = (value) => {
    if (value === 'Auto') {
      // Clear whichever side is currently set — there is at most one.
      if (accentValue && accentValue !== 'Auto') onVdChange('EnglishAccent', 'Auto');
      else if (dialectValue && dialectValue !== 'Auto') onVdChange('ChineseDialect', 'Auto');
      return;
    }
    onVdChange(ACCENT_OPTIONS.includes(value) ? 'EnglishAccent' : 'ChineseDialect', value);
  };

  // ONE preset system (10x §1.3): personalities + the old PROMPT presets
  // share a single "Starting points" lane, so the 5-visible slice and its
  // overflow count are computed over the COMBINED list, not chipPersonalities
  // alone. `uid` is namespaced (personality ids and PRESETS ids can collide —
  // e.g. both have a 'narrator') so it's safe as both the React key and the
  // roving-tabindex nav id.
  const allChips = [
    ...chipPersonalities.map((p) => ({ ...p, kind: 'personality', uid: `personality:${p.id}` })),
    ...PRESETS.map((p) => ({ ...p, kind: 'preset', uid: `preset:${p.id}` })),
  ];
  const visibleChips = chipsExpanded ? allChips : allChips.slice(0, VISIBLE_CHIP_COUNT);
  const overflowCount = allChips.length - visibleChips.length;
  const visibleUids = visibleChips.map((c) => c.uid);
  const activeUid = activePersonality ? `personality:${activePersonality}` : null;
  const selectChip = (uid) => {
    const chip = allChips.find((c) => c.uid === uid);
    if (!chip) return;
    if (chip.kind === 'personality') applyPersonality(chip);
    else applyPreset(chip);
  };

  return (
    <div>
      {/* ── Describe your voice (#317) — free text drives the controls.
                The placeholder explains itself; no extra header (10x §1.2). ── */}
      <div className="mb-[8px]">
        <textarea
          className="input-base w-full resize-y min-h-[44px] mb-1"
          rows={2}
          placeholder={t('clone.describe_placeholder')}
          value={describeText}
          onChange={onDescribeChange}
        />
        {describeText.trim() && !describeMatchedAny && (
          <div className="text-[0.65rem] text-[#d79921] mb-[2px]" role="status">
            {t('clone.describe_no_match')}
          </div>
        )}
        {describeMatchedAny && describeUnmatched.length > 0 && (
          <div className="text-[0.65rem] text-[#d79921] mb-[2px]" role="status">
            {t('clone.describe_unmatched', { items: describeUnmatched.join(', ') })}
          </div>
        )}
        <div className="text-[0.62rem] text-[var(--chrome-fg-muted)]">
          {t('clone.describe_hint')}
        </div>
      </div>

      {/* ONE preset system (10x §1.3): personalities + the old PROMPT
                presets share a single "Starting points" lane — both set
                vdStates + instruct. Chips beyond the first 5 of that COMBINED
                lane (#1771 follow-up) reveal in place via a dynamic "N
                more…" chip so the row never hardcodes names or a count. */}
      <div className="mt-[8px] mr-0 mb-[12px] ml-0">
        <div className="font-[var(--chrome-font-mono)] text-[0.62rem] uppercase tracking-[0.06em] text-[var(--chrome-fg-muted)] mb-[6px]">
          {t('clone.starting_points', { defaultValue: 'Starting points' })}
        </div>
        <div
          className="flex flex-wrap gap-[6px] mb-[10px]"
          role="group"
          aria-label={t('clone.starting_points', { defaultValue: 'Starting points' })}
        >
          {visibleChips.map((chip, i) => {
            const isPersonality = chip.kind === 'personality';
            const Icon = isPersonality
              ? PERSONALITY_ICONS[chip.id] || FALLBACK_PERSONALITY_ICON
              : PRESET_ICONS[chip.id] || FALLBACK_VOICE_ICON;
            // Only personalities track an "active" pick — PRESETS never did
            // (applying one rewrites vdStates directly with no selection
            // memory). Roving tabindex (WAI-ARIA toolbar pattern): the active
            // chip is the group's single tab stop (first chip if nothing
            // matches). This is a toggle strip, not a true radio group —
            // clicking the active personality chip again clears it
            // (applyPersonality) — so it uses aria-pressed rather than
            // role="radio"/aria-checked.
            const checked = isPersonality && activePersonality === chip.id;
            const roving = checked || (!visibleUids.includes(activeUid) && i === 0);
            const label = isPersonality
              ? t(`clone.personality_${chip.id}`, { defaultValue: chip.name })
              : t(`clone.preset_${chip.id}`, { defaultValue: chip.name });
            return (
              <button
                key={chip.uid}
                type="button"
                aria-pressed={checked}
                tabIndex={roving ? 0 : -1}
                data-chip-nav="true"
                className={`${PCHIP_BASE} ${checked ? PCHIP_ACTIVE : PCHIP_INACTIVE}`}
                onClick={() => selectChip(chip.uid)}
                onKeyDown={(e) => onChipKeyDown(e, visibleUids, i)}
              >
                <span className="inline-flex items-center">
                  <Icon size={13} />
                </span>
                {stripVoiceEmoji(label)}
              </button>
            );
          })}
          {overflowCount > 0 && (
            <button
              type="button"
              className={`${CHIP_BASE} ${CHIP_INACTIVE}`}
              onClick={() => setChipsExpanded(true)}
            >
              {t('clone.chips_more', {
                count: overflowCount,
                defaultValue: `${overflowCount} more…`,
              })}
            </button>
          )}
        </div>
      </div>
      {/* Details summary (10x §1.5, #1771 follow-up): the whole fine-grained
                block collapses to ONE recipe line — the only place any picked
                value is printed. Expanding it REPLACES this line's content
                area with the five fields; the recipe is never shown twice.
                All-Auto (first run) starts expanded. */}
      <button
        type="button"
        className="flex items-center gap-[8px] w-full mt-[4px] mb-[8px] px-[10px] py-[6px] bg-[var(--chrome-hover-bg)] border border-transparent rounded-[8px] cursor-pointer text-left transition-[border-color] duration-[var(--dur-fast)] hover:border-transparent focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:1px]"
        onClick={() => setIdentityOpen((o) => !o)}
        aria-expanded={identityOpen}
        aria-controls="design-details-fields"
      >
        <span className="font-[var(--chrome-font-mono)] text-[0.62rem] uppercase tracking-[0.06em] text-[var(--chrome-fg-muted)] flex-none">
          {t('clone.details', { defaultValue: 'Details' })}
        </span>
        <span className="flex-1 min-w-0 text-[0.74rem] text-[var(--chrome-fg)] truncate">
          {identityRecipe}
        </span>
        <span className="flex items-center gap-[3px] text-[0.62rem] text-[var(--chrome-fg-muted)] flex-none">
          {t('clone.edit', { defaultValue: 'Edit' })}
          {identityOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </span>
      </button>
      {identityOpen && (
        <div id="design-details-fields">
          <div className="grid grid-cols-2 gap-x-[12px] gap-y-[8px]">
            {SELECT_CATEGORIES.map((key) => (
              <div key={key} className="min-w-0">
                <label htmlFor={`vd-${key}`} className="label-row text-[0.7rem]">
                  {t(`clone.cat_${key}`)}
                </label>
                <select
                  id={`vd-${key}`}
                  className="input-base"
                  value={vdStates[key] || 'Auto'}
                  onChange={(e) => onVdChange(key, e.target.value)}
                >
                  {CATEGORIES[key].map((opt) => (
                    <option key={opt} value={opt}>
                      {optLabel(opt)}
                    </option>
                  ))}
                </select>
              </div>
            ))}
            <div className="col-[1/-1] min-w-0">
              <label htmlFor="vd-AccentDialect" className="label-row text-[0.7rem]">
                {t('clone.cat_AccentDialect', { defaultValue: 'Accent or Dialect' })}
                <span className="ml-[6px] text-[0.58rem] text-[var(--chrome-fg-muted)] font-medium">
                  {t('clone.accent_dialect_hint', {
                    defaultValue: 'one or the other, never both',
                  })}
                </span>
              </label>
              <select
                id="vd-AccentDialect"
                className="input-base"
                value={accentDialectValue}
                onChange={(e) => onAccentDialectChange(e.target.value)}
              >
                <option value="Auto">{optLabel('Auto')}</option>
                <optgroup label={t('clone.cat_EnglishAccent')}>
                  {ACCENT_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>
                      {optLabel(opt)}
                    </option>
                  ))}
                </optgroup>
                <optgroup label={t('clone.cat_ChineseDialect')}>
                  {DIALECT_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>
                      {optLabel(opt)}
                    </option>
                  ))}
                </optgroup>
              </select>
            </div>
          </div>

          {/* Reset to the description's implied recipe, or save the current
                    one as a reusable profile (0005): the backend renders a
                    deterministic identity sample (seed 42) and stores the
                    slider picks for later re-editing. */}
          {/* Separated by space alone, never a rule: the app-wide border
                    removal (tests/test_no_literal_borders.py) took every
                    decorative divider out, so a token hairline here would
                    reintroduce exactly the class that guard protects. */}
          <div className="mt-[var(--space-4)] flex items-center justify-between gap-[var(--space-3)] flex-wrap">
            <Button variant="ghost" size="sm" onClick={resetToDescription}>
              {t('clone.reset_to_description', { defaultValue: 'Reset to description' })}
            </Button>
            {!showSaveProfile ? (
              <Button
                variant="subtle"
                size="sm"
                onClick={() => setShowSaveProfile(true)}
                leading={<Save size={12} />}
              >
                {t('clone.save_design_as_profile', { defaultValue: 'Save design as profile' })}
              </Button>
            ) : (
              <div className="flex gap-[var(--space-3)] items-center [&>:first-child]:flex-1">
                <Input
                  size="sm"
                  placeholder={t('clone.profile_name')}
                  value={profileName}
                  onChange={(e) => setProfileName(e.target.value)}
                />
                <Button
                  variant="subtle"
                  size="sm"
                  onClick={() =>
                    handleSaveDesignProfile(
                      vdStates,
                      buildDesignInstruct(vdStates, instruct).instruct,
                      language,
                    )
                  }
                >
                  {t('clone.save')}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setShowSaveProfile(false)}>
                  {t('clone.cancel')}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
