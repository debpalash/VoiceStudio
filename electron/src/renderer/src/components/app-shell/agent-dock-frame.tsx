import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { useDockResize } from '@/hooks/use-dock-resize';

export function AgentDockFrame({
  label,
  expanded = true,
  resizable = false,
  resizeStorageKey = 'voicestudio.agent-dock-height',
  className,
  children,
}: {
  label: string;
  expanded?: boolean;
  resizable?: boolean;
  resizeStorageKey?: string;
  className?: string;
  children: ReactNode;
}) {
  const resize = useDockResize({ storageKey: resizeStorageKey, enabled: resizable });
  return (
    <section
      ref={resize.host}
      aria-label={label}
      style={resizable && expanded ? { height: resize.height } : undefined}
      className={cn(
        'relative z-40 flex min-h-0 shrink-0 flex-col border-t border-sidebar-border bg-sidebar text-sidebar-foreground shadow-[0_-8px_24px_rgb(0_0_0/12%)]',
        expanded && 'h-[clamp(12rem,24vh,17rem)]',
        className,
      )}
    >
      {resizable && expanded && (
        <div
          {...resize.separatorProps}
          aria-label={label}
          className="workspace-resize-handle absolute inset-x-0 -top-1.5 z-20 h-3 cursor-row-resize touch-none outline-none"
        />
      )}
      {children}
    </section>
  );
}
