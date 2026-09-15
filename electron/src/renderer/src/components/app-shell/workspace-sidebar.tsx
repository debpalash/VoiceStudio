import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { PanelLeftIcon, PanelLeftOpenIcon, SettingsIcon } from 'lucide-react';
import { brandIcon, brandArtwork } from '@/lib/brand';
import { getBridge, isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { Button, buttonVariants } from '@/components/ui/button';
import { usePaneResize } from '@/hooks/use-pane-resize';
import { useWorkspace } from '@/lib/store/workspace';
import { VoicesSidebar } from '@/features/clone/voices-sidebar';
import { WorkspaceNavigation } from './workspace-menu';
import { StatusBar } from './status-bar';
import { SystemNotifications } from './system-notifications';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { useWorkspaceSidebarState } from './use-workspace-sidebar';

export function WorkspaceSidebar() {
  const backend = useBackendStatus();
  const showCompactBrand = ['win32', 'linux'].includes(getBridge()?.app.platform ?? '');
  const { t } = useTranslation();
  const { libraryOpen, libraryTab } = useWorkspace();
  const { compact, compactViewport, forceExpanded, secondaryWorkspace, setOpen } =
    useWorkspaceSidebarState();
  const sidebarResize = usePaneResize({
    storageKey: 'voicestudio.library-width',
    side: 'left',
    minimum: 220,
    initial: 256,
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
          className="brand-sidebar relative isolate grid h-dvh min-h-0 w-12 shrink-0 grid-rows-[auto_minmax(0,1fr)_auto_auto] overflow-hidden border-r border-border/50 bg-sidebar"
        >
          {showCompactBrand ? (
            <div className="workspace-titlebar flex w-full shrink-0 items-center justify-center">
              <img src={brandIcon} alt={t('app.name')} className="size-6 shrink-0" />
            </div>
          ) : (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('clone.toggle_sidebar')}
              aria-expanded={false}
              onClick={() => {
                setOpen(true);
              }}
              className={cn(
                'workspace-titlebar h-auto w-full shrink-0 rounded-none outline-none focus-visible:ring-2 focus-visible:ring-ring',
                isMac() && 'pt-5',
              )}
            >
              <PanelLeftOpenIcon className="size-5" aria-hidden="true" />
            </Button>
          )}
          <WorkspaceNavigation compact />
          <StatusBar compact />
          <div className="flex shrink-0 flex-col items-center gap-1 border-t border-border/50 py-2">
            <Link
              to="/settings"
              aria-label={t('nav.settings')}
              title={t('nav.settings')}
              className={buttonVariants({ variant: 'ghost', size: 'icon-sm' })}
            >
              <SettingsIcon />
            </Link>
            <SystemNotifications enabled={backend.stage === 'ready'} compact />
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
              isMac() && 'pl-20',
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
                setOpen(false);
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
            <StatusBar />
            <div className="flex items-center justify-between gap-2 border-t border-border/50 px-3 py-2">
              <Link to="/settings" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>
                <SettingsIcon />
                {t('nav.settings')}
              </Link>
              <SystemNotifications enabled={backend.stage === 'ready'} />
            </div>
          </div>
        </aside>
      )}
    </>
  );
}
