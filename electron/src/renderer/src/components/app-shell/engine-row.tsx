import { useEffect, useId, useState } from 'react';
import { Link } from '@tanstack/react-router';
import {
  CheckIcon,
  ChevronRightIcon,
  CopyIcon,
  LoaderCircleIcon,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ComputeVendorIcon, formatComputeRuntime } from '@/components/compute-vendor-icon';
import { cn } from '@/lib/utils';
import { sidebarToolState } from './status-runtime';
import type { EngineDetailLevel } from './use-engine-detail-level';

export function compactEngineName(name: string) {
  return name.replace(/\s+\([^)]*\)\s*$/, '').trim() || name;
}

function CopyModelId({ value }: { value: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <button
      type="button"
      aria-label={t(copied ? 'sidebarTools.copied' : 'sidebarTools.copyModel')}
      title={t(copied ? 'sidebarTools.copied' : 'sidebarTools.copyModel')}
      className="grid size-5 shrink-0 place-items-center rounded text-muted-foreground outline-none hover:bg-sidebar-accent/70 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      onClick={() =>
        void navigator.clipboard
          ?.writeText(value)
          .then(() => setCopied(true))
          .catch(() => {})
      }
    >
      {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
    </button>
  );
}

export function EngineRow({
  row,
  level,
  online,
  dotClass,
  open,
  onToggle,
}: {
  row: {
    family: string;
    Icon: LucideIcon;
    detail: string;
    title?: string | null;
    runtime?: string | null;
    problem?: string | null;
    state: string;
  };
  level: EngineDetailLevel;
  online: boolean;
  dotClass: string;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { family, Icon, detail, title, runtime, problem, state } = row;
  const name = t('sidebarTools.' + family);
  const status = t(sidebarToolState(state, online));
  const model = title || detail;
  const loading =
    online &&
    ['engineRuntime.loading', 'preferences.loading', 'network.switching', 'common.saving'].includes(
      state,
    );
  // Normal states read from the dot alone; words appear only when something needs attention.
  const quiet =
    online && (state === 'engineRuntime.idle' || state === 'engineRuntime.ready') && level !== 'simple';
  const modelLabel = detail.includes('/')
    ? detail.slice(detail.lastIndexOf('/') + 1)
    : compactEngineName(model);
  const stateLabel = (
    <span
      role="status"
      title={status}
      className="inline-flex shrink-0 items-center gap-1.5 text-[11px] font-normal text-muted-foreground"
    >
      {loading ? (
        <LoaderCircleIcon
          aria-hidden="true"
          className="size-3 animate-spin motion-reduce:animate-none"
        />
      ) : (
        <span
          aria-hidden="true"
          className={cn('size-1.5 rounded-full', online ? dotClass : 'bg-muted-foreground')}
        />
      )}
      <span className={quiet ? 'sr-only' : undefined}>{status}</span>
    </span>
  );
  const content = (
    <>
      <span
        aria-hidden="true"
        className="grid size-7 shrink-0 place-items-center rounded-md bg-sidebar-accent/45 text-muted-foreground ring-1 ring-inset ring-sidebar-border/40 transition-colors group-hover/engine-row:text-foreground"
      >
        <Icon className="size-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className="truncate text-xs font-medium text-foreground" title={name}>
            {name}
          </span>
          {level === 'simple' ? (
            stateLabel
          ) : (
            <ChevronRightIcon
              aria-hidden="true"
              className={cn(
                'size-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover/engine-row:opacity-100 group-focus-visible/engine-row:opacity-100',
                open && level === 'details' && 'opacity-100',
                open && level === 'details' ? 'rotate-90' : 'rtl:rotate-180',
              )}
            />
          )}
        </span>
        {level !== 'simple' && (
          <span className="mt-0.5 flex items-center justify-between gap-2">
            <span
              data-slot="engine-selected-model"
              className="min-w-0 truncate text-[11px] text-muted-foreground"
              title={model}
            >
              {modelLabel}
            </span>
            {stateLabel}
          </span>
        )}
      </span>
    </>
  );
  const rowClass =
    'group/engine-row flex min-h-11 w-full items-center gap-2.5 rounded-lg px-1.5 py-1 text-start outline-none transition-colors hover:bg-sidebar-accent/60 focus-visible:ring-2 focus-visible:ring-ring';
  return (
    <div data-slot="engine-row" aria-busy={loading} className="min-w-0">
      {level === 'details' ? (
        <button
          type="button"
          className={rowClass}
          aria-expanded={open}
          aria-controls={id}
          aria-label={`${name}: ${status}`}
          onClick={onToggle}
        >
          {content}
        </button>
      ) : (
        <Link
          to="/settings/models/$family"
          params={{ family }}
          className={rowClass}
          aria-label={`${name}: ${status}. ${t('modelSettings.change')}`}
        >
          {content}
        </Link>
      )}
      {level === 'details' && open && (
        <div
          id={id}
          data-slot="engine-diagnostics"
          className="mx-1 mt-0.5 mb-1.5 rounded-lg bg-sidebar-accent/25 p-2.5 ring-1 ring-inset ring-sidebar-border/40"
        >
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5 text-[11px]">
            {title && title !== detail && (
              <>
                <dt className="text-muted-foreground">{t('sidebarTools.engineLabel')}</dt>
                <dd className="truncate text-foreground" title={title}>
                  {compactEngineName(title)}
                </dd>
              </>
            )}
            <dt className="text-muted-foreground">{t('sidebarTools.modelLabel')}</dt>
            <dd className="flex min-w-0 items-center gap-1">
              <code className="min-w-0 truncate font-mono text-[10.5px] text-foreground/90" title={detail}>
                {detail}
              </code>
              <CopyModelId value={detail} />
            </dd>
            {runtime && (
              <>
                <dt className="text-muted-foreground">{t('sidebarTools.runsOn')}</dt>
                <dd className="flex min-w-0 items-center gap-1.5 text-foreground">
                  <ComputeVendorIcon runtime={runtime} className="size-3 shrink-0" />
                  <span className="truncate">{formatComputeRuntime(runtime)}</span>
                </dd>
              </>
            )}
          </dl>
          {problem && (
            <p className="mt-2 rounded-md bg-warning/10 px-2 py-1.5 text-[11px] leading-snug text-foreground/85 [overflow-wrap:anywhere]">
              {problem}
            </p>
          )}
          <Link
            to="/settings/models/$family"
            params={{ family }}
            className="mt-2.5 flex h-7 w-full items-center justify-center gap-1 rounded-md text-[11px] font-medium text-foreground ring-1 ring-inset ring-sidebar-border/60 outline-none transition-colors hover:bg-sidebar-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t('modelSettings.change')}
            <ChevronRightIcon className="size-3 rtl:rotate-180" aria-hidden="true" />
          </Link>
        </div>
      )}
    </div>
  );
}
