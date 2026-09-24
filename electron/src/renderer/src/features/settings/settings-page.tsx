import { SupportSettings } from './support-settings';
import { UpdateSettings } from './update-settings';
import { MirrorSettings } from './mirror-settings';
import { StorageSettings } from './storage-settings';
import { PrivacySettings } from './privacy-settings';
import { PerformanceSettings } from './performance-settings';
import { CredentialSettings } from './credential-settings';
import { NetworkSettings } from './network-settings';
import { SharingSettings } from './sharing-settings';
import { MediaTools } from './media-tools';
import { LogSettings } from './log-settings';
import { PronunciationSettings } from './pronunciation-settings';
import { PermissionsSettings } from './permissions-settings';
import { UsageSettings } from './usage-settings';
import { WorkersSettings } from './workers-settings';
import { OpenApiSettings } from './openapi-settings';
import { DiagnosticsSettings } from './diagnostics-settings';
import { ModelSettings } from './model-settings';
import { familyIcons, modelFamilies } from './model-family';
import { Link, useNavigate, useRouterState } from '@tanstack/react-router';
import { runRendererTask } from '@/lib/global-error-recovery';
import {
  ArrowLeftIcon,
  ExternalLinkIcon,
  SettingsIcon,
  WifiIcon,
  Share2Icon,
  CpuIcon,
  ShieldCheckIcon,
  HardDriveIcon,
  KeyRoundIcon,
  FileTextIcon,
  ChevronRightIcon,
  PaletteIcon,
  SearchIcon,
  Settings2Icon,
  MoonIcon,
  SunIcon,
  MonitorIcon,
  LanguagesIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  BoxesIcon,
  WrenchIcon,
  SpellCheckIcon,
  LockKeyholeIcon,
  BarChart3Icon,
  ServerCogIcon,
  BracesIcon,
  StethoscopeIcon,
  HeartHandshakeIcon,
  ClipboardCheckIcon,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { PalettePicker } from './palette-picker';
import { useTheme } from '@/hooks/use-theme';
import { appearanceScales, useAppearance } from '@/hooks/use-appearance';
import { isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { SettingsContent, SettingsRow, SettingsSection } from './settings-layout';
import i18n, { APP_LANGUAGE_ITEMS, APP_LANGUAGES, setAppLanguage, type AppLocale } from '@/i18n';
import { setReviewMode, useReviewMode } from '@/hooks/use-review-mode';
import { rememberSettingsRoute } from '@/lib/settings-route';
import { setWorkspace, useWorkspace } from '@/lib/store/workspace';

const sections = ['general', 'appearance'] as const;
const fields = {
  general: ['language', 'review_mode'],
  appearance: ['theme', 'font', 'ui_scale', 'glass', 'sidebar_expanded'],
};

const fieldKey = (key: string) =>
  key === 'glass'
    ? 'themeAppearance.glass'
    : key === 'language' || key === 'review_mode'
      ? `settings.${key}`
      : `preferences.${key}`;

const settingsNavItemClass =
  'group h-9 min-w-0 rounded-lg font-normal transition-[color,background-color,box-shadow,backdrop-filter] duration-150 hover:bg-[var(--sidebar-row-hover)] hover:backdrop-blur-xl hover:shadow-[inset_0_1px_0_rgb(255_255_255/8%),0_5px_15px_rgb(0_0_0/9%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/60 aria-[current=page]:bg-[var(--sidebar-row-active)] aria-[current=page]:font-medium aria-[current=page]:shadow-[inset_2px_0_0_var(--primary)]';
const settingsNavIconClass =
  'size-4 shrink-0 text-muted-foreground transition-[color,filter] duration-150 group-hover:text-sidebar-foreground group-hover:drop-shadow-[0_1px_3px_rgb(0_0_0/20%)]';
const settingsNavActiveClass =
  'bg-[var(--sidebar-row-active)] font-medium shadow-[inset_2px_0_0_var(--primary)]';

const extraSettings = [
  {
    to: '/settings/pronunciation',
    label: 'pronunciation.title',
    Icon: SpellCheckIcon,
    Component: PronunciationSettings,
    fields: [
      'pronunciation.title',
      'pronunciation.help',
      'pronunciation.test_label',
      'pronunciation.backup_title',
    ],
  },
  {
    to: '/settings/updates',
    label: 'updates.tab',
    Icon: RefreshCwIcon,
    Component: UpdateSettings,
    fields: ['updates.tab', 'updates.check_now', 'about.update_channel'],
  },
  {
    to: '/settings/support',
    label: 'donate.title',
    Icon: ExternalLinkIcon,
    Component: SupportSettings,
    fields: ['donate.title', 'contact.channels_label'],
  },
  {
    to: '/settings/network',
    label: 'settings.network',
    Icon: WifiIcon,
    Component: NetworkSettings,
    fields: ['settings.network', 'settings.proxy'],
  },
  {
    to: '/settings/sharing',
    label: 'sharing.title',
    Icon: Share2Icon,
    Component: SharingSettings,
    fields: [
      'sharing.title',
      'sharing.local_network',
      'sharing.tailscale_title',
      'settings.remote_backend_title',
    ],
  },
  {
    to: '/settings/credentials',
    label: 'settings.credentials',
    Icon: KeyRoundIcon,
    Component: CredentialSettings,
    fields: [
      'settings.credentials',
      'settings.hf_token_input',
      'settings.translation_providers',
      'credentials.deepl_key',
      'credentials.microsoft_key',
    ],
  },
  {
    to: '/settings/performance',
    label: 'settings.compute_device_title',
    Icon: CpuIcon,
    Component: PerformanceSettings,
    fields: [
      'settings.compute_device_title',
      'settings.perf_torch_compile',
      'settings.generate_budget_title',
    ],
  },
  {
    to: '/settings/usage',
    label: 'settings.usage',
    Icon: BarChart3Icon,
    Component: UsageSettings,
    fields: [
      'settings.usage',
      'settings.usage_takes',
      'settings.usage_audio',
      'settings.usage_by_language',
    ],
  },
  {
    to: '/settings/workers',
    label: 'settings.workers_title',
    Icon: ServerCogIcon,
    Component: WorkersSettings,
    fields: [
      'settings.workers_title',
      'settings.workers_add',
      'settings.worker_join_title',
      'settings.worker_join_code',
    ],
  },
  {
    to: '/settings/privacy',
    label: 'settings.privacy',
    Icon: ShieldCheckIcon,
    Component: PrivacySettings,
    fields: [
      'settings.privacy',
      'privacy.analytics_title',
      'privacy.watermark_title',
      'settings.history_retention',
    ],
  },
  {
    to: '/settings/permissions',
    label: 'permissions.title',
    Icon: LockKeyholeIcon,
    Component: PermissionsSettings,
    fields: ['permissions.title', 'permissions.microphone', 'permissions.accessibility'],
  },
  {
    to: '/settings/storage',
    label: 'settings.storage',
    Icon: HardDriveIcon,
    Component: StorageSettings,
    fields: [
      'settings.storage',
      'settings.storage_usage',
      'settings.history_retention',
      'updates.backup_line',
    ],
  },
  {
    to: '/settings/diagnostics',
    label: 'about.diagnostics',
    Icon: StethoscopeIcon,
    Component: DiagnosticsSettings,
    fields: ['about.diagnostics', 'about.self_check', 'about.save_bundle'],
  },
  {
    to: '/settings/openapi',
    label: 'openapi.title',
    Icon: BracesIcon,
    Component: OpenApiSettings,
    fields: ['openapi.title', 'openapi.copy_url', 'openapi.open_raw'],
  },
] as const;

export function SettingsPage() {
  const { t } = useTranslation();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const extra = extraSettings.find((item) => pathname === item.to);
  const isOpenApi = pathname === '/settings/openapi';
  const isModels = pathname.includes('/models');
  const isMedia = pathname.endsWith('/media');
  const isLogs = pathname.endsWith('/logs');
  const modelFamily = modelFamilies.find((family) => pathname.endsWith('/models/' + family));
  const section = pathname.endsWith('/general') ? 'general' : 'appearance';
  const [query, setQuery] = useState('');
  const navigate = useNavigate();
  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => rememberSettingsRoute(pathname), [pathname]);
  useEffect(() => {
    if (!target) return;
    const field = document.getElementById(target);
    if (field) {
      field.scrollIntoView({ block: 'center' });
      field.focus({ preventScroll: true });
      setTarget(null);
    }
  }, [pathname, target]);
  const { mode, setTheme, updateTheme } = useTheme();
  const appearance = useAppearance();
  const workspace = useWorkspace();
  const reviewMode = useReviewMode();
  const targetIds: Record<string, string> = {
    theme: 'theme-label',
    font: 'font-label',
    ui_scale: 'scale-label',
    glass: 'glass-label',
    sidebar_expanded: 'sidebar-expanded-label',
    language: 'language-label',
    review_mode: 'review-mode-label',
  };
  const extraMatches = extraSettings.filter((item) =>
    item.fields.some((key) =>
      t(key).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
    ),
  );
  const matches = sections.filter((item) =>
    [item, ...fields[item]].some((key) =>
      t(fieldKey(key)).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
    ),
  );

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-[clamp(11rem,20vw,17rem)] shrink-0 flex-col border-r border-sidebar-border/50 bg-sidebar text-sidebar-foreground shadow-[inset_-1px_0_0_color-mix(in_oklab,var(--foreground)_2%,transparent)]">
        <header className="workspace-titlebar flex h-12 shrink-0 items-center gap-2.5 border-b border-sidebar-border/45 bg-background/25 px-3.5 backdrop-blur-xl">
          {/* The brand and window controls live in the main sidebar to the
              left; this column is the Settings section list. */}
          <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-sidebar-border/45 bg-background/45 text-primary shadow-[inset_0_1px_0_rgb(255_255_255/7%)]">
            <SettingsIcon aria-hidden="true" className="size-4" />
          </span>
          <span className="truncate text-sm font-semibold tracking-[-0.014em]">
            {t('nav.settings')}
          </span>
        </header>
        <div className="studio-scrollbar flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain p-3 [scrollbar-gutter:stable]">
          <div className="sticky top-0 z-10 flex h-9 shrink-0 items-center gap-2 rounded-lg border border-sidebar-border/45 bg-sidebar/90 px-2.5 shadow-xs backdrop-blur-xl transition-[border-color,background-color,box-shadow] focus-within:border-primary/30 focus-within:bg-background/55 focus-within:ring-2 focus-within:ring-ring/20">
            <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
            <Input
              type="search"
              aria-label={t('preferences.search')}
              placeholder={t('preferences.search')}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setQuery('');
              }}
              className="h-auto min-w-0 border-0 bg-transparent p-0 shadow-none focus-visible:ring-0 dark:bg-transparent"
            />
          </div>
          <nav aria-label={t('nav.settings')} className="flex flex-col gap-0.5">
            {matches.map((item) => (
              <Link
                key={item}
                title={t(`preferences.${item}`)}
                to={item === 'appearance' ? '/settings/appearance' : '/settings/general'}
                className={cn(
                  buttonVariants({ variant: 'ghost' }),
                  settingsNavItemClass,
                  'justify-start gap-2 px-2 text-sm',
                  !extra &&
                    !isModels &&
                    !isLogs &&
                    !isMedia &&
                    section === item &&
                    'bg-[var(--sidebar-row-active)] font-medium',
                )}
                aria-current={
                  !extra && !isModels && !isLogs && !isMedia && section === item
                    ? 'page'
                    : undefined
                }
              >
                {item === 'appearance' ? (
                  <PaletteIcon className={settingsNavIconClass} />
                ) : (
                  <Settings2Icon className={settingsNavIconClass} />
                )}
                <span data-slot="settings-nav-label" className="min-w-0 flex-1 truncate text-left">
                  {t(`preferences.${item}`)}
                </span>
              </Link>
            ))}
            {query.trim() &&
              sections.flatMap((item) =>
                fields[item]
                  .filter((key) =>
                    t(fieldKey(key)).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
                  )
                  .map((key) => (
                    <Button
                      key={key}
                      variant="ghost"
                      className={cn(
                        settingsNavItemClass,
                        'justify-start pl-8 text-xs text-muted-foreground',
                      )}
                      onClick={() => {
                        setTarget(targetIds[key]);
                        setQuery('');
                        runRendererTask('Open settings search result', () =>
                          navigate({
                            to:
                              item === 'appearance' ? '/settings/appearance' : '/settings/general',
                          }),
                        );
                      }}
                    >
                      <span
                        data-slot="settings-nav-label"
                        className="min-w-0 flex-1 truncate text-left"
                      >
                        {t(fieldKey(key))}
                      </span>
                    </Button>
                  )),
              )}
            <Link
              to="/settings/models"
              title={t('modelSettings.models')}
              className={cn(
                buttonVariants({ variant: 'ghost' }),
                settingsNavItemClass,
                'justify-start px-2',
                isModels && settingsNavActiveClass,
              )}
            >
              <BoxesIcon className={settingsNavIconClass} />
              <span data-slot="settings-nav-label" className="min-w-0 flex-1 truncate text-left">
                {t('modelSettings.models')}
              </span>
            </Link>
            {isModels &&
              modelFamilies.map((family) => {
                const FamilyIcon = familyIcons[family];
                return (
                  <Link
                    key={family}
                    title={t('engineSidebar.' + family)}
                    to="/settings/models/$family"
                    params={{ family }}
                    aria-current={modelFamily === family ? 'page' : undefined}
                    className={cn(
                      buttonVariants({ variant: 'ghost' }),
                      settingsNavItemClass,
                      'justify-start gap-2 pl-7 text-muted-foreground',
                      modelFamily === family && 'bg-sidebar-accent text-foreground',
                    )}
                  >
                    <FamilyIcon className={cn(settingsNavIconClass, 'size-3.5')} />
                    <span
                      data-slot="settings-nav-label"
                      className="min-w-0 flex-1 truncate text-left"
                    >
                      {t('engineSidebar.' + family)}
                    </span>
                  </Link>
                );
              })}
            {extraMatches
              .filter(({ to }) => to !== '/settings/support')
              .map(({ to, label, Icon }) => (
                <Link
                  key={to}
                  title={t(label)}
                  to={to}
                  aria-current={extra?.to === to ? 'page' : undefined}
                  className={cn(
                    buttonVariants({ variant: 'ghost', size: 'sm' }),
                    settingsNavItemClass,
                    'w-full justify-start',
                    extra?.to === to && 'bg-sidebar-accent',
                  )}
                >
                  <Icon className={settingsNavIconClass} />
                  <span
                    data-slot="settings-nav-label"
                    className="min-w-0 flex-1 truncate text-left"
                  >
                    {t(label)}
                  </span>
                </Link>
              ))}
            <Link
              to="/settings/media"
              title={t('settings.audio_tools')}
              className={cn(
                buttonVariants({ variant: 'ghost', size: 'sm' }),
                settingsNavItemClass,
                'w-full justify-start',
                isMedia && settingsNavActiveClass,
              )}
            >
              <WrenchIcon className={settingsNavIconClass} />
              <span data-slot="settings-nav-label" className="min-w-0 flex-1 truncate text-left">
                {t('settings.audio_tools')}
              </span>
            </Link>
            <Link
              to="/settings/logs"
              title={t('settings.logs')}
              className={cn(
                buttonVariants({ variant: 'ghost' }),
                settingsNavItemClass,
                'justify-start px-2',
                isLogs && settingsNavActiveClass,
              )}
            >
              <FileTextIcon className={settingsNavIconClass} />
              <span data-slot="settings-nav-label" className="min-w-0 flex-1 truncate text-left">
                {t('settings.logs')}
              </span>
            </Link>
          </nav>
          {matches.length === 0 && extraMatches.length === 0 && (
            <p role="status" className="px-2 py-6 text-center text-xs text-muted-foreground">
              {t('preferences.no_matches')}
            </p>
          )}
        </div>
        <div className="shrink-0 space-y-1.5 border-t border-sidebar-border/50 bg-background/15 p-3">
          <Link
            to="/settings/support"
            title={t('donate.title')}
            aria-current={extra?.to === '/settings/support' ? 'page' : undefined}
            className={cn(
              buttonVariants({ variant: 'ghost' }),
              'group relative w-full justify-start overflow-hidden border border-primary/20 bg-gradient-to-r from-primary/15 via-primary/8 to-transparent text-foreground shadow-[inset_0_1px_0_rgb(255_255_255/10%)] transition-[border-color,background-color,box-shadow] hover:border-primary/35 hover:from-primary/20 hover:shadow-[0_8px_24px_-16px_var(--primary)]',
              extra?.to === '/settings/support' && 'border-primary/40 bg-primary/15',
            )}
          >
            <HeartHandshakeIcon className="size-4 shrink-0 text-primary transition-transform duration-200 group-hover:scale-110 motion-reduce:transition-none" />
            <span data-slot="settings-nav-label" className="min-w-0 flex-1 truncate text-left">
              {t('donate.title')}
            </span>
          </Link>
          <Link
            to="/clone"
            title={t('preferences.back')}
            className={cn(
              buttonVariants({ variant: 'ghost' }),
              settingsNavItemClass,
              'w-full justify-start',
            )}
          >
            <ArrowLeftIcon className={settingsNavIconClass} />
            <span data-slot="settings-nav-label" className="min-w-0 flex-1 truncate text-left">
              {t('preferences.back')}
            </span>
          </Link>
        </div>
      </aside>
      <section className="flex min-w-0 flex-1 flex-col bg-background">
        <header
          className={cn(
            'workspace-titlebar flex shrink-0 items-center gap-2 px-5 text-sm',
            !isMac() && 'native-controls-right',
          )}
        >
          <span className="text-muted-foreground">{t('nav.settings')}</span>
          <ChevronRightIcon aria-hidden="true" className="size-3.5 text-muted-foreground/60" />
          <h1 className="font-medium">
            {extra
              ? t(extra.label)
              : isMedia
                ? t('settings.audio_tools')
                : isLogs
                  ? t('settings.logs')
                  : isModels
                    ? t('modelSettings.models')
                    : t(`preferences.${section}`)}
          </h1>
          {!extra && !isModels && !isLogs && !isMedia && section === 'appearance' && (
            <Button
              size="xs"
              variant="ghost"
              className="ml-auto"
              onClick={() => {
                appearance.update({
                  font: 'inter',
                  scale: 100,
                  glass: true,
                });
                updateTheme({
                  mode: 'dark',
                  light: 'signal',
                  dark: 'signal',
                });
              }}
            >
              <RotateCcwIcon />
              {t('preferences.reset')}
            </Button>
          )}
        </header>
        <SettingsContent
          fullBleed={isOpenApi || isLogs}
          key={
            extra?.to ||
            (isMedia ? 'media' : isLogs ? 'logs' : isModels ? (modelFamily ?? 'models') : section)
          }
        >
          {extra ? (
            <extra.Component />
          ) : isMedia ? (
            <MediaTools />
          ) : isLogs ? (
            <LogSettings />
          ) : isModels ? (
            <>
              <ModelSettings key={modelFamily ?? 'all'} family={modelFamily} />
              {!modelFamily && <MirrorSettings />}
            </>
          ) : (
            <SettingsSection
              icon={section === 'appearance' ? PaletteIcon : Settings2Icon}
              title={t(`preferences.${section}`)}
            >
              {section === 'appearance' ? (
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
                    title={t('preferences.sidebar_expanded')}
                    description={t('preferences.sidebar_expanded_hint')}
                  >
                    <Switch
                      aria-labelledby="sidebar-expanded-label"
                      checked={!workspace.autoCollapseSidebar}
                      onCheckedChange={(keep) => setWorkspace({ autoCollapseSidebar: !keep })}
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
                            fontFamily:
                              font === 'inter' ? '"Inter Variable", sans-serif' : 'system-ui',
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
              ) : (
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
              )}
            </SettingsSection>
          )}
        </SettingsContent>
      </section>
    </div>
  );
}
