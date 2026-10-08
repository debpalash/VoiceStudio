import { useStore } from '@tanstack/react-store';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useRouterState } from '@tanstack/react-router';
import {
  ArrowUpIcon,
  BotIcon,
  ChevronDownIcon,
  FolderOpenIcon,
  PlusIcon,
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
import { cn } from '@/lib/utils';
import { getFrontendLogs } from '@shared/utils/consoleBuffer';
import { agentFeatures, type AgentFeature } from '@shared/agent-workspace';
import { useBackendStatus } from '@/hooks/use-backend-status';
import type { RepairAgentId, RepairAgentInfo } from '../../../../preload/index.d';
import {
  DEFAULT_REPAIR_AGENT_KEY,
  REPAIR_AGENT_OPEN_EVENT,
  takePendingRepairRequest,
  type RepairAgentRequest,
} from '@/lib/repair-agent-events';
import {
  agentConversation,
  beginAgentTurn,
  conversationHistory,
  receiveAgentEvent,
  restoreAgentTurn,
  updateAgentTurn,
} from '@/lib/agent-conversation';
import { translationActivity } from '@/features/dub/translation-activity';
import { TranslationAgentDock } from './translation-agent-dock';
import { AgentDockFrame } from './agent-dock-frame';
import './voice-studio-agent.css';

const icons = { codex: codexIcon, claude: claudeIcon, opencode: openCodeIcon, pi: piIcon };
const labels: Record<AgentFeature, string> = {
  repair: 'agentWorkspace.repair',
  setup: 'agentWorkspace.setup',
  clone: 'nav.clone',
  design: 'designWorkspace.title',
  dub: 'dubWorkspace.title',
  transcribe: 'nav.transcribe',
  stories: 'nav.stories',
  audiobook: 'audiobook.title',
  workflows: 'workflows.title',
  tools: 'tools.title',
};
function savedAgent(): RepairAgentId {
  try {
    const id = localStorage.getItem(DEFAULT_REPAIR_AGENT_KEY);
    if (id && id in icons) return id as RepairAgentId;
  } catch {
    /* Session-only preference. */
  }
  return 'codex';
}

export function VoiceStudioAgent() {
  const { t } = useTranslation();
  const bridge = getBridge();
  const backend = useBackendStatus();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const conversation = useStore(agentConversation);
  const translation = useStore(translationActivity);
  const [open, setOpen] = useState(false);
  const [agents, setAgents] = useState<RepairAgentInfo[]>([]);
  const [selected, setSelected] = useState<RepairAgentId>(savedAgent);
  const [mode, setMode] = useState<'diagnose' | 'fix'>('fix');
  const [features, setFeatures] = useState<AgentFeature[]>([]);
  const [source, setSource] = useState(false);
  const [workspace, setWorkspace] = useState({ available: false, path: '' });
  const [details, setDetails] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<RepairAgentRequest | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const submitting = useRef(false);
  const active = conversation.messages.find((message) => message.id === conversation.activeId);
  const running = active?.status === 'running';
  const selectedAgent = agents.find((agent) => agent.id === selected);
  const canSend = Boolean(
    selectedAgent?.available &&
    conversation.draft.trim() &&
    !running &&
    (!source || workspace.available),
  );
  const setDraft = (draft: string) => agentConversation.setState((state) => ({ ...state, draft }));
  // The mount load and the panel-open refresh both scan, and their answers
  // can arrive out of order: an older scan never replaces a newer one.
  const scans = useRef({ issued: 0, applied: 0 });
  const showAgents = (scan: number, found: RepairAgentInfo[]) => {
    if (scan < scans.current.applied) return;
    scans.current.applied = scan;
    setAgents(found);
    setSelected((current) =>
      found.some((agent) => agent.id === current && agent.available)
        ? current
        : (found.find((agent) => agent.available)?.id ?? current),
    );
  };

  useEffect(() => {
    if (!bridge) return;
    let alive = true;
    const unsubscribe = bridge.repair.onEvent(receiveAgentEvent);
    const scan = ++scans.current.issued;
    void Promise.all([bridge.repair.list(), bridge.repair.getState()])
      .then(([found, state]) => {
        if (!alive) return;
        showAgents(scan, found);
        setWorkspace({ available: state.workspaceAvailable, path: state.workspacePath ?? '' });
        restoreAgentTurn(state);
      })
      .catch((reason) => {
        if (alive) setError(String(reason));
      });
    return () => {
      alive = false;
      unsubscribe();
    };
  }, [bridge]);

  // A CLI installed while the app was running must appear without a restart.
  useEffect(() => {
    if (!bridge || !open) return;
    let alive = true;
    const scan = ++scans.current.issued;
    void bridge.repair
      .list({ refresh: true })
      .then((found) => {
        if (alive) showAgents(scan, found);
      })
      .catch(() => {
        // Keep the previous list; the initial load reports failures.
      });
    return () => {
      alive = false;
    };
  }, [bridge, open]);

  useEffect(() => {
    const receive = (request?: RepairAgentRequest | null) => {
      setOpen(true);
      if (!request?.report) return;
      setFeatures(['repair']);
      const needsSource = !request.report.trimStart().startsWith('ACTION_REQUEST:');
      setSource(needsSource);
      if (needsSource) setDetails(true);
      setDraft(request.report);
      if (request.autoFix) setPending(request);
    };
    const listener = (event: Event) =>
      receive(takePendingRepairRequest() ?? (event as CustomEvent<RepairAgentRequest>).detail);
    window.addEventListener(REPAIR_AGENT_OPEN_EVENT, listener);
    const initial = takePendingRepairRequest();
    if (initial) receive(initial);
    return () => window.removeEventListener(REPAIR_AGENT_OPEN_EVENT, listener);
  }, []);

  useEffect(() => {
    if (!pending || !selectedAgent?.available || running || submitting.current) return;
    setPending(null);
    // Crash recovery reuses only a harness the user previously chose for a run.
    // Otherwise the prefilled request waits for Send.
    try {
      if (localStorage.getItem(DEFAULT_REPAIR_AGENT_KEY) !== selected) return;
    } catch {
      return;
    }
    void send(pending.report!, 'fix', !pending.report!.trimStart().startsWith('ACTION_REQUEST:'));
  }, [pending, selectedAgent, running]);

  useEffect(() => {
    if (open && follow.current && log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [conversation.messages, open]);

  useEffect(() => {
    if (details && log.current) {
      follow.current = false;
      log.current.scrollTop = 0;
    }
  }, [details]);

  async function send(text = conversation.draft, nextMode = mode, useSource = source) {
    if (!bridge || !selectedAgent?.available || submitting.current || running || !text.trim())
      return;
    if (useSource && !workspace.available) {
      setDetails(true);
      return;
    }
    submitting.current = true;
    setError('');
    const history = conversationHistory();
    const turn = beginAgentTurn(text.trim());
    follow.current = true;
    try {
      const result = await bridge.repair.start({
        agent: selected,
        mode: nextMode,
        workspace: useSource ? 'source' : 'app',
        features,
        report: text.trim(),
        history,
        context: JSON.stringify({ route: pathname, frontendLogs: getFrontendLogs().slice(-60) }),
      });
      updateAgentTurn(turn, { sessionId: result.sessionId });
      try {
        localStorage.setItem(DEFAULT_REPAIR_AGENT_KEY, selected);
      } catch {
        /* No persistence required. */
      }
    } catch (reason) {
      updateAgentTurn(turn, { status: 'failed' });
      setError(reason instanceof Error ? reason.message : String(reason));
      setDraft(text);
    } finally {
      submitting.current = false;
    }
  }
  async function chooseWorkspace() {
    if (!bridge) return;
    try {
      const state = await bridge.repair.chooseWorkspace();
      setWorkspace({ available: state.workspaceAvailable, path: state.workspacePath ?? '' });
    } catch {
      setError(t('repairAgent.noSource'));
    }
  }
  async function stop() {
    try {
      await bridge?.repair.stop();
    } catch (reason) {
      setError(String(reason));
    }
  }
  if (!bridge) return null;
  if (!open && !running && translation.runs.length) return <TranslationAgentDock />;
  if (!open)
    return createPortal(
      <Button
        size="icon"
        variant="secondary"
        aria-label={t('repairAgent.title')}
        title={t('repairAgent.title')}
        onClick={() => setOpen(true)}
        className="fixed right-3 top-1/2 z-40 size-11 -translate-y-1/2 rounded-full border border-sidebar-border bg-sidebar text-sidebar-foreground shadow-lg"
      >
        <BotIcon />
        {running && <span className="absolute right-1 top-1 size-2 rounded-full bg-primary" />}
      </Button>,
      document.body,
    );

  return (
    <AgentDockFrame
      label={t('repairAgent.title')}
      className="agent-workspace"
      resizable
      resizeStorageKey="voicestudio.agent-workspace-height"
    >
      <header className="agent-workspace-header">
        <BotIcon className="size-5 text-primary" />
        <strong>{t('repairAgent.title')}</strong>
        <span className="agent-workspace-state" role="status">
          {running ? t('common.loading') : t('repairAgent.ready')}
        </span>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t('agentWorkspace.new_chat')}
          title={t('agentWorkspace.new_chat')}
          disabled={running}
          onClick={() => {
            agentConversation.setState(() => ({ messages: [], draft: '', activeId: '' }));
            setError('');
          }}
        >
          <PlusIcon />
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t('common.close')}
          onClick={() => setOpen(false)}
        >
          <XIcon />
        </Button>
      </header>
      <div className="agent-workspace-options">
        <div
          className="agent-workspace-harnesses"
          role="group"
          aria-label={t('agentWorkspace.harness')}
        >
          {agents.map((agent) => (
            <Button
              key={agent.id}
              size="sm"
              variant={selected === agent.id ? 'secondary' : 'ghost'}
              disabled={running || !agent.available}
              aria-pressed={selected === agent.id}
              title={agent.available ? agent.version : t('repairAgent.notInstalled')}
              onClick={() => setSelected(agent.id)}
            >
              <img
                src={icons[agent.id]}
                alt=""
                className={cn('size-4', ['pi', 'opencode'].includes(agent.id) && 'dark:invert')}
              />
              {agent.label}
            </Button>
          ))}
        </div>
        <div className="agent-workspace-modes" role="group" aria-label={t('agentWorkspace.mode')}>
          <Button
            size="sm"
            variant={mode === 'diagnose' ? 'secondary' : 'ghost'}
            aria-pressed={mode === 'diagnose'}
            disabled={running}
            onClick={() => setMode('diagnose')}
          >
            {t('agentWorkspace.plan')}
          </Button>
          <Button
            size="sm"
            variant={mode === 'fix' ? 'secondary' : 'ghost'}
            aria-pressed={mode === 'fix'}
            disabled={running}
            onClick={() => setMode('fix')}
          >
            {t('agentWorkspace.autopilot')}
          </Button>
        </div>
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={details}
          onClick={() => setDetails((value) => !value)}
        >
          {t('agentWorkspace.features')}
          <ChevronDownIcon />
        </Button>
      </div>
      <div
        ref={log}
        className="agent-workspace-chat studio-scrollbar"
        role="log"
        aria-label={t('agentWorkspace.conversation')}
        aria-live="polite"
        onScroll={() => {
          const element = log.current;
          if (element)
            follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 60;
        }}
      >
        {details && (
          <div className="agent-workspace-details studio-scrollbar">
            <div
              className="agent-workspace-feature-list"
              role="group"
              aria-label={t('agentWorkspace.features')}
            >
              {agentFeatures.map((feature) => (
                <Button
                  key={feature}
                  size="xs"
                  variant={features.includes(feature) ? 'secondary' : 'outline'}
                  aria-pressed={features.includes(feature)}
                  disabled={running}
                  onClick={() =>
                    setFeatures((current) =>
                      current.includes(feature)
                        ? current.filter((item) => item !== feature)
                        : [...current, feature],
                    )
                  }
                >
                  {t(labels[feature])}
                </Button>
              ))}
            </div>
            <label className="agent-workspace-source">
              <input
                type="checkbox"
                checked={source}
                disabled={running}
                onChange={(event) => setSource(event.target.checked)}
              />
              <WrenchIcon className="size-3.5" />
              {t('agentWorkspace.source')}
            </label>
            {source && (
              <div className="flex items-center gap-2 text-xs">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={running}
                  onClick={() => void chooseWorkspace()}
                >
                  <FolderOpenIcon />
                  {t('settings.models_dir_choose')}
                </Button>
                <span className="truncate" title={workspace.path}>
                  {workspace.path || t('repairAgent.noSource')}
                </span>
              </div>
            )}
            <details>
              <summary>{t('repairAgent.contextNotice')}</summary>
              <pre className="agent-workspace-diagnostics">
                {backend.logTail.slice(-40).join('\n')}
              </pre>
            </details>
          </div>
        )}
        {conversation.messages.length === 0 ? (
          <div className="agent-workspace-welcome">
            <p>{t('agentWorkspace.welcome')}</p>
            <div className="agent-workspace-presets">
              {(['setup', 'repair', 'audiobook', 'clone'] as const).map((feature) => (
                <button
                  key={feature}
                  disabled={running}
                  onClick={() => {
                    setFeatures([feature]);
                    setSource(false);
                    setDraft(t('agentWorkspace.starter', { feature: t(labels[feature]) }));
                  }}
                >
                  <span>{t(labels[feature])}</span>
                  <ArrowUpIcon className="size-3.5 rotate-45" />
                </button>
              ))}
            </div>
            {!agents.some((agent) => agent.available) && (
              <p className="text-muted-foreground">{t('agentWorkspace.no_harness')}</p>
            )}
          </div>
        ) : (
          conversation.messages.map((message) => (
            <article
              key={message.id}
              className={cn('agent-workspace-message', message.role === 'user' && 'is-user')}
            >
              <span className="agent-workspace-speaker">
                {t(message.role === 'user' ? 'agentWorkspace.you' : 'repairAgent.title')}
              </span>
              <div className="agent-workspace-message-text">
                {message.content ||
                  t(
                    message.status === 'running'
                      ? 'common.loading'
                      : message.status === 'failed'
                        ? 'common.error'
                        : message.status === 'stopped'
                          ? 'common.stop'
                          : 'repairAgent.complete',
                  )}
              </div>
              {message.status && message.status !== 'running' && (
                <span className="agent-workspace-result">
                  {t(
                    message.status === 'complete'
                      ? 'repairAgent.complete'
                      : message.status === 'stopped'
                        ? 'common.stop'
                        : 'common.error',
                  )}
                </span>
              )}
            </article>
          ))
        )}
      </div>
      <form
        className="agent-workspace-composer"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSend) void send();
        }}
      >
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
        <div className="agent-workspace-input">
          <textarea
            aria-label={t('agentWorkspace.placeholder')}
            placeholder={t('agentWorkspace.placeholder')}
            value={conversation.draft}
            maxLength={12000}
            disabled={running}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                if (canSend) void send();
              }
            }}
          />
          {running ? (
            <Button
              type="button"
              size="icon"
              variant="secondary"
              aria-label={t('common.stop')}
              onClick={() => void stop()}
            >
              <SquareIcon />
            </Button>
          ) : (
            <Button
              type="submit"
              size="icon"
              disabled={!canSend}
              aria-label={t('agentWorkspace.send')}
            >
              <ArrowUpIcon />
            </Button>
          )}
        </div>
        <p className="agent-workspace-hint">
          {t(mode === 'fix' ? 'agentWorkspace.autopilot_hint' : 'agentWorkspace.plan_hint')}
        </p>
      </form>
    </AgentDockFrame>
  );
}
