import { useEffect, useRef, useState } from 'react';
import { CrashDetails } from '@/components/crash-details';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { BotIcon, CopyIcon, FolderOpenIcon, RefreshCwIcon, Trash2Icon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { apiJson } from '@/lib/api/client';
import { getBridge } from '@/components/bridge';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { logSeverity, stripLogAnsi, type LogSeverity } from '@/lib/log-format';
import { openRepairAgent } from '@/lib/repair-agent-events';
import { cn } from '@/lib/utils';
import {
  getFrontendLogs,
  clearFrontendLogs,
} from '@shared/utils/consoleBuffer';
interface LogResponse {
  lines: string[];
  path?: string;
  exists?: boolean;
}
export function LogSettings() {
  const { t } = useTranslation();
  const backend = useBackendStatus();
  const [source, setSource] = useState<'backend' | 'frontend'>('backend');
  const [filter, setFilter] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [copied, setCopied] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const query = useQuery({
    queryKey: ['settings-logs', source],
    refetchInterval: 3000,
    queryFn: (): Promise<LogResponse> =>
      source === 'backend'
        ? apiJson('/system/logs?tail=1000')
        : Promise.resolve({
            lines: getFrontendLogs().map(
              (entry) => `${new Date(entry.t).toISOString()} [${entry.level}] ${entry.msg}\n`,
            ),
          }),
  });
  const lines =
    query.data?.lines ?? (source === 'backend' ? backend.logTail.map((line) => line + '\n') : []);
  const visibleLines = lines.filter((line) =>
    line.toLocaleLowerCase().includes(filter.toLocaleLowerCase()),
  );
  const text = visibleLines.map(stripLogAnsi).join('');
  const problemReport = visibleLines
    .filter((line) => ['error', 'warning'].includes(logSeverity(line)))
    .slice(-40)
    .map(stripLogAnsi)
    .join('');
  useEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [text]);
  const clear = async () => {
    setBusy(true);
    setFailed(false);
    try {
      if (source === 'frontend') clearFrontendLogs();
      else await apiJson('/system/logs/clear', { method: 'POST' });
      await query.refetch();
      setConfirmClear(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col" aria-label={t('settings.logs')}>
      <CrashDetails />
      <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-1.5 border-b border-border/50 bg-muted/15 px-4 py-2">
        {(['backend', 'frontend'] as const).map((item) => (
          <Button
            key={item}
            size="sm"
            variant={source === item ? 'secondary' : 'ghost'}
            aria-pressed={source === item}
            onClick={() => {
              setSource(item);
              setConfirmClear(false);
              setFailed(false);
              follow.current = true;
            }}
          >
            {t('common.' + item)}
          </Button>
        ))}
        <Input
          type="search"
          aria-label={t('common.search')}
          placeholder={t('common.search')}
          value={filter}
          onChange={(event) => {
            setFilter(event.target.value);
            setCopied(false);
          }}
          className="mx-1 h-7 min-w-40 flex-1 @2xl:max-w-xl"
        />
        <Button
          variant="ghost"
          size="sm"
          aria-label={t('repairAgent.title')}
          title={t('repairAgent.title')}
          disabled={!problemReport}
          onClick={() => openRepairAgent(problemReport)}
        >
          <BotIcon />
          <span className="hidden @3xl:inline">{t('repairAgent.title')}</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t('common.refresh')}
          title={t('common.refresh')}
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <RefreshCwIcon className={query.isFetching ? 'animate-spin' : undefined} />
          <span className="hidden @3xl:inline">{t('common.refresh')}</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t(copied ? 'transcriptions.copied' : 'logs.copy_visible')}
          title={t(copied ? 'transcriptions.copied' : 'logs.copy_visible')}
          disabled={!text}
          onClick={() => {
            setFailed(false);
            void navigator.clipboard
              .writeText(text)
              .then(() => setCopied(true))
              .catch(() => setFailed(true));
          }}
        >
          <CopyIcon />
          <span className="hidden @3xl:inline">
            {t(copied ? 'transcriptions.copied' : 'logs.copy_visible')}
          </span>
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('common.clear')}
          onClick={() => setConfirmClear(true)}
        >
          <Trash2Icon />
        </Button>
      </div>
      {query.data?.exists && query.data.path && getBridge() && (
        <Button
          variant="ghost"
          className="h-8 shrink-0 justify-start rounded-none border-b border-border/40 px-4 text-xs text-muted-foreground"
          onClick={() => {
            void getBridge()
              ?.files.revealPath(query.data!.path!)
              .catch(() => setFailed(true));
          }}
        >
          <FolderOpenIcon />
          {t('common.open')}
        </Button>
      )}
      {confirmClear && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-warning/20 bg-warning/5 px-4 py-2 text-sm">
          <p className="mr-auto">
            {t(
              source === 'frontend'
                ? 'settings.clear_frontend_confirm'
                : 'settings.clear_backend_confirm',
            )}
          </p>
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={() => void clear()}>
              {t('common.clear')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => setConfirmClear(false)}
            >
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      )}
      {(failed || query.isError) && (
        <p
          role="alert"
          className="shrink-0 border-b border-destructive/20 bg-destructive/5 px-4 py-2 text-sm text-destructive"
        >
          {t('common.error')}
        </p>
      )}
      <div
        ref={scroll}
        role="log"
        aria-live="off"
        aria-label={t('settings.logs')}
        tabIndex={0}
        className="studio-scrollbar min-h-0 min-w-0 flex-1 overflow-auto whitespace-pre-wrap break-words border-0 bg-muted/10 p-3 font-mono text-xs leading-5 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
        onScroll={(event) => {
          const element = event.currentTarget;
          follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 32;
        }}
      >
        {visibleLines.length > 0
          ? visibleLines.map((line, index) => {
              const severity = logSeverity(line);
              const severityStyle: Record<LogSeverity, string> = {
                error: 'border-destructive/70 bg-destructive/8 text-destructive',
                warning: 'border-warning/70 bg-warning/8 text-warning-foreground',
                success: 'border-emerald-500/50 text-emerald-500',
                debug: 'border-transparent text-muted-foreground/65',
                info: 'border-transparent text-foreground/85',
              };
              return (
                <span
                  key={`${index}-${line.slice(0, 24)}`}
                  data-severity={severity}
                  className={cn(
                    'block min-h-5 rounded-r-sm border-l-2 px-2',
                    severityStyle[severity],
                  )}
                >
                  {stripLogAnsi(line).replace(/\r?\n$/, '') || ' '}
                </span>
              );
            })
          : t(source === 'frontend' ? 'logs.empty_frontend' : 'logs.empty_backend')}
      </div>
    </section>
  );
}
