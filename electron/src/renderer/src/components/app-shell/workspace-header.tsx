import { SidebarToggle } from './sidebar-toggle';
import type { ReactNode } from 'react';
import { SearchIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { isMac } from '@/components/bridge';

export function WorkspaceHeader({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <header
      className={cn(
        'workspace-titlebar flex shrink-0 items-center gap-3 border-b border-border/50 px-5',
        !isMac() && 'native-controls-right',
      )}
    >
      {!isMac() && <SidebarToggle />}
      {children}
      <Button
        variant="ghost"
        size="sm"
        className="group ml-auto h-8 shrink-0 gap-2 rounded-lg border border-transparent px-2.5 text-muted-foreground transition-[color,background-color,border-color,box-shadow] duration-150 hover:border-white/10 hover:bg-white/[0.07] hover:text-foreground hover:shadow-[0_6px_18px_-12px_hsl(var(--foreground)/0.4),inset_0_1px_0_hsl(0_0%_100%/0.08)]"
        aria-label={t('preferences.search')}
        title={isMac() ? 'Command + K' : 'Ctrl + K'}
        onClick={() => window.dispatchEvent(new Event('voicestudio:commands'))}
      >
        <SearchIcon className="transition-colors duration-150" />
        <span className="hidden text-xs lg:inline">{isMac() ? '⌘K' : 'Ctrl K'}</span>
      </Button>
    </header>
  );
}
