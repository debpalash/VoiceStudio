import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from '@tanstack/react-router';
import {
  ArrowLeftIcon,
  BoxesIcon,
  FileTextIcon,
  HeartHandshakeIcon,
  PaletteIcon,
  SearchIcon,
  Settings2Icon,
  SettingsIcon,
  WrenchIcon,
  type LucideIcon,
} from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { runRendererTask } from '@/lib/global-error-recovery';
import { cn } from '@/lib/utils';
import { familyIcons, type ModelFamily } from './model-family';
import {
  fieldKey,
  fieldMatches,
  keysMatch,
  matchingFamilies,
  normalizeSettingsQuery,
  pinnedPages,
  preferenceFields,
  preferenceSections,
  sectionMatches,
  type PreferenceSection,
} from './settings-search';

const navItemClass =
  'group h-9 min-w-0 rounded-lg font-normal transition-colors hover:bg-[var(--sidebar-row-hover)] aria-[current=page]:bg-[var(--sidebar-row-active)] aria-[current=page]:font-semibold';
const navIconClass =
  'size-4 shrink-0 text-muted-foreground transition-[color,filter] duration-150 group-hover:text-sidebar-foreground group-hover:drop-shadow-[0_1px_3px_rgb(0_0_0/20%)]';

const fieldTargets: Record<string, string> = {
  theme: 'theme-label',
  font: 'font-label',
  ui_scale: 'scale-label',
  glass: 'glass-label',
  sidebar_expanded: 'sidebar-expanded-label',
  language: 'language-label',
  review_mode: 'review-mode-label',
};

const systemRoutes = new Set([
  '/settings/performance',
  '/settings/permissions',
  '/settings/network',
  '/settings/sharing',
  '/settings/storage',
  '/settings/credentials',
  '/settings/openapi',
]);
const diagnosticRoutes = new Set(['/settings/diagnostics', '/settings/updates']);

export interface SettingsNavExtra {
  to: string;
  label: string;
  Icon: LucideIcon;
  fields: readonly string[];
}

/** Where the Settings column currently is, for the nav's selected state. */
export type SettingsLocation =
  | { kind: 'section'; section: PreferenceSection }
  | { kind: 'models'; family?: ModelFamily }
  | { kind: 'media' | 'logs' }
  | { kind: 'extra'; to: string };

function NavLabel({ children }: { children: ReactNode }) {
  return (
    <span data-slot="settings-nav-label" className="min-w-0 flex-1 truncate text-left">
      {children}
    </span>
  );
}

function NavLink({
  to,
  label,
  Icon,
  current,
  params,
  className,
}: {
  to: string;
  label: string;
  Icon: LucideIcon;
  current: boolean;
  params?: Record<string, string>;
  className?: string;
}) {
  return (
    <Link
      to={to}
      params={params}
      title={label}
      aria-current={current ? 'page' : undefined}
      className={cn(
        buttonVariants({ variant: 'ghost' }),
        navItemClass,
        'justify-start gap-2 px-2 text-sm',
        className,
      )}
    >
      <Icon className={navIconClass} />
      <NavLabel>{label}</NavLabel>
    </Link>
  );
}

/** The Settings section list with its search box. */
export function SettingsNav({
  extras,
  location,
  onTarget,
}: {
  extras: readonly SettingsNavExtra[];
  location: SettingsLocation;
  onTarget: (id: string) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const needle = normalizeSettingsQuery(query);
  const sections = preferenceSections.filter((section) => sectionMatches(t, section, needle));
  const extraMatches = extras.filter(
    (item) => item.to !== '/settings/support' && keysMatch(t, item.fields, needle),
  );
  const everyday = extraMatches.filter(
    (item) => !systemRoutes.has(item.to) && !diagnosticRoutes.has(item.to),
  );
  const system = extraMatches.filter((item) => systemRoutes.has(item.to));
  const diagnostics = extraMatches.filter((item) => diagnosticRoutes.has(item.to));
  const onModels = location.kind === 'models';
  const families = matchingFamilies(t, needle, onModels);
  const pinned = {
    models: keysMatch(t, pinnedPages.models, needle),
    media: keysMatch(t, pinnedPages.media, needle),
    logs: keysMatch(t, pinnedPages.logs, needle),
  };
  const nothing =
    sections.length === 0 &&
    extraMatches.length === 0 &&
    !pinned.models &&
    !pinned.media &&
    !pinned.logs;
  const sectionTo = (section: PreferenceSection) =>
    section === 'appearance' ? '/settings/appearance' : '/settings/general';
  const link = ({ to, label, Icon }: SettingsNavExtra) => (
    <NavLink
      key={to}
      to={to}
      label={t(to === '/settings/diagnostics' ? 'about.diagnostics' : label)}
      Icon={Icon}
      current={location.kind === 'extra' && location.to === to}
    />
  );

  return (
    <aside className="flex w-[clamp(11rem,20vw,17rem)] shrink-0 flex-col border-r border-sidebar-border/50 bg-sidebar text-sidebar-foreground shadow-[inset_-1px_0_0_color-mix(in_oklab,var(--foreground)_2%,transparent)]">
      <div
        data-slot="settings-column-header"
        className="workspace-titlebar flex h-12 shrink-0 items-center gap-2 px-3"
      >
        <div className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/12 text-primary">
          <SettingsIcon aria-hidden="true" className="size-3.5" />
        </div>
        <h2 className="min-w-0 truncate text-sm font-semibold">{t('nav.settings')}</h2>
      </div>
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
          {sections.map((section) => (
            <div key={section} className="flex flex-col gap-0.5">
              <NavLink
                to={sectionTo(section)}
                label={t(`preferences.${section}`)}
                Icon={section === 'appearance' ? PaletteIcon : Settings2Icon}
                current={location.kind === 'section' && location.section === section}
              />
              {needle &&
                preferenceFields[section]
                  .filter((field) => fieldMatches(t, field, needle))
                  .map((field) => (
                    <Button
                      key={field}
                      variant="ghost"
                      className={cn(
                        navItemClass,
                        'justify-start pl-8 text-xs text-muted-foreground',
                      )}
                      onClick={() => {
                        onTarget(fieldTargets[field]);
                        setQuery('');
                        runRendererTask('Open settings search result', () =>
                          navigate({ to: sectionTo(section) }),
                        );
                      }}
                    >
                      <NavLabel>{t(fieldKey(field))}</NavLabel>
                    </Button>
                  ))}
            </div>
          ))}
          {everyday.map(link)}
          {(pinned.models || pinned.media || system.length > 0) && (
            <div
              role="group"
              aria-label={t('preferences.audio_tools_origin_system')}
              className="mt-3 flex flex-col gap-0.5 border-t border-sidebar-border/50 pt-2"
            >
              <p
                aria-hidden="true"
                className="px-2 py-1 text-xs font-semibold text-muted-foreground"
              >
                {t('preferences.audio_tools_origin_system')}
              </p>
              {pinned.models && (
                <NavLink
                  to="/settings/models"
                  label={t('modelSettings.models')}
                  Icon={BoxesIcon}
                  current={onModels && !location.family}
                />
              )}
              {pinned.models &&
                families.map((family) => (
                  <NavLink
                    key={family}
                    to="/settings/models/$family"
                    params={{ family }}
                    label={t('engineSidebar.' + family)}
                    Icon={familyIcons[family]}
                    current={onModels && location.family === family}
                    className="pl-7 text-muted-foreground"
                  />
                ))}
              {system.map(link)}
              {pinned.media && (
                <NavLink
                  to="/settings/media"
                  label={t('settings.audio_tools')}
                  Icon={WrenchIcon}
                  current={location.kind === 'media'}
                />
              )}
            </div>
          )}
          {(pinned.logs || diagnostics.length > 0) && (
            <div
              role="group"
              aria-label={t('about.diagnostics')}
              className="mt-3 flex flex-col gap-0.5 border-t border-sidebar-border/50 pt-2"
            >
              <p
                aria-hidden="true"
                className="px-2 py-1 text-xs font-semibold text-muted-foreground"
              >
                {t('about.diagnostics')}
              </p>
              {pinned.logs && (
                <NavLink
                  to="/settings/logs"
                  label={t('settings.logs')}
                  Icon={FileTextIcon}
                  current={location.kind === 'logs'}
                />
              )}
              {diagnostics.map(link)}
            </div>
          )}
        </nav>
        {nothing && (
          <p role="status" className="px-2 py-6 text-center text-xs text-muted-foreground">
            {t('preferences.no_matches')}
          </p>
        )}
      </div>
      <div className="shrink-0 space-y-1.5 border-t border-sidebar-border/50 bg-background/15 p-3">
        <Link
          to="/settings/support"
          title={t('donate.title')}
          aria-current={
            location.kind === 'extra' && location.to === '/settings/support' ? 'page' : undefined
          }
          className={cn(
            buttonVariants({ variant: 'ghost' }),
            'group relative w-full justify-start overflow-hidden border border-primary/20 bg-gradient-to-r from-primary/15 via-primary/8 to-transparent text-foreground shadow-[inset_0_1px_0_rgb(255_255_255/10%)] transition-[border-color,background-color,box-shadow] hover:border-primary/35 hover:from-primary/20 hover:shadow-[0_8px_24px_-16px_var(--primary)] aria-[current=page]:border-primary/40 aria-[current=page]:bg-primary/15',
          )}
        >
          <HeartHandshakeIcon className="size-4 shrink-0 text-primary transition-transform duration-200 group-hover:scale-110 motion-reduce:transition-none" />
          <NavLabel>{t('donate.title')}</NavLabel>
        </Link>
        <NavLink
          to="/clone"
          label={t('preferences.back')}
          Icon={ArrowLeftIcon}
          current={false}
          className="w-full"
        />
      </div>
    </aside>
  );
}
