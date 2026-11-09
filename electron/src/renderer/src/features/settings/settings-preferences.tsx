import {
  ClipboardCheckIcon,
  LanguagesIcon,
  MonitorIcon,
  MoonIcon,
  PaletteIcon,
  RotateCcwIcon,
  Settings2Icon,
  SunIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useTheme } from '@/hooks/use-theme';
import { appearanceScales, useAppearance } from '@/hooks/use-appearance';
import { setReviewMode, useReviewMode } from '@/hooks/use-review-mode';
import { setWorkspace, useWorkspace } from '@/lib/store/workspace';
import i18n, { APP_LANGUAGE_ITEMS, APP_LANGUAGES, setAppLanguage, type AppLocale } from '@/i18n';
import { PalettePicker } from './palette-picker';
import { SettingsRow, SettingsSection } from './settings-layout';

/** Settings → Appearance. */
export function AppearanceSettings() {
  const { t } = useTranslation();
  const { mode, setTheme } = useTheme();
  const appearance = useAppearance();
  const workspace = useWorkspace();
  return (
    <SettingsSection icon={PaletteIcon} title={t('preferences.appearance')}>
      <>
        <SettingsRow id="theme-label" title={t('preferences.theme')}>
          <Button
            size="sm"
            variant={mode === 'light' ? 'secondary' : 'ghost'}
            aria-pressed={mode === 'light'}
            onClick={() => setTheme('light')}
          >
            <SunIcon />
            {t('themeAppearance.light')}
          </Button>
          <Button
            size="sm"
            variant={mode === 'dark' ? 'secondary' : 'ghost'}
            aria-pressed={mode === 'dark'}
            onClick={() => setTheme('dark')}
          >
            <MoonIcon />
            {t('themeAppearance.dark')}
          </Button>
          <Button
            size="sm"
            variant={mode === 'system' ? 'secondary' : 'ghost'}
            aria-pressed={mode === 'system'}
            onClick={() => setTheme('system')}
          >
            <MonitorIcon />
            {t('themeAppearance.system')}
          </Button>
        </SettingsRow>
        <SettingsRow id="glass-label" title={t('themeAppearance.glass')}>
          <Switch
            aria-labelledby="glass-label"
            checked={appearance.glass}
            onCheckedChange={(glass) => appearance.update({ glass })}
          />
        </SettingsRow>
        <SettingsRow
          id="sidebar-expanded-label"
          description={t('preferences.sidebar_expanded_hint')}
          title={t('preferences.sidebar_expanded')}
        >
          <Switch
            aria-labelledby="sidebar-expanded-label"
            checked={workspace.autoCollapseSidebar === false}
            onCheckedChange={(pinned) => setWorkspace({ autoCollapseSidebar: !pinned })}
          />
        </SettingsRow>
        <PalettePicker appearance="light" />
        <PalettePicker appearance="dark" />
        <SettingsRow id="font-label" title={t('preferences.font')}>
          {(['inter', 'system'] as const).map((font) => (
            <Button
              size="sm"
              key={font}
              variant={appearance.font === font ? 'secondary' : 'ghost'}
              aria-pressed={appearance.font === font}
              onClick={() => appearance.update({ font })}
            >
              <span
                style={{
                  fontFamily: font === 'inter' ? '"Inter Variable", sans-serif' : 'system-ui',
                }}
              >
                {font === 'inter' ? 'Inter' : t('preferences.audio_tools_origin_system')}
              </span>
            </Button>
          ))}
        </SettingsRow>
        <SettingsRow id="scale-label" title={t('preferences.ui_scale')}>
          {appearanceScales.map((scale) => (
            <Button
              size="sm"
              key={scale}
              variant={appearance.scale === scale ? 'secondary' : 'ghost'}
              aria-pressed={appearance.scale === scale}
              onClick={() => appearance.update({ scale })}
            >
              {scale}%
            </Button>
          ))}
        </SettingsRow>
      </>
    </SettingsSection>
  );
}

/** Restores the default font, scale, glass and theme. */
export function AppearanceResetButton() {
  const { t } = useTranslation();
  const { updateTheme } = useTheme();
  const appearance = useAppearance();
  return (
    <Button
      size="xs"
      variant="ghost"
      className="ml-auto"
      onClick={() => {
        appearance.update({ font: 'inter', scale: 100, glass: true });
        updateTheme({ mode: 'dark', light: 'signal', dark: 'signal' });
      }}
    >
      <RotateCcwIcon />
      {t('preferences.reset')}
    </Button>
  );
}

/** Settings → General. */
export function GeneralSettings() {
  const { t } = useTranslation();
  const reviewMode = useReviewMode();
  return (
    <SettingsSection icon={Settings2Icon} title={t('preferences.general')}>
      <>
        <SettingsRow
          id="language-label"
          title={t('settings.language')}
          description={t('settings.language_desc')}
        >
          <Select
            items={APP_LANGUAGE_ITEMS}
            value={i18n.resolvedLanguage || i18n.language}
            onValueChange={(value) => void setAppLanguage(value as AppLocale)}
          >
            <SelectTrigger aria-labelledby="language-label" className="min-w-44">
              <LanguagesIcon className="size-4 text-muted-foreground" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {APP_LANGUAGES.map(({ code, label }) => (
                <SelectItem key={code} value={code}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingsRow>
        <SettingsRow
          id="review-mode-label"
          title={t('settings.review_mode')}
          description={t('settings.review_mode_desc')}
        >
          <div
            role="group"
            aria-label={t('settings.review_mode')}
            className="inline-flex rounded-lg border border-border/60 bg-muted/30 p-0.5"
          >
            {(['on', 'off'] as const).map((value) => (
              <Button
                key={value}
                size="sm"
                variant={reviewMode === value ? 'secondary' : 'ghost'}
                aria-pressed={reviewMode === value}
                onClick={() => setReviewMode(value)}
                className="shadow-none"
              >
                {value === 'on' && <ClipboardCheckIcon />}
                {t(`settings.review_mode_${value}`)}
              </Button>
            ))}
          </div>
        </SettingsRow>
      </>
    </SettingsSection>
  );
}
