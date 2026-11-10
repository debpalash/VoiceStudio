import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangleIcon, DownloadIcon, ExternalLinkIcon, RotateCwIcon } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { Button } from '@/components/ui/button';
import { MarkdownLite } from '@/components/markdown-lite';
import { useThrottledUpdate, useUpdateState } from '@/hooks/use-update-state';
import { runUpdateAction, type UpdateAction } from '@/lib/update-actions';
import { formatBytes, updateErrorTitleKey } from '@/lib/update-format';
import { splitRoundedMinutes } from '@shared/utils/timeFormat';
import { cn } from '@/lib/utils';
import type { UpdateState } from '../../../../preload/index.d';

/** Release notes beyond this are cut at a line; the full notes are one click away. */
export const NOTES_LIMIT = 1_200;

export function capNotes(notes: string, limit = NOTES_LIMIT): string {
  if (notes.length <= limit) return notes;
  const cut = notes.lastIndexOf('\n', limit);
  return `${notes.slice(0, cut > limit / 2 ? cut : limit).trimEnd()}\n\n…`;
}

/** The pill shows only while an update needs attention; "Later" hides its version. */
export function updatePillVisible(state: UpdateState | null): state is UpdateState {
  if (!state) return false;
  if (state.status === 'downloading' || state.status === 'error') return true;
  if (state.status === 'available' || state.status === 'downloaded')
    return !state.availableVersion || state.dismissedVersion !== state.availableVersion;
  return false;
}

function formatEta(seconds: number): string {
  const { minutes, seconds: remainder } = splitRoundedMinutes(seconds);
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

function ProgressRing({ pct }: { pct: number }) {
  const circumference = 2 * Math.PI * 6;
  return (
    <svg viewBox="0 0 16 16" className="size-3.5 -rotate-90" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" strokeWidth="2" className="stroke-muted" />
      <circle
        cx="8"
        cy="8"
        r="6"
        fill="none"
        strokeWidth="2"
        strokeLinecap="round"
        className="stroke-primary transition-[stroke-dashoffset] duration-300 motion-reduce:transition-none"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - Math.max(0, Math.min(100, pct)) / 100)}
      />
    </svg>
  );
}

/**
 * The title-bar update control, left of the account avatar: a ring while an
 * update downloads, Restart once it is verified, Retry after a failure. Its
 * popover carries the details and every action.
 */
export function UpdatePill() {
  const { t } = useTranslation();
  const live = useUpdateState();
  const state = useThrottledUpdate(live);
  const [open, setOpen] = useState(false);
  if (!updatePillVisible(state)) return null;

  const pct = Math.round(state.progress);
  const act = (action: UpdateAction) => {
    if (action !== 'retry' && action !== 'download') setOpen(false);
    void runUpdateAction(action, state, t);
  };
  const label =
    state.status === 'downloading'
      ? t('update.downloading', { pct })
      : state.status === 'downloaded'
        ? t('update.ready', { version: state.availableVersion ?? '' })
        : state.status === 'error'
          ? t(updateErrorTitleKey(state), { version: state.availableVersion ?? '' })
          : t('update.available', { version: state.availableVersion ?? '' });
  const detail =
    state.status === 'downloading' && state.totalBytes
      ? t('update.download_detail', {
          downloaded: formatBytes(state.transferredBytes || 0),
          total: formatBytes(state.totalBytes),
          speed: state.bytesPerSecond ? formatBytes(state.bytesPerSecond) : '—',
          eta: state.etaSeconds === undefined ? '—' : formatEta(state.etaSeconds),
        })
      : null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            data-slot="update-pill"
            data-status={state.status}
            className={cn(
              'app-no-drag h-7 shrink-0 gap-1 rounded-full px-2 text-xs',
              state.status === 'error'
                ? 'text-destructive hover:text-destructive'
                : 'text-primary hover:text-primary',
            )}
            aria-label={label}
            title={label}
          />
        }
      >
        {state.status === 'downloading' ? (
          <>
            <ProgressRing pct={pct} />
            <span className="tabular-nums">{pct}%</span>
          </>
        ) : state.status === 'downloaded' ? (
          <>
            <RotateCwIcon />
            <span>{t('update.pill_restart')}</span>
          </>
        ) : state.status === 'error' ? (
          <>
            <AlertTriangleIcon />
            <span>{t('update.retry')}</span>
          </>
        ) : (
          <>
            <DownloadIcon />
            <span>{t('update.pill_update')}</span>
          </>
        )}
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="end"
        className="flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2.5 p-3 text-sm"
      >
        <div className="min-w-0">
          <p className="font-medium">{label}</p>
          <p className="mt-0.5 text-xs text-muted-foreground tabular-nums">
            v{state.currentVersion}
            {state.availableVersion && ` → v${state.availableVersion}`}
          </p>
        </div>
        {state.status === 'downloading' && (
          <div className="space-y-1.5">
            <div
              role="progressbar"
              aria-label={label}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct}
              className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
            >
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none"
                style={{ width: `${pct}%` }}
              />
            </div>
            {detail && <p className="text-[11px] text-muted-foreground tabular-nums">{detail}</p>}
          </div>
        )}
        {state.status === 'downloaded' && (
          <p className="text-xs text-muted-foreground">
            {t(state.installOnQuit ? 'update.ready_hint_quit' : 'update.ready_hint_restart')}
          </p>
        )}
        {state.status === 'error' && state.error && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">{t('common.more_info')}</summary>
            <p className="mt-1 break-words">{state.error}</p>
          </details>
        )}
        {state.notes && state.status !== 'error' && (
          <div className="max-h-48 overflow-y-auto rounded-md border border-border/60 bg-muted/30 px-2.5 py-2">
            <MarkdownLite
              text={capNotes(state.notes)}
              className="text-xs leading-5 text-muted-foreground"
            />
          </div>
        )}
        <button
          type="button"
          className="inline-flex items-center gap-1 self-start text-xs text-primary hover:underline"
          onClick={() => act('manual')}
        >
          <ExternalLinkIcon className="size-3" aria-hidden="true" />
          {t(state.status === 'error' ? 'update.download_manually' : 'update.view_release')}
        </button>
        <div className="flex items-center justify-end gap-2">
          {state.status !== 'downloading' && (
            <Button size="sm" variant="ghost" onClick={() => act('later')}>
              {t('update.toast_later')}
            </Button>
          )}
          {state.status === 'downloaded' && (
            <Button size="sm" onClick={() => act('install')}>
              <RotateCwIcon />
              {t('update.pill_restart')}
            </Button>
          )}
          {state.status === 'error' && (
            <Button size="sm" onClick={() => act('retry')}>
              {t('update.retry')}
            </Button>
          )}
          {state.status === 'available' && (
            <Button size="sm" onClick={() => act('download')}>
              <DownloadIcon />
              {t('update.download')}
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
