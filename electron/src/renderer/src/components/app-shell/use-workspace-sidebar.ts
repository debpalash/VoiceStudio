import { useSyncExternalStore } from 'react';
import { useRouterState } from '@tanstack/react-router';
import { setWorkspace, useWorkspace } from '@/lib/store/workspace';

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
  '/calls',
  '/settings',
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

export function useWorkspaceSidebarState() {
  const { libraryOpen, expandedLibraryContext, autoCollapseSidebar } = useWorkspace();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const compactViewport = useCompactViewport();
  const compactContext = `${pathname}:${compactViewport}`;
  const forceExpanded = expandedLibraryContext === compactContext;
  // Auto-collapse is a preference (Settings → Appearance → Keep sidebar
  // expanded): off means a secondary workspace on a narrow window keeps the
  // full sidebar. A workspace that shows the voice library itself still
  // collapses the duplicate.
  const autoCollapse =
    autoCollapseSidebar !== false && compactViewport && routeHasSecondarySidebar(pathname);
  const compact =
    !libraryOpen || ((routeOwnsVoiceLibrary(pathname) || autoCollapse) && !forceExpanded);
  const setOpen = (open: boolean) =>
    setWorkspace({ libraryOpen: open, expandedLibraryContext: open ? compactContext : null });
  return {
    compact,
    compactViewport,
    forceExpanded,
    secondaryWorkspace: routeHasSecondarySidebar(pathname),
    setOpen,
  };
}
