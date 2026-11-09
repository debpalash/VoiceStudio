import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { PanelLeftIcon, PanelLeftOpenIcon, SettingsIcon } from 'lucide-react';
import { brandIcon, brandArtwork } from '@/lib/brand';
import { appVersion, isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { Button, buttonVariants } from '@/components/ui/button';
import { usePaneResize } from '@/hooks/use-pane-resize';
import { useWindowFullscreen } from '@/hooks/use-window-fullscreen';
import { MAC_TRAFFIC_LIGHTS_BAND } from '@shared/utils/windowChrome';
import { useWorkspace } from '@/lib/store/workspace';
import { useWorkspaceSidebarState } from './use-workspace-sidebar';
import { WorkspaceNavigation } from './workspace-menu';
import { StatusBar } from './status-bar';

export function versionTranslationKey(development: boolean) {
  return development ? ('app.version_dev' as const) : ('app.version' as const);
}

export function WorkspaceSidebar() {
  const { t } = useTranslation();
  const mac = isMac();
  // Fullscreen hides the traffic lights, so their reserved corner is given back to the brand.
  const trafficLights = mac && !useWindowFullscreen();
  const versionLabel = t(versionTranslationKey(import.meta.env.DEV), {
    version: appVersion(),
  });
  const { libraryOpen } = useWorkspace();
  const { compact, compactViewport, forceExpanded, secondaryWorkspace, setOpen } =
    useWorkspaceSidebarState();
  const sidebarResize = usePaneResize({
    storageKey: 'voicestudio.nav-width',
    side: 'left',
    minimum: 196,
    initial: 216,
    maximum: 288,
    reserve: compactViewport && secondaryWorkspace && forceExpanded ? 520 : 640,
    enabled: libraryOpen,
  });
  return (
    <>
      {compact && (
        <aside
          aria-label={t('clone.saved_profiles')}
          data-slot="compact-main-sidebar"
          onDoubleClick={() => setOpen(true)}
          className={cn(
            'brand-sidebar relative isolate grid h-full min-h-0 shrink-0 grid-rows-[auto_minmax(0,1fr)_auto_auto] overflow-hidden bg-sidebar',
            mac ? 'w-16' : 'w-12 border-r border-border/50',
          )}
        >
          {mac && (
            <span
              aria-hidden="true"
              data-slot="compact-sidebar-divider"
              style={{ top: 48 + (trafficLights ? MAC_TRAFFIC_LIGHTS_BAND : 0) }}
              className="pointer-events-none absolute right-0 bottom-0 w-px bg-border/50"
            />
          )}
          <div
            data-slot="titlebar-row"
            style={trafficLights ? { paddingTop: MAC_TRAFFIC_LIGHTS_BAND } : undefined}
            className="workspace-titlebar box-content flex h-12 shrink-0 items-center justify-center"
          >
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={t('clone.toggle_sidebar')}
              title={t('clone.toggle_sidebar')}
              aria-expanded={false}
              onClick={() => setOpen(true)}
              className="group/rail relative"
            >
              <img
                src={brandIcon}
                alt=""
                data-slot="rail-brand"
                className="size-5 transition-opacity duration-150 group-hover/rail:opacity-0 group-focus-visible/rail:opacity-0 motion-reduce:transition-none"
              />
              <PanelLeftOpenIcon
                aria-hidden="true"
                className="absolute size-4 opacity-0 transition-opacity duration-150 group-hover/rail:opacity-100 group-focus-visible/rail:opacity-100 motion-reduce:transition-none"
              />
            </Button>
          </div>
          <WorkspaceNavigation compact />
          {!mac && <StatusBar compact />}
          <div
            className={cn(
              // One centered column, like the navigation above it.
              'flex shrink-0 flex-col items-center gap-1 border-t border-border/50 py-2',
            )}
          >
            <Link
              to="/settings"
              aria-label={t('nav.settings')}
              title={t('nav.settings')}
              className={cn(
                buttonVariants({ variant: 'ghost', size: 'icon-sm' }),
                'workspace-nav-item',
              )}
            >
              <SettingsIcon
                className="workspace-nav-icon"
                data-tone="settings"
                data-motion="turn"
              />
            </Link>
            {mac && <StatusBar compact inline />}
          </div>
        </aside>
      )}
      {libraryOpen && !compact && (
        <aside
          ref={sidebarResize.host}
          style={{ width: sidebarResize.width }}
          aria-label={t('app.name')}
          className="brand-sidebar relative isolate grid h-full min-h-0 min-w-0 shrink-0 grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden border-r border-border/50 bg-sidebar"
        >
          <img
            src={brandArtwork}
            alt=""
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-40 w-full object-cover object-right opacity-25 [mask-image:linear-gradient(black,transparent)] dark:opacity-40"
          />
          <header
            data-slot="titlebar-row"
            // The traffic lights own a strip above the brand (one shared constant
            // with the main process); this drag row pads past it, never under it.
            style={trafficLights ? { paddingTop: MAC_TRAFFIC_LIGHTS_BAND } : undefined}
            className="workspace-titlebar box-content flex h-14 shrink-0 items-center gap-2 px-3"
          >
            <Link
              to="/"
              aria-label={`${t('app.name')} ${versionLabel}`}
              className="flex min-w-0 flex-1 items-center gap-2 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <img src={brandIcon} alt="" className="size-6 shrink-0" />
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="truncate text-[15px] font-semibold tracking-tight">
                  {t('app.name')}
                </span>
                <span className="mt-0.5 truncate text-[11px] leading-none text-muted-foreground tabular-nums">
                  {versionLabel}
                </span>
              </span>
            </Link>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={t('common.close')}
              title={t('common.close')}
              onClick={() => setOpen(false)}
            >
              <PanelLeftIcon aria-hidden="true" />
            </Button>
          </header>
          <div
            {...sidebarResize.separatorProps}
            aria-label={t('app.name')}
            className="absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize touch-none outline-none hover:bg-primary/15 focus-visible:bg-primary/30"
          />
          <div className="min-h-0 min-w-0 overflow-y-auto overflow-x-hidden">
            <WorkspaceNavigation />
          </div>
          <div className="flex min-w-0 shrink-0 flex-col border-t border-border/50">
            <StatusBar
              footerLeading={
                mac ? (
                  <Link
                    to="/settings"
                    aria-label={t('nav.settings')}
                    title={t('nav.settings')}
                    className={cn(
                      buttonVariants({ variant: 'ghost', size: 'sm' }),
                      // Settings may shrink (label truncates) so long translations never push
                      // the footer row wider than the pane.
                      'workspace-nav-item h-7 min-w-0 shrink gap-1.5 px-2 text-xs',
                    )}
                  >
                    <SettingsIcon
                      className="workspace-nav-icon"
                      data-tone="settings"
                      data-motion="turn"
                    />
                    <span className="hidden min-w-0 truncate @[11rem]/engines:inline">
                      {t('nav.settings')}
                    </span>
                  </Link>
                ) : undefined
              }
            />
            {!mac && (
              <div className="flex items-center justify-between gap-2 border-t border-border/50 px-3 py-2">
                <Link
                  to="/settings"
                  className={cn(
                    buttonVariants({ variant: 'ghost', size: 'sm' }),
                    'workspace-nav-item',
                  )}
                >
                  <SettingsIcon
                    className="workspace-nav-icon"
                    data-tone="settings"
                    data-motion="turn"
                  />
                  {t('nav.settings')}
                </Link>
              </div>
            )}
          </div>
        </aside>
      )}
    </>
  );
}
