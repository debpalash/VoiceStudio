import { Suspense } from 'react';
import { useRouterState } from '@tanstack/react-router';
import { LibraryIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { retryableLazy } from '@/lib/lazy-boundary';
import { setWorkspace, useWorkspace } from '@/lib/store/workspace';
import { LIBRARY_ROUTES } from './use-workspace-sidebar';

const VoicesSidebar = retryableLazy(
  () => import('@/features/clone/voices-sidebar').then((m) => ({ default: m.VoicesSidebar })),
  null,
);

/** The user's saved voices and recent takes, beside the slim navigation column. */
export function ContextSidebar() {
  const { t } = useTranslation();
  const { libraryOpen, libraryTab } = useWorkspace();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (!LIBRARY_ROUTES.has(pathname.replace(/\/+$/, '') || '/')) return null;
  return (
    <SecondarySidebar
      title={t('nav.voice')}
      icon={LibraryIcon}
      variant="library"
      className="flex flex-col overflow-hidden p-0"
      collapsed={!libraryOpen}
      onCollapsedChange={(collapsed) => setWorkspace({ libraryOpen: !collapsed })}
    >
      {libraryOpen && (
        <Suspense fallback={null}>
          <VoicesSidebar initialTab={libraryTab} />
        </Suspense>
      )}
    </SecondarySidebar>
  );
}
