import { useId, useState, type ReactNode } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ListRank } from './linking';

/** One engine (or standalone model) on a Settings -> Models family page. */
export interface FamilyItem {
  key: string;
  /** Anchor id of the row heading (Settings search and deep links focus it). */
  anchorId: string;
  name: string;
  summary?: ReactNode;
  badges?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  /** Always-visible blocks under the row: failures, access help. */
  notices?: ReactNode;
  /** Expandable details: hosted models, setup snippet, docs, engine test. */
  body?: ReactNode;
  defaultOpen?: boolean;
  search: readonly (string | null | undefined)[];
  /** Search text of hosted models; a match opens the row. */
  childSearch?: readonly (string | null | undefined)[];
  rank: ListRank;
  /** Can never run on this device: listed under the folded group. */
  folded?: boolean;
  /** A model row with install actions only; hidden where the model store is shown. */
  installOnly?: boolean;
}

export function FamilyRow({ item, forceOpen = false }: { item: FamilyItem; forceOpen?: boolean }) {
  // null until the user toggles: the default then follows late-arriving data
  // (the active engine's models load after the engine list).
  const [openState, setOpen] = useState<boolean | null>(null);
  const panelId = useId();
  const expandable = Boolean(item.body);
  const open = expandable && ((openState ?? Boolean(item.defaultOpen)) || forceOpen);
  return (
    <div
      data-slot="model-row"
      data-active={item.rank.active || undefined}
      className="relative px-3 py-2.5 transition-colors duration-150 hover:bg-muted/25 data-[active]:bg-primary/[0.035] @xl:px-4"
    >
      {item.rank.active && (
        <span
          aria-hidden="true"
          className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-primary"
        />
      )}
      <div className="flex min-w-0 flex-col gap-2 @2xl:flex-row @2xl:items-center @2xl:gap-6">
        <div className="flex min-w-0 flex-1 items-start gap-1.5">
          {expandable ? (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={panelId}
              onClick={() => setOpen(!open)}
              className="-m-1 mt-px grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              aria-labelledby={item.anchorId}
            >
              <ChevronRightIcon
                aria-hidden="true"
                className={cn(
                  'size-4 transition-transform duration-150 motion-reduce:transition-none',
                  open && 'rotate-90',
                )}
              />
            </button>
          ) : (
            <span aria-hidden="true" className="size-4 shrink-0" />
          )}
          <div className="min-w-0 flex-1 space-y-0.5">
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <h3
                id={item.anchorId}
                tabIndex={-1}
                className="min-w-0 text-sm font-medium tracking-[-0.005em] outline-none [overflow-wrap:anywhere]"
              >
                {item.name}
              </h3>
              {item.badges}
            </div>
            {item.summary && (
              <div
                className="line-clamp-2 text-xs leading-[1.45] text-muted-foreground [overflow-wrap:anywhere]"
                title={typeof item.summary === 'string' ? item.summary : undefined}
              >
                {item.summary}
              </div>
            )}
          </div>
        </div>
        <div
          role="group"
          aria-labelledby={item.anchorId}
          className="flex min-w-0 max-w-full flex-wrap items-center gap-2 pl-6 @2xl:justify-end @2xl:pl-0 [&_button]:max-w-full"
        >
          {item.meta}
          {item.actions}
        </div>
      </div>
      {item.notices && <div className="mt-2 flex flex-wrap gap-2 pl-6">{item.notices}</div>}
      {expandable && (
        <div
          id={panelId}
          hidden={!open}
          className="mt-2 ml-2 space-y-2 border-l border-border/60 pl-4 @xl:ml-2.5"
        >
          {open && item.body}
        </div>
      )}
    </div>
  );
}
