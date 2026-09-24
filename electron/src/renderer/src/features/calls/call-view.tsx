import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  CopyIcon,
  HandIcon,
  PhoneIncomingIcon,
  PhoneOffIcon,
  PhoneOutgoingIcon,
  RotateCcwIcon,
  SendIcon,
  WifiOffIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { describeError } from '@/lib/api/client';
import { callTime, hangUp, sayOnCall, setTakeover, type CallRecord } from '@/lib/api/calls';
import type { Profile } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { AgentStateIndicator, OutcomeBadge, StatusTimeline } from './call-parts';
import { formatDuration, type LiveLine } from './call-state';
import type { LiveCall } from './use-live-call';

function Transcript({ lines, live }: { lines: LiveLine[]; live: boolean }) {
  const { t } = useTranslation();
  const end = useRef<HTMLDivElement>(null);
  const last = lines[lines.length - 1];
  useEffect(() => {
    if (live) end.current?.scrollIntoView?.({ block: 'end' });
  }, [live, lines.length, last?.text]);
  if (!lines.length)
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        {t(live ? 'calls.transcript_waiting' : 'calls.transcript_empty')}
      </p>
    );
  return (
    <div
      role="log"
      aria-live={live ? 'polite' : 'off'}
      aria-label={t('calls.transcript')}
      // The pane (and its scrollbar) spans the full width; the conversation
      // itself stays a readable column on very wide screens.
      className="mx-auto flex w-full max-w-5xl flex-col gap-2.5"
    >
      {lines.map((line, index) => {
        const agent = line.speaker === 'agent';
        return (
          <div
            key={`${line.speaker}-${line.t}-${index}`}
            data-speaker={line.speaker}
            className={cn('flex flex-col gap-0.5', agent ? 'items-end' : 'items-start')}
          >
            <span className="px-1 text-[11px] text-muted-foreground">
              {t(agent ? 'calls.speaker_agent' : 'calls.speaker_caller')} · {formatDuration(line.t)}
            </span>
            <p
              className={cn(
                // Cap the bubble, not the page: long lines stay readable on 4K.
                'max-w-[min(42rem,85%)] rounded-2xl px-3.5 py-2 text-sm leading-6 whitespace-pre-wrap break-words',
                agent
                  ? 'rounded-br-md bg-primary text-primary-foreground'
                  : 'rounded-bl-md bg-muted text-foreground',
                !line.final && 'opacity-70 italic',
              )}
            >
              {line.text}
            </p>
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}

function LiveControls({ callId, live }: { callId: string; live: LiveCall }) {
  const { t } = useTranslation();
  const ids = useId();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState<'takeover' | 'say' | 'hangup' | null>(null);
  const takeover = live.state.takeover;
  const run = async (kind: 'takeover' | 'say' | 'hangup', action: () => Promise<void>) => {
    if (busy) return;
    setBusy(kind);
    try {
      await action();
    } catch (error) {
      toast.error(describeError(error));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="space-y-2">
      {takeover && (
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const next = text.trim();
            if (!next) return;
            void run('say', async () => {
              await sayOnCall(callId, next);
              setText('');
            });
          }}
        >
          <label htmlFor={`${ids}-say`} className="sr-only">
            {t('calls.say_label')}
          </label>
          <Input
            id={`${ids}-say`}
            autoFocus
            value={text}
            placeholder={t('calls.say_placeholder')}
            onChange={(event) => setText(event.target.value)}
          />
          <Button
            type="submit"
            disabled={!text.trim() || busy === 'say'}
            aria-busy={busy === 'say'}
          >
            <SendIcon aria-hidden="true" />
            {t('calls.say')}
          </Button>
        </form>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex items-center gap-2 text-sm">
          <HandIcon aria-hidden="true" className="size-4 text-muted-foreground" />
          <span aria-hidden="true">{t('calls.take_over')}</span>
          <Switch
            aria-label={t('calls.take_over')}
            checked={takeover}
            disabled={busy === 'takeover'}
            onCheckedChange={(enabled) =>
              void run('takeover', async () => {
                await setTakeover(callId, enabled);
                live.setTakeover(enabled);
              })
            }
          />
        </span>
        <Button
          variant="destructive"
          className="ml-auto bg-destructive text-white hover:bg-destructive/90 dark:bg-destructive dark:hover:bg-destructive/90"
          disabled={busy === 'hangup'}
          aria-busy={busy === 'hangup'}
          onClick={() => void run('hangup', () => hangUp(callId))}
        >
          <PhoneOffIcon aria-hidden="true" />
          {t('calls.hang_up')}
        </Button>
      </div>
    </div>
  );
}

/** Status, agent state, transcript and — while live — the take-over and hang-up controls. */
export function CallView({
  live,
  lead,
  maxMinutes,
}: {
  live: LiveCall;
  /** Rendered above the transcript, inside its scroll area (details on narrow layouts). */
  lead?: ReactNode;
  maxMinutes: number | null;
}) {
  const { t } = useTranslation();
  const { call, state } = live;
  if (!call) {
    return (
      <p className="p-6 text-sm text-muted-foreground">
        {live.error ? describeError(live.error) : t('common.loading')}
      </p>
    );
  }
  const active = !state.ended;
  const failed = state.outcome === 'failed' || /fail|busy|no.?answer/i.test(state.status);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border/50 px-4 py-3 sm:px-6">
        <div className="mx-auto max-w-5xl space-y-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <StatusTimeline status={state.status} failed={failed} />
            <AgentStateIndicator state={active ? state.agentState : null} />
            <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
              <span className="sr-only">{t('calls.elapsed')} </span>
              {formatDuration(live.elapsed)}
              {maxMinutes ? ` / ${formatDuration(maxMinutes * 60)}` : ''}
            </span>
          </div>
          {active && (live.stream === 'reconnecting' || live.stream === 'closed') && (
            <p role="status" className="flex items-center gap-2 text-xs text-warning-foreground">
              <WifiOffIcon aria-hidden="true" className="size-3.5" />
              {t(live.stream === 'closed' ? 'calls.stream_lost' : 'calls.reconnecting')}
            </p>
          )}
        </div>
      </div>
      <div className="studio-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {lead && (
          <div className="mx-auto mb-5 max-w-5xl border-b border-border/50 pb-5">{lead}</div>
        )}
        <Transcript lines={state.lines} live={active} />
      </div>
      {active && (
        <div className="shrink-0 border-t border-border/50 bg-background/80 px-4 py-3 backdrop-blur sm:px-6">
          <div className="mx-auto max-w-5xl">
            <LiveControls callId={call.id} live={live} />
          </div>
        </div>
      )}
    </div>
  );
}

/** Outcome, summary and follow-up actions for the selected call. */
export function CallDetails({
  live,
  voice,
  onCallAgain,
}: {
  live: LiveCall;
  voice: Profile | null;
  onCallAgain: (call: CallRecord) => void;
}) {
  const { t, i18n } = useTranslation();
  const { call, state } = live;
  if (!call) return null;
  const summary = state.summary ?? call.summary ?? '';
  const createdMs = callTime(call.created_at);
  const created = createdMs === null ? null : new Date(createdMs);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(summary);
      toast.success(t('transcriptions.copied'));
    } catch {
      toast.error(t('transcriptions.copy_failed'));
    }
  };
  const DirectionIcon = call.direction === 'inbound' ? PhoneIncomingIcon : PhoneOutgoingIcon;
  return (
    <section aria-label={t('calls.details')} className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <DirectionIcon aria-hidden="true" className="size-4 text-muted-foreground" />
        <h2 className="min-w-0 truncate font-mono text-base font-semibold">{call.to_masked}</h2>
        <OutcomeBadge outcome={state.outcome ?? call.outcome} />
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">{t('calls.direction')}</dt>
        <dd>{t(call.direction === 'inbound' ? 'calls.inbound' : 'calls.outbound')}</dd>
        {voice && (
          <>
            <dt className="text-muted-foreground">{t('calls.voice_label')}</dt>
            <dd className="truncate">{voice.name}</dd>
          </>
        )}
        {created && (
          <>
            <dt className="text-muted-foreground">{t('calls.when')}</dt>
            <dd>
              {new Intl.DateTimeFormat(i18n.language, {
                dateStyle: 'medium',
                timeStyle: 'short',
              }).format(created)}
            </dd>
          </>
        )}
        {state.ended && (
          <>
            <dt className="text-muted-foreground">{t('calls.duration')}</dt>
            <dd className="tabular-nums">{formatDuration(live.elapsed)}</dd>
          </>
        )}
      </dl>
      {call.brief && (
        <div>
          <h3 className="text-xs font-medium text-muted-foreground">{t('calls.brief_label')}</h3>
          <p className="mt-1 whitespace-pre-wrap break-words">{call.brief}</p>
        </div>
      )}
      {state.ended && (
        <div>
          <h3 className="text-xs font-medium text-muted-foreground">{t('calls.summary')}</h3>
          <p className="mt-1 whitespace-pre-wrap break-words">
            {summary || t('calls.summary_pending')}
          </p>
        </div>
      )}
      {state.ended && (
        <div className="flex flex-wrap gap-2">
          {call.direction === 'outbound' && (
            <Button size="sm" onClick={() => onCallAgain(call)}>
              <RotateCcwIcon aria-hidden="true" />
              {t('calls.call_again')}
            </Button>
          )}
          <Button size="sm" variant="outline" disabled={!summary} onClick={() => void copy()}>
            <CopyIcon aria-hidden="true" />
            {t('calls.copy_summary')}
          </Button>
        </div>
      )}
    </section>
  );
}
