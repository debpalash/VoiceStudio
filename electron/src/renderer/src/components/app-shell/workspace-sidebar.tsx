import { Link, useRouterState } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { PanelLeftIcon, PanelLeftOpenIcon, SettingsIcon } from 'lucide-react';
import { brandIcon, brandArtwork } from '@/lib/brand';
import { isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { Button, buttonVariants } from '@/components/ui/button';
import { usePaneResize } from '@/hooks/use-pane-resize';
import { setWorkspace, useWorkspace } from '@/lib/store/workspace';
import { VoicesSidebar } from '@/features/clone/voices-sidebar';
import { WorkspaceNavigation } from './workspace-menu';
import { StatusBar } from './status-bar';
import { SystemNotifications } from './system-notifications';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { useState, useSyncExternalStore } from 'react';

const SECONDARY_ROUTES = new Set([
  '/stories',
  '/audiobook',
  '/tools',
  '/batch',
  '/gallery',
  '/personas',
  '/projects',
  '/dub',
  '/design',
  '/transcriptions',
]);
// A local-controls pane needs enough room for the actual workspace. At the
// default desktop window, preserve navigation as a rail and restore the full
// voice library automatically once both it and a local-controls pane leave a
// useful editing canvas. Browser zoom and Windows display scaling are included
// in the CSS viewport width, so this threshold also covers high-DPI layouts.
const COMPACT_QUERY = '(max-width: 1680px)';

function routeHasSecondarySidebar(pathname: string): boolean {
  const normalized = pathname.replace(/\/+$/, '') || '/';
  return [...SECONDARY_ROUTES].some(
    (route) => normalized === route || normalized.startsWith(`${route}/`),
  );
}

function routeOwnsVoiceLibrary(pathname: string): boolean {
  const normalized = pathname.replace(/\/+$/, '') || '/';
  return normalized === '/personas' || normalized.startsWith('/personas/');
}

function useCompactViewport(): boolean {
  return useSyncExternalStore(
    (notify) => {
      const query = window.matchMedia(COMPACT_QUERY);
      query.addEventListener('change', notify);
      return () => query.removeEventListener('change', notify);
    },
    () => window.matchMedia(COMPACT_QUERY).matches,
    () => false,
  );
}

export function WorkspaceSidebar() {
  const backend = useBackendStatus();
  const { t } = useTranslation();
  const mac = isMac();
  const { libraryOpen, libraryTab } = useWorkspace();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const compactViewport = useCompactViewport();
  const compactContext = `${pathname}:${compactViewport}`;
  const [expandedContext, setExpandedContext] = useState<string | null>(null);
  const forceExpanded = expandedContext === compactContext;
  const ownsVoiceLibrary = routeOwnsVoiceLibrary(pathname);
  const compact =
    !libraryOpen ||
    ((ownsVoiceLibrary || (compactViewport && routeHasSecondarySidebar(pathname))) &&
      !forceExpanded);
  const secondaryWorkspace = routeHasSecondarySidebar(pathname);
  const setLibraryOpen = (libraryOpen: boolean) => setWorkspace({ libraryOpen });
  const sidebarResize = usePaneResize({
    storageKey: 'voicestudio.library-width',
    side: 'left',
    minimum: mac ? 288 : 220,
    initial: mac ? 288 : 256,
    maximum: 360,
    reserve: compactViewport && secondaryWorkspace && forceExpanded ? 520 : 640,
    enabled: libraryOpen,
  });
  return (
    <>
      {compact && (
        <aside
          aria-label={t('clone.saved_profiles')}
          data-slot="compact-main-sidebar"
          className={cn(
            'brand-sidebar relative isolate grid h-dvh min-h-0 shrink-0 grid-rows-[auto_minmax(0,1fr)_auto_auto] overflow-hidden bg-sidebar',
            mac ? 'w-16' : 'w-12 border-r border-border/50',
          )}
        >
          {mac && (
            <span
              aria-hidden="true"
              data-slot="compact-sidebar-divider"
              className="pointer-events-none absolute top-[72px] right-0 bottom-0 w-px bg-border/50"
            />
          )}
          <div
            className={cn(
              'workspace-titlebar flex shrink-0 justify-center',
              mac ? 'min-h-[72px] items-end pb-1' : 'h-12 items-center',
            )}
          >
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('clone.toggle_sidebar')}
              aria-expanded={false}
              onClick={() => {
                setExpandedContext(compactContext);
                setLibraryOpen(true);
              }}
              className="shrink-0 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <PanelLeftOpenIcon className="size-5" aria-hidden="true" />
            </Button>
          </div>
          <WorkspaceNavigation compact />
          {!mac && <StatusBar compact />}
          <div
            className={cn(
              'shrink-0 border-t border-border/50 py-2',
              mac ? 'grid grid-cols-2 items-center gap-1 px-1' : 'flex flex-col items-center gap-1',
            )}
          >
            <Link
              to="/settings"
              aria-label={t('nav.settings')}
              title={t('nav.settings')}
              className={buttonVariants({
                variant: 'ghost',
                size: mac ? 'icon-xs' : 'icon-sm',
              })}
            >
              <SettingsIcon />
            </Link>
            {mac && <StatusBar compact inline />}
            {!mac && <SystemNotifications enabled={backend.stage === 'ready'} compact />}
          </div>
        </aside>
      )}
      {libraryOpen && !compact && (
        <aside
          ref={sidebarResize.host}
          style={{ width: sidebarResize.width }}
          aria-label={t('clone.saved_profiles')}
          className="brand-sidebar relative isolate grid h-full min-h-0 min-w-0 shrink-0 grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden border-r border-border/50 bg-sidebar"
        >
          <img
            src={brandArtwork}
            alt=""
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-40 w-full object-cover object-right opacity-25 [mask-image:linear-gradient(black,transparent)] dark:opacity-40"
          />
          <header
            className={cn(
              'workspace-titlebar flex shrink-0 items-center gap-2 px-4',
              mac && 'pl-24',
            )}
          >
            <Link
              to="/"
              aria-label={t('app.name')}
              className="flex min-w-0 flex-1 items-center gap-2 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <img src={brandIcon} alt="" className="size-6 shrink-0" />
              <span className="truncate text-base font-semibold">{t('app.name')}</span>
            </Link>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('common.close')}
              onClick={() => {
                setExpandedContext(null);
                setLibraryOpen(false);
              }}
            >
              <PanelLeftIcon />
            </Button>
          </header>
          <div
            {...sidebarResize.separatorProps}
            aria-label={t('clone.saved_profiles')}
            className="absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize touch-none outline-none hover:bg-primary/15 focus-visible:bg-primary/30"
          />
          <VoicesSidebar key={libraryTab} initialTab={libraryTab} />
          <div className="flex min-w-0 shrink-0 flex-col border-t border-border/50">
            <WorkspaceNavigation />
            <StatusBar
              footerLeading={
                mac ? (
                  <Link
                    to="/settings"
                    aria-label={t('nav.settings')}
                    title={t('nav.settings')}
                    className={buttonVariants({ variant: 'ghost', size: 'icon-xs' })}
                  >
                    <SettingsIcon />
                  </Link>
                ) : undefined
              }
            />
            {!mac && (
              <div className="flex items-center justify-between gap-2 border-t border-border/50 px-3 py-2">
                <Link to="/settings" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>
                  <SettingsIcon />
                  {t('nav.settings')}
                </Link>
                <SystemNotifications enabled={backend.stage === 'ready'} />
              </div>
            )}
          </div>
        </aside>
      )}
    </>
  );
}
