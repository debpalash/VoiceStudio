import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { PanelLeftIcon, PanelLeftOpenIcon, SettingsIcon } from 'lucide-react';
import { brandIcon, brandArtwork } from '@/lib/brand';
import { isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { Button, buttonVariants } from '@/components/ui/button';
import { usePaneResize } from '@/hooks/use-pane-resize';
import { useWorkspace } from '@/lib/store/workspace';
import { useWorkspaceSidebarState } from './use-workspace-sidebar';
import { VoicesSidebar } from '@/features/clone/voices-sidebar';
import { WorkspaceNavigation } from './workspace-menu';
import { StatusBar } from './status-bar';
import { SystemNotifications } from './system-notifications';
import { useBackendStatus } from '@/hooks/use-backend-status';

export function WorkspaceSidebar() {
  const backend = useBackendStatus();
  const { t } = useTranslation();
  const mac = isMac();
  const { libraryOpen, libraryTab } = useWorkspace();
  // One source of truth for "rail or full sidebar": the header toggle and the
  // sidebar's own buttons used to keep separate copies of this state.
  const { compact, compactViewport, forceExpanded, secondaryWorkspace, setOpen } =
    useWorkspaceSidebarState();
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
            {/* The rail can always reopen itself, on every platform: the
                brand icon alone gave Windows/Linux no local control. */}
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('clone.toggle_sidebar')}
              aria-expanded={false}
              onClick={() => setOpen(true)}
              className="shrink-0 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {mac ? (
                <PanelLeftOpenIcon className="size-5" aria-hidden="true" />
              ) : (
                <img src={brandIcon} alt="" className="size-6 shrink-0" />
              )}
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
          className="brand-sidebar relative isolate grid h-full min-h-0 min-w-0 shrink-0 grid-cols-[minmax(0,1fr)] grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden border-r border-border/50 bg-sidebar"
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
              onClick={() => setOpen(false)}
            >
              <PanelLeftIcon />
            </Button>
          </header>
          <div
            {...sidebarResize.separatorProps}
            aria-label={t('clone.saved_profiles')}
            className="absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize touch-none outline-none hover:bg-primary/15 focus-visible:bg-primary/30"
          />
          {/* Navigation first, like the compact rail: it stays at a fixed
              position under the header instead of riding the library's height. */}
          <div className="min-w-0 shrink-0 border-b border-border/50">
            <WorkspaceNavigation />
          </div>
          <VoicesSidebar key={libraryTab} initialTab={libraryTab} />
          <div className="flex min-w-0 shrink-0 flex-col border-t border-border/50">
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
