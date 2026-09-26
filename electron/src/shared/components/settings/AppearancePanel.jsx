/**
 * Settings → Appearance panel.
 *
 * Houses the UI scale (S/M/L) and color-theme picker that used to live in
 * the always-visible LogsFooter chrome. Moved here because they're
 * rarely-used preferences that don't need to compete with logs / error
 * counts on every screen — Settings is where appearance config belongs.
 */
import React from 'react';
import { Palette } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAppStore, FONT_OPTIONS, FONT_STACKS } from '../../store';
import { SettingsSection, SettingRow, InfoHint, SettingsToggle } from './primitives';

const THEMES = [
  {
    id: 'auto',
    labelKey: 'settings.theme_auto',
    defaultLabel: 'System Auto',
    dot: 'linear-gradient(135deg, #fdf6e3 50%, #1d2021 50%)',
  },
  { id: 'gruvbox', label: 'Gruvbox', dot: '#d3869b' },
  { id: 'midnight', label: 'Midnight', dot: '#8b5cf6' },
  { id: 'nord', label: 'Nord', dot: '#88c0d0' },
  { id: 'solarized', label: 'Solarized', dot: '#268bd2' },
  { id: 'rose-pine', label: 'Rosé Pine', dot: '#ebbcba' },
  { id: 'catppuccin', label: 'Catppuccin', dot: '#cba6f7' },
  { id: 'light', labelKey: 'settings.theme_light', defaultLabel: 'Light', dot: '#1d6b9f' },
];

/**
 * WAI-ARIA radio-group keyboard support for the theme-dot / font-tile pickers:
 * arrow keys move selection (wrapping), Home/End jump to the ends, and focus
 * follows selection. Pair with `radioTabIndex` for the roving tabindex so the
 * group occupies a single tab stop, as the announced role promises.
 */
function radioGroupKeyDown(e, values, current, select) {
  const STEP = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
  let next;
  if (e.key in STEP) {
    const idx = Math.max(0, values.indexOf(current));
    next = values[(idx + STEP[e.key] + values.length) % values.length];
  } else if (e.key === 'Home') {
    next = values[0];
  } else if (e.key === 'End') {
    next = values[values.length - 1];
  }
  if (!next) return;
  e.preventDefault();
  select(next);
  const el = e.currentTarget
    .closest('[role="radiogroup"]')
    ?.querySelector(`[data-radio-value="${next}"]`);
  el?.focus();
}

/** Roving tabindex: only the checked radio (or the first, if none is checked
 * — e.g. a stale persisted value) is tabbable. */
function radioTabIndex(values, current, value) {
  const focusable = values.includes(current) ? current : values[0];
  return value === focusable ? 0 : -1;
}

/**
 * Miniature of each navigation skin — the tile shows the layout instead of
 * describing it, because "rail" vs "tabs" is a shape, not a word.
 */
function NavStylePreview({ style }) {
  if (style === 'tabs') {
    return (
      <span className="appearance-panel__nav-preview" aria-hidden="true">
        <span className="appearance-panel__nav-preview-bar">
          <span className="appearance-panel__nav-preview-tab is-active" />
          <span className="appearance-panel__nav-preview-tab" />
          <span className="appearance-panel__nav-preview-tab" />
        </span>
        <span className="appearance-panel__nav-preview-plane" />
      </span>
    );
  }
  return (
    <span
      className="appearance-panel__nav-preview appearance-panel__nav-preview--rail"
      aria-hidden="true"
    >
      <span className="appearance-panel__nav-preview-rail">
        <span className="appearance-panel__nav-preview-dot is-active" />
        <span className="appearance-panel__nav-preview-dot" />
        <span className="appearance-panel__nav-preview-dot" />
      </span>
      <span className="appearance-panel__nav-preview-plane" />
    </span>
  );
}

export default function AppearancePanel() {
  const { t } = useTranslation();
  const uiScale = useAppStore((s) => s.uiScale);
  const setUiScale = useAppStore((s) => s.setUiScale);
  const theme = useAppStore((s) => s.theme);
  const setTheme = useAppStore((s) => s.setTheme);
  const font = useAppStore((s) => s.font);
  const setFont = useAppStore((s) => s.setFont);
  const reduceMotion = useAppStore((s) => s.reduceMotion);
  const setReduceMotion = useAppStore((s) => s.setReduceMotion);
  const autoPlayPreview = useAppStore((s) => s.autoPlayPreview);
  const setAutoPlayPreview = useAppStore((s) => s.setAutoPlayPreview);
  const showHeaderLiveStats = useAppStore((s) => s.showHeaderLiveStats);
  const setShowHeaderLiveStats = useAppStore((s) => s.setShowHeaderLiveStats);
  const navStyle = useAppStore((s) => s.navStyle);
  const setNavStyle = useAppStore((s) => s.setNavStyle);

  const scaleLabel = t('settings.ui_scale', { defaultValue: 'UI scale' });
  const themeLabel = t('settings.color_theme', { defaultValue: 'Color theme' });
  const fontLabel = t('settings.font', { defaultValue: 'Font' });
  const themeIds = THEMES.map((th) => th.id);
  const fontIds = FONT_OPTIONS.map((f) => f.id);
  const navStyleLabel = t('settings.nav_style', { defaultValue: 'Navigation style' });
  const navStyles = [
    { id: 'rail', label: t('settings.nav_style_rail', { defaultValue: 'Sidebar rail' }) },
    { id: 'tabs', label: t('settings.nav_style_tabs', { defaultValue: 'Titlebar tabs' }) },
  ];
  const navStyleIds = navStyles.map((n) => n.id);

  return (
    <SettingsSection
      className="appearance-panel"
      icon={Palette}
      title={t('settings.appearance', { defaultValue: 'Appearance' })}
      actions={
        <InfoHint label={t('settings.appearance', { defaultValue: 'Appearance' })}>
          {t('settings.appearance_help', {
            defaultValue:
              'These controls used to live in the bottom logs bar — moved here so the footer can stay focused on logs. Changes apply instantly and persist across launches.',
          })}
        </InfoHint>
      }
    >
      <SettingRow
        className="appearance-panel__row--nav-style"
        stack
        align="start"
        title={navStyleLabel}
        subtitle={t('settings.nav_style_desc', {
          defaultValue:
            'Switch workspaces from an icon rail down the window edge, or from tabs across the title bar.',
        })}
        control={
          <div
            className="flex flex-wrap gap-[var(--space-3)]"
            role="radiogroup"
            aria-label={navStyleLabel}
          >
            {navStyles.map((n) => (
              <button
                key={n.id}
                type="button"
                role="radio"
                aria-checked={navStyle === n.id}
                aria-label={n.label}
                tabIndex={radioTabIndex(navStyleIds, navStyle, n.id)}
                data-radio-value={n.id}
                data-testid={`appearance-nav-style-${n.id}`}
                className={`appearance-panel__nav-tile ${navStyle === n.id ? 'is-active' : ''}`}
                onClick={() => setNavStyle(n.id)}
                onKeyDown={(e) => radioGroupKeyDown(e, navStyleIds, navStyle, setNavStyle)}
              >
                <NavStylePreview style={n.id} />
                <span className="appearance-panel__nav-name">{n.label}</span>
              </button>
            ))}
          </div>
        }
      />

      <SettingRow
        title={scaleLabel}
        control={
          <div className="inline-flex w-[clamp(160px,100%,260px)] min-w-0 items-center gap-[var(--space-4)]">
            <input
              type="range"
              min="0.6"
              max="1.75"
              step="0.05"
              value={uiScale}
              onChange={(e) => setUiScale(Number(e.target.value))}
              aria-label={scaleLabel}
              aria-valuetext={`${Math.round(uiScale * 100)}%`}
              className="min-w-0 flex-1 cursor-pointer accent-[var(--color-brand)]"
            />
            <span className="min-w-[40px] text-right text-[length:var(--text-sm)] tabular-nums text-[var(--chrome-fg)]">
              {Math.round(uiScale * 100)}%
            </span>
          </div>
        }
      />

      <SettingRow
        title={themeLabel}
        control={
          <div
            className="inline-flex items-center gap-[var(--space-4)]"
            role="radiogroup"
            aria-label={themeLabel}
          >
            {THEMES.map((th) => (
              <button
                key={th.id}
                type="button"
                className={`appearance-panel__theme-dot ${theme === th.id ? 'is-active' : ''}`}
                style={{ '--dot-color': th.dot }}
                onClick={() => setTheme(th.id)}
                onKeyDown={(e) => radioGroupKeyDown(e, themeIds, theme, setTheme)}
                title={th.labelKey ? t(th.labelKey, { defaultValue: th.defaultLabel }) : th.label}
                aria-label={
                  th.labelKey ? t(th.labelKey, { defaultValue: th.defaultLabel }) : th.label
                }
                aria-checked={theme === th.id}
                role="radio"
                tabIndex={radioTabIndex(themeIds, theme, th.id)}
                data-radio-value={th.id}
              />
            ))}
          </div>
        }
      />

      <SettingRow
        className="appearance-panel__row--fonts"
        stack
        align="start"
        title={fontLabel}
        control={
          <div
            className="grid w-full min-w-0 grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-[var(--space-3)]"
            role="radiogroup"
            aria-label={fontLabel}
          >
            {FONT_OPTIONS.map((f) => (
              <button
                key={f.id}
                type="button"
                role="radio"
                aria-checked={font === f.id}
                aria-label={f.label}
                tabIndex={radioTabIndex(fontIds, font, f.id)}
                data-radio-value={f.id}
                data-testid={`appearance-font-${f.id}`}
                className={`appearance-panel__font-tile ${font === f.id ? 'is-active' : ''}`}
                style={{ fontFamily: FONT_STACKS[f.id] || 'var(--font-sans)' }}
                onClick={() => setFont(f.id)}
                onKeyDown={(e) => radioGroupKeyDown(e, fontIds, font, setFont)}
              >
                <span className="appearance-panel__font-sample">Ag</span>
                <span className="appearance-panel__font-name">{f.label}</span>
              </button>
            ))}
          </div>
        }
      />

      <SettingRow
        title={t('settings.autoplay_preview', { defaultValue: 'Auto-play preview' })}
        subtitle={t('settings.autoplay_preview_label', {
          defaultValue: 'Play the output as soon as a render finishes',
        })}
        control={
          <SettingsToggle
            checked={autoPlayPreview}
            onChange={setAutoPlayPreview}
            id="autoplay-preview"
            aria-label={t('settings.autoplay_preview', { defaultValue: 'Auto-play preview' })}
          />
        }
      />

      <SettingRow
        title={t('settings.header_live_stats', {
          defaultValue: 'Show live system metrics in header',
        })}
        subtitle={t('settings.header_live_stats_desc', {
          defaultValue: 'Adds a live RAM / CPU / VRAM monitor to the top bar (off by default).',
        })}
        control={
          <SettingsToggle
            checked={showHeaderLiveStats}
            onChange={setShowHeaderLiveStats}
            aria-label={t('settings.header_live_stats', {
              defaultValue: 'Show live system metrics in header',
            })}
          />
        }
      />

      <SettingRow
        title={t('settings.reduce_motion', { defaultValue: 'Reduce motion' })}
        subtitle={t('settings.reduce_motion_desc', {
          defaultValue:
            'Stops background animation and transitions. Your system setting is honoured on its own; this adds to it.',
        })}
        control={
          <SettingsToggle
            checked={reduceMotion}
            onChange={setReduceMotion}
            aria-label={t('settings.reduce_motion', { defaultValue: 'Reduce motion' })}
          />
        }
      />
    </SettingsSection>
  );
}
