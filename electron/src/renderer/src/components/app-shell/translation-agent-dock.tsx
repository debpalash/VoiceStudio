import { useEffect, useRef, useState } from 'react';
import { useStore } from '@tanstack/react-store';
import { useTranslation } from 'react-i18next';
import { BotIcon, ChevronDownIcon, ChevronUpIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cancelDub, useDubSession } from '@/features/dub/dub-session';
import { translationActivity } from '@/features/dub/translation-activity';
import { AgentDockFrame } from './agent-dock-frame';

export function TranslationAgentDock() {
  const { t } = useTranslation();
  const activity = useStore(translationActivity);
  const session = useDubSession();
  const [now, setNow] = useState(Date.now());
  const log = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const latest = activity.runs.at(-1);
  const running = activity.runs.some((run) => run.status === 'running');
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  useEffect(() => {
    if (log.current && following.current) log.current.scrollTop = log.current.scrollHeight;
  }, [activity.runs, activity.tab, activity.expanded]);
  if (!latest) return null;
  const elapsed = Math.max(0, Math.floor(((latest.endedAt || now) - latest.startedAt) / 1000));
  const completed = latest.rows.filter((row) => row.text && !row.error).length;
  const status = latest.status === 'running' ? t('dub.translating')
    : latest.status === 'complete' ? t('repairAgent.complete')
    : latest.status === 'cancelled' ? t('dubActivity.cancelled') : t('common.error');
  const busy = ['translating', 'generating', 'transcribing', 'preparing'].includes(session.phase);
  return <AgentDockFrame label={t('dub.translate_with_agent')} expanded={activity.expanded}>
    <header className="flex min-h-12 shrink-0 items-center gap-2 border-b border-sidebar-border px-3">
      <BotIcon className="size-4 shrink-0" />
      <div className="min-w-0 flex-1 text-xs" role="status">
        <p className="truncate font-medium">{status} · {latest.target} · {latest.agent}</p>
        <p className="text-muted-foreground">{t('dubActivity.progress', { done: completed, total: latest.rows.length })} · {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}</p>
      </div>
      {running && <Button size="sm" variant="ghost" onClick={() => void cancelDub()}>{t('common.cancel')}</Button>}
      <Button size="sm" variant="ghost" aria-expanded={activity.expanded} aria-controls="translation-agent-details"
        onClick={() => translationActivity.setState((s) => ({ ...s, expanded: !s.expanded }))}>
        {activity.expanded ? <ChevronDownIcon /> : <ChevronUpIcon />}{t('common.details')}
      </Button>
      {!running && <Button size="icon-sm" variant="ghost" aria-label={t('common.close')}
        onClick={() => translationActivity.setState((s) => ({ ...s, runs: [] }))}><XIcon /></Button>}
    </header>
    {activity.expanded && <div id="translation-agent-details" className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 gap-1 border-b border-sidebar-border px-3 py-1" role="tablist" aria-label={t('common.details')}>
        {(['output', 'logs'] as const).map((tab) => <Button key={tab} size="sm" variant={activity.tab === tab ? 'secondary' : 'ghost'} role="tab"
          tabIndex={activity.tab === tab ? 0 : -1}
          onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 'output' : event.key === 'End' ? 'logs' : tab === 'logs' ? 'output' : 'logs';
            translationActivity.setState((s) => ({ ...s, tab: next }));
            document.getElementById(`translation-${next}-tab`)?.focus();
          }}
          aria-selected={activity.tab === tab} aria-controls={`translation-${tab}-panel`} id={`translation-${tab}-tab`}
          onClick={() => translationActivity.setState((s) => ({ ...s, tab }))}>
          {t(tab === 'output' ? 'dubActivity.output' : 'logs.title')}
        </Button>)}
      </div>
      <div ref={log} role="tabpanel" id={`translation-${activity.tab}-panel`} aria-labelledby={`translation-${activity.tab}-tab`}
        onScroll={() => { const el = log.current; if (el) following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}
        className="studio-scrollbar min-h-0 flex-1 overflow-auto p-3">
        {activity.runs.map((run) => <div key={run.id} className="mb-4 space-y-2">
          <p className="text-xs font-medium">{run.target} · {run.agent} · {run.purpose === 'fit' ? t('dubActivity.fitting') : t('dub.translate')}</p>
          {activity.tab === 'logs' ? <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5">{run.logs || t('dubActivity.waiting')}</pre>
            : run.rows.some((row) => row.text || row.error) ? run.rows.filter((row) => row.text || row.error).map((row) =>
              <div key={row.id} className="grid gap-2 rounded-md border border-sidebar-border p-2 text-sm @xl:grid-cols-2 [content-visibility:auto]">
                <p className="whitespace-pre-wrap break-words text-muted-foreground">{row.source}</p>
                <p className="whitespace-pre-wrap break-words">{row.error || row.text}</p>
              </div>) : <p className="text-xs text-muted-foreground">{t('dubActivity.waiting')}</p>}
          {run.error && <p role="alert" className="text-xs text-destructive">{run.error}</p>}
          {run.status === 'failed' && run.retry && activity.runs.filter((r) => r.target === run.target && r.purpose === run.purpose).at(-1)?.id === run.id && <Button size="sm" variant="outline"
            disabled={busy || running || session.jobId !== run.jobId || Boolean(session.recovery)}
            onClick={() => void run.retry?.()}>{t('common.retry')} · {run.target}</Button>}
        </div>)}
      </div>
    </div>}
  </AgentDockFrame>;
}
