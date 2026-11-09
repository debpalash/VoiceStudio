import { WorkspaceSidebar } from './workspace-sidebar';
import { ContextSidebar } from './context-sidebar';
import { CommandPalette } from '@/components/command-palette';
import { Outlet, useRouterState } from '@tanstack/react-router';
import { BackendGate } from '../backend-gate';
import { RepairAgentDock } from './repair-agent-dock';
import { isMac } from '../bridge';
import { cn } from '@/lib/utils';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { AccountCorner } from './account-corner';
import { NowPlayingDock } from '@/components/now-playing-dock';

export function AppShell() {
  const backend = useBackendStatus();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const shellOnly = pathname.startsWith('/settings') || pathname === '/pro';
  // Settings pads its own header (account-corner-safe); every other page, /pro
  // included, gets the shared clearance so its search button never sits under
  // the account corner and notifications bell.
  const macWorkspace = isMac() && !pathname.startsWith('/settings');
  const SettingsWorkspace = pathname === '/settings/openapi' ? 'div' : 'main';
  return (
    <div
      className={cn(
        'app-surface relative flex h-full flex-col bg-background text-foreground',
        macWorkspace && 'macos-notification-safe-area',
      )}
    >
      <div className="flex min-h-0 flex-1">
        {shellOnly ? (
          <>
            <CommandPalette />
            <WorkspaceSidebar />
            <SettingsWorkspace
              data-slot="workspace-content"
              role={pathname === '/settings/openapi' ? 'main' : undefined}
              className="@container relative flex min-w-0 flex-1 flex-col overflow-hidden"
            >
              <div className="min-h-0 flex-1 overflow-hidden">
                <Outlet />
              </div>
              <RepairAgentDock />
            </SettingsWorkspace>
          </>
        ) : (
          <BackendGate repairDock={<RepairAgentDock />}>
            <CommandPalette />
            <WorkspaceSidebar />
            <ContextSidebar />
            <main
              data-slot="workspace-content"
              className="@container relative flex min-w-0 flex-1 flex-col overflow-hidden"
            >
              <div className="min-h-0 flex-1 overflow-hidden">
                <Outlet />
              </div>
              <RepairAgentDock />
            </main>
          </BackendGate>
        )}
      </div>
      <NowPlayingDock />
      {/* Native drag-region hit testing follows document order. Keep these
          no-drag controls after every workspace titlebar, outside BackendGate. */}
      {/* Top-right corner on every page and platform: account, then the notifications bell. */}
      <AccountCorner notificationsEnabled={isBackendReachable(backend.stage)} />
    </div>
  );
}
