import { SupportShortcut } from './support-shortcut';
import { SidebarToggle } from './sidebar-toggle';
import type { ReactNode } from 'react';
import { SearchIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { isMac } from '@/components/bridge';

/**
 * True when a page's own pane is titled exactly like the page, so the pane
 * hides its duplicate and the title is read once, top left, on every page.
 */
export function paneTitleInShell(row: HTMLElement, title: string): boolean {
  const shell = row.closest('[data-slot="workspace-content"]');
  const heading = shell?.querySelector('[data-slot="workspace-header"] h1');
  return Boolean(title.trim() && heading?.textContent?.trim() === title.trim());
}

export function WorkspaceHeader({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <header
      data-slot="workspace-header"
      className={cn(
        'workspace-titlebar flex shrink-0 items-center gap-3 border-b border-border/50 px-5',
        !isMac() && 'native-controls-right',
      )}
    >
      {!isMac() && <SidebarToggle />}
      {/* The page's own items fill the bar, so one pushed right with ms-auto sits beside
          search instead of splitting the free space with it. */}
      <div
        className={cn(
          'flex min-w-0 flex-1 items-center gap-3',
          // One title style and place on every page: the h1 always leads, top left.
          '[&>h1]:min-w-0 [&>h1]:truncate [&>h1]:text-[13px] [&>h1]:font-medium [&>h1]:text-foreground/90',
        )}
      >
        {children}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <SupportShortcut />
        <Button
          variant="ghost"
          size="sm"
          className="h-7 shrink-0 gap-1 px-1.5 text-muted-foreground hover:text-foreground"
          aria-label={t('preferences.search')}
          title={isMac() ? 'Command + K' : 'Ctrl + K'}
          onClick={() => window.dispatchEvent(new Event('voicestudio:commands'))}
        >
          <SearchIcon />
          <span className="hidden text-xs font-medium lg:inline">{isMac() ? '⌘K' : 'Ctrl K'}</span>
        </Button>
      </div>
    </header>
  );
}
