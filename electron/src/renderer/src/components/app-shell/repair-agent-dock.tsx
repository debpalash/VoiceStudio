import { useStore } from '@tanstack/react-store';
import { translationActivity } from '@/features/dub/translation-activity';
import { TranslationAgentDock } from './translation-agent-dock';
import { AgentDockFrame } from './agent-dock-frame';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useRouterState } from '@tanstack/react-router';
import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  FolderOpenIcon,
  PencilLineIcon,
  PlayIcon,
  ShieldCheckIcon,
  SquareIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import codexIcon from '@lobehub/icons-static-svg/icons/codex-color.svg';
import claudeIcon from '@lobehub/icons-static-svg/icons/claudecode-color.svg';
import openCodeIcon from '@lobehub/icons-static-svg/icons/opencode.svg';
import piIcon from '@lobehub/icons-static-svg/icons/pi.svg';
import { getBridge } from '@/components/bridge';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { getFrontendLogs } from '@shared/utils/consoleBuffer';
import { useBackendStatus } from '@/hooks/use-backend-status';
import type {
  RepairAgentId,
  RepairAgentInfo,
  RepairAgentStatus,
} from '../../../../preload/index.d';
import {
  DEFAULT_REPAIR_AGENT_KEY,
  REPAIR_AGENT_OPEN_EVENT,
  takePendingRepairRequest,
  type RepairAgentRequest,
} from '@/lib/repair-agent-events';
import { apiJson } from '@/lib/api/client';
import { collectRepairLogLines, repairLogCause } from '@/lib/repair-log-analysis';
import { canRunRepairRequest, isAppOperationRequest } from '../../../../shared/repair-request';
import './repair-agent-dock.css';

const OUTPUT_LIMIT = 250_000;
const AGENT_ICONS = {
  codex: { src: codexIcon, monochrome: false },
  claude: { src: claudeIcon, monochrome: false },
  opencode: { src: openCodeIcon, monochrome: true },
  pi: { src: piIcon, monochrome: true },
};

function savedRepairAgent(): RepairAgentId | null {
  try {
    return localStorage.getItem(DEFAULT_REPAIR_AGENT_KEY) as RepairAgentId | null;
  } catch {
    return null;
  }
}

function rememberRepairAgent(agent: RepairAgentId): void {
  try {
    localStorage.setItem(DEFAULT_REPAIR_AGENT_KEY, agent);
  } catch {
    // The current session can still run it when persistent storage is unavailable.
  }
}

function RepairGlyph({ className }: { className?: string }) {
  return (
    <span className={cn('relative block size-5', className)} aria-hidden="true">
      <BotIcon className="absolute left-0 top-0 size-4" />
      <WrenchIcon className="absolute bottom-0 right-0 size-3 rounded-full bg-background p-px text-primary" />
    </span>
  );
}

export function RepairAgentDock() {
  const { t } = useTranslation();
  const translation = useStore(translationActivity);
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const backend = useBackendStatus();
  const bridge = getBridge();
  const [open, setOpen] = useState(false);
  const [agents, setAgents] = useState<RepairAgentInfo[]>([]);
  const [selected, setSelected] = useState<RepairAgentId>('codex');
  const [status, setStatus] = useState<RepairAgentStatus>('idle');
  const [workspaceAvailable, setWorkspaceAvailable] = useState(true);
  const [workspacePath, setWorkspacePath] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [output, setOutput] = useState('');
  const [report, setReport] = useState('');
  const [captured, setCaptured] = useState<string[]>([]);
  const [mode, setMode] = useState<'diagnose' | 'fix'>('diagnose');
  const [error, setError] = useState('');
  const [autoFixReport, setAutoFixReport] = useState('');
  const [chooseDefault, setChooseDefault] = useState(false);
  const [showReportEditor, setShowReportEditor] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const terminal = useRef<HTMLPreElement>(null);
  const translationRunId = translation.runs.at(-1)?.id;
  useEffect(() => {
    if (translationRunId) setOpen(false);
  }, [translationRunId]);

  useEffect(() => {
    if (!bridge) return;
    let alive = true;
    void Promise.all([bridge.repair.list(), bridge.repair.getState()])
      .then(([found, current]) => {
        if (!alive) return;
        setAgents(found);
        setWorkspaceAvailable(current.workspaceAvailable);
        setWorkspacePath(current.workspacePath ?? '');
        setStatus(current.status);
        setSessionId(current.sessionId ?? '');
        setOutput(current.output);
        if (current.mode) setMode(current.mode);
        const preferred = found.find((agent) => agent.available)?.id;
        if (preferred) {
          setSelected((current) =>
            found.find((agent) => agent.id === current)?.available ? current : preferred,
          );
        }
      })
      .catch(
        (reason) => alive && setError(reason instanceof Error ? reason.message : String(reason)),
      );
    const unsubscribe = bridge.repair.onEvent((event) => {
      if (event.type === 'output') {
        setSessionId(event.sessionId);
        setOutput((current) => (current + event.text).slice(-OUTPUT_LIMIT));
      } else {
        setSessionId(event.sessionId);
        setStatus(event.status);
      }
    });
    return () => {
      alive = false;
      unsubscribe();
    };
  }, [bridge]);

  useEffect(() => {
    const openRequest = (detail?: RepairAgentRequest | null) => {
      if (detail?.report) {
        const lines = detail.report.split(/\r?\n/).filter(Boolean);
        setCaptured(collectRepairLogLines(lines));
        setReport(detail.report);
        if (detail.autoFix) setAutoFixReport(detail.report);
        setShowReportEditor(false);
        setLogsOpen(false);
      }
      setOpen(true);
    };
    const openFromApp = (event: Event) => {
      openRequest(takePendingRepairRequest() ?? (event as CustomEvent<RepairAgentRequest>).detail);
    };
    window.addEventListener(REPAIR_AGENT_OPEN_EVENT, openFromApp);
    const pending = takePendingRepairRequest();
    if (pending) openRequest(pending);
    return () => window.removeEventListener(REPAIR_AGENT_OPEN_EVENT, openFromApp);
  }, []);

  useEffect(() => {
    if (!autoFixReport || agents.length === 0 || status === 'running') return;
    const saved = savedRepairAgent();
    const agent = agents.find((item) => item.id === saved && item.available);
    if (!agent || !canRunRepairRequest(autoFixReport, workspaceAvailable)) {
      setChooseDefault(true);
      return;
    }
    setSelected(agent.id);
    setChooseDefault(false);
    setAutoFixReport('');
    void run('fix', agent.id, autoFixReport);
  }, [agents, autoFixReport, status, workspaceAvailable]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const frontend = getFrontendLogs().map(
      (entry) => `${new Date(entry.t).toISOString()} [${entry.level}] ${entry.msg}`,
    );
    const finish = (backendLines: string[]) => {
      if (!alive) return;
      const found = collectRepairLogLines([...backendLines, ...frontend]);
      setCaptured((current) => collectRepairLogLines([...current, ...found]));
      if (found.length > 0) setReport((current) => current || found.join('\n'));
    };
    if (pathname.endsWith('/settings/logs')) {
      void apiJson<{ lines?: string[] }>('/system/logs?tail=1000')
        .then((response) => finish(response.lines ?? backend.logTail))
        .catch(() => finish(backend.logTail));
    } else {
      finish(backend.logTail);
    }
    return () => {
      alive = false;
    };
  }, [backend.logTail, open, pathname]);

  useEffect(() => {
    if (terminal.current) terminal.current.scrollTop = terminal.current.scrollHeight;
  }, [output]);

  if (!bridge) return null;
  const repair = bridge.repair;
  const running = status === 'running';
  const available = agents.some((agent) => agent.available);
  const activeReport = autoFixReport || report;
  const appOperation = isAppOperationRequest(activeReport);
  const canRun = canRunRepairRequest(activeReport, workspaceAvailable);
  const needsAttention =
    Boolean(backend.lastCrash && !backend.lastCrash.acknowledged) ||
    ['crashed', 'failed'].includes(backend.stage);
  const recognizedCause = repairLogCause(captured);
  const selectedAgent = agents.find((agent) => agent.id === selected);
  const selectedAgentIcon = AGENT_ICONS[selected];
  const statusText = t(
    status === 'running'
      ? 'common.loading'
      : status === 'failed'
        ? 'common.error'
        : status === 'stopped'
          ? 'common.stop'
          : status === 'complete'
            ? 'repairAgent.complete'
            : 'repairAgent.ready',
  );
  const causeText =
    recognizedCause === 'hfAccess'
      ? t('modelMaintenance.gatedAccessRequired')
      : recognizedCause === 'memory'
        ? t('errors.crash_oom_kill')
        : recognizedCause === 'port'
          ? t('backend.port_in_use', { port: backend.port })
          : recognizedCause === 'brokenRuntime'
            ? t('errors.crash_broken_env')
            : '';
  async function run(
    nextMode: 'diagnose' | 'fix',
    agent: RepairAgentId = selected,
    nextReport: string = report,
  ) {
    setError('');
    setOutput('');
    setMode(nextMode);
    setStatus('running');
    const context = JSON.stringify({
      route: pathname,
      frontendLogs: getFrontendLogs().slice(-120),
    });
    try {
      const started = await repair.start({
        agent,
        mode: nextMode,
        report: nextReport,
        context,
      });
      if (nextMode === 'fix') rememberRepairAgent(agent);
      setSessionId(started.sessionId);
    } catch (reason) {
      setStatus('failed');
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }
  const chooseWorkspace = async () => {
    setError('');
    try {
      const current = await repair.chooseWorkspace();
      setWorkspaceAvailable(current.workspaceAvailable);
      setWorkspacePath(current.workspacePath ?? '');
    } catch {
      setError(t('repairAgent.noSource'));
    }
  };

  if (!open && status !== 'running' && translation.runs.length) return <TranslationAgentDock />;

  if (!open) {
    return createPortal(
      <Button
        type="button"
        size="icon"
        variant="secondary"
        aria-label={t('repairAgent.title')}
        title={t('repairAgent.title')}
        onClick={() => setOpen(true)}
        className="fixed right-3 top-1/2 z-40 size-11 -translate-y-1/2 rounded-full border border-sidebar-border bg-sidebar/90 text-sidebar-foreground shadow-[0_8px_28px_rgb(0_0_0/28%)] backdrop-blur-xl hover:bg-sidebar-row-hover"
      >
        <RepairGlyph />
        {needsAttention && (
          <span className="absolute right-0.5 top-0.5 size-2.5 rounded-full border-2 border-background bg-destructive" />
        )}
      </Button>,
      document.body,
    );
  }

  return (
    <AgentDockFrame
      label={t('repairAgent.title')}
      className="repair-dock"
      resizable
      resizeStorageKey="voicestudio.repair-dock-height"
    >
      <header className="repair-dock-header">
        <div className="repair-dock-identity">
          <span className="repair-dock-glyph">
            <RepairGlyph className="text-foreground" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-xs font-semibold">{t('repairAgent.title')}</p>
            <p className="repair-dock-status" role="status" title={statusText}>
              <span
                className={cn(
                  'repair-dock-status-dot',
                  status === 'running' && 'is-running',
                  status === 'failed' && 'is-failed',
                  status === 'complete' && 'is-complete',
                )}
                aria-hidden="true"
              />
              <span className={status === 'idle' ? 'sr-only' : 'truncate'}>{statusText}</span>
            </p>
          </div>
        </div>
        <div className="repair-dock-agents" role="group" aria-label={t('repairAgent.title')}>
          {agents.map((agent) => {
            const agentIcon = AGENT_ICONS[agent.id];
            return (
              <Button
                key={agent.id}
                type="button"
                size="sm"
                variant={selected === agent.id ? 'secondary' : 'ghost'}
                disabled={running || !agent.available}
                aria-pressed={selected === agent.id}
                title={agent.available ? agent.version : t('repairAgent.notInstalled')}
                onClick={() => setSelected(agent.id)}
                className="repair-dock-agent"
              >
                <img
                  src={agentIcon.src}
                  alt=""
                  className={cn('size-4 shrink-0', agentIcon.monochrome && 'dark:invert')}
                />
                <span className="truncate">{agent.label}</span>
              </Button>
            );
          })}
        </div>
        <div className="repair-dock-header-actions">
          {running && (
            <Button type="button" size="sm" variant="ghost" onClick={() => void repair.stop()}>
              <SquareIcon />
              {t('common.stop')}
            </Button>
          )}
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            aria-label={t('common.close')}
            onClick={() => setOpen(false)}
          >
            <XIcon />
          </Button>
        </div>
      </header>
      <div className="repair-dock-body">
        {output ? (
          <pre
            ref={terminal}
            role="log"
            aria-live="polite"
            className="repair-dock-terminal studio-scrollbar"
          >
            {output}
          </pre>
        ) : (
          <div role="status" aria-live="polite" className="repair-dock-empty">
            <span className="repair-dock-empty-icon">
              <RepairGlyph className="size-6 text-muted-foreground" />
            </span>
            <p className="max-w-sm text-xs leading-5">
              {t(
                !canRun
                  ? 'repairAgent.noSource'
                  : !available
                    ? 'repairAgent.noneFound'
                    : 'repairAgent.ready',
              )}
            </p>
          </div>
        )}
        <div className="repair-dock-inspector studio-scrollbar">
          {!workspaceAvailable && !appOperation && (
            <Button type="button" variant="outline" onClick={() => void chooseWorkspace()}>
              <FolderOpenIcon />
              {t('settings.models_dir_choose')}
            </Button>
          )}
          {chooseDefault && canRun && available && (
            <div className="repair-dock-card repair-dock-default">
              <div className="repair-dock-default-copy">
                <span className="repair-dock-default-agent" aria-hidden="true">
                  <img
                    src={selectedAgentIcon.src}
                    alt=""
                    className={cn('size-4', selectedAgentIcon.monochrome && 'dark:invert')}
                  />
                </span>
                <p>{t('repairAgent.chooseDefault')}</p>
              </div>
              <div className="repair-dock-default-actions">
                <Button
                  type="button"
                  size="sm"
                  disabled={!selectedAgent?.available}
                  onClick={() => {
                    const pending = autoFixReport || report;
                    setChooseDefault(false);
                    setAutoFixReport('');
                    void run('fix', selected, pending);
                  }}
                >
                  <WrenchIcon />
                  {t('repairAgent.useAutomatically', {
                    agent: selectedAgent?.label ?? selected,
                  })}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setChooseDefault(false);
                    setAutoFixReport('');
                  }}
                >
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          )}
          {captured.length > 0 ? (
            <div
              className="repair-dock-problems"
              role="region"
              aria-label={t('repairAgent.captured', { count: captured.length })}
            >
              <div className="repair-dock-problem-heading">
                <p className="flex min-w-0 items-center gap-2 font-medium text-destructive">
                  <span className="repair-dock-problem-icon">
                    <AlertTriangleIcon className="size-3.5" />
                  </span>
                  <span className="truncate">
                    {t('repairAgent.captured', { count: captured.length })}
                  </span>
                </p>
                <Button
                  type="button"
                  size="icon-xs"
                  variant={showReportEditor ? 'secondary' : 'ghost'}
                  aria-label={t('repairAgent.placeholder')}
                  aria-pressed={showReportEditor}
                  title={t('repairAgent.placeholder')}
                  onClick={() => setShowReportEditor((current) => !current)}
                >
                  <PencilLineIcon />
                </Button>
              </div>
              {causeText && <p className="mt-1.5 leading-4 text-foreground/80">{causeText}</p>}
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="repair-dock-log-toggle"
                aria-expanded={logsOpen}
                onClick={() => setLogsOpen((current) => !current)}
              >
                <ChevronRightIcon
                  className={cn('repair-dock-log-chevron', logsOpen && 'is-open')}
                />
                <span className="truncate">{t('crash.stderr_title')}</span>
              </Button>
              {logsOpen && (
                <pre className="repair-dock-log studio-scrollbar">{captured.join('\n')}</pre>
              )}
            </div>
          ) : (
            <p className="repair-dock-card flex items-center gap-2 text-xs text-muted-foreground">
              <CheckCircle2Icon className="size-3.5" />
              {t('logs.all_clear')}
            </p>
          )}
          {(captured.length === 0 || showReportEditor) && (
            <textarea
              value={report}
              disabled={running || !canRun || !available}
              onChange={(event) => setReport(event.target.value)}
              placeholder={t('repairAgent.placeholder')}
              aria-label={t('repairAgent.placeholder')}
              className="repair-dock-report"
              autoFocus={showReportEditor}
            />
          )}
          {(error || (status === 'failed' && !output)) && (
            <p role="alert" className="text-xs text-destructive">
              {error || t('common.error')}
            </p>
          )}
          {status === 'complete' && mode === 'fix' && workspaceAvailable && (
            <p className="text-xs text-emerald-500">{t('repairAgent.prReady')}</p>
          )}
          <div className="repair-dock-footer">
            <div className="repair-dock-actions">
              <Button
                type="button"
                variant="outline"
                disabled={running || !canRun || !available}
                onClick={() => void run('diagnose')}
              >
                <PlayIcon />
                {t('repairAgent.diagnose')}
              </Button>
              <Button
                type="button"
                disabled={running || !canRun || !available}
                onClick={() => void run('fix')}
              >
                <WrenchIcon />
                {t('repairAgent.fix')}
              </Button>
            </div>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    aria-label={t('repairAgent.contextNotice')}
                  />
                }
              >
                <ShieldCheckIcon />
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-72 text-xs leading-5">
                {t('repairAgent.contextNotice')}
              </TooltipContent>
            </Tooltip>
          </div>
          {sessionId && (
            <span className="truncate font-mono text-[9px] text-muted-foreground">{sessionId}</span>
          )}
          {workspacePath && (
            <span
              className="truncate font-mono text-[9px] text-muted-foreground"
              title={workspacePath}
            >
              {workspacePath}
            </span>
          )}
        </div>
      </div>
    </AgentDockFrame>
  );
}
