import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function AgentDockFrame({ label, expanded = true, children }: {
  label: string; expanded?: boolean; children: ReactNode;
}) {
  return <section aria-label={label} className={cn(
    'relative z-40 flex min-h-0 shrink-0 flex-col border-t border-sidebar-border bg-sidebar text-sidebar-foreground shadow-[0_-8px_24px_rgb(0_0_0/12%)]',
    expanded && 'h-[clamp(20rem,42vh,28rem)]',
  )}>{children}</section>;
}
