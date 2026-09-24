import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import {
  CheckIcon,
  CircleAlertIcon,
  EarIcon,
  PhoneOffIcon,
  RefreshCwIcon,
  BrainIcon,
  AudioLinesIcon,
  WrenchIcon,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { ExternalLink } from '@/components/external-link';
import type { AgentState, CallOutcome, ReadinessId, ReadinessItem } from '@/lib/api/calls';
import { cn } from '@/lib/utils';
import { CALL_PHASES, callPhase } from './call-state';

export const CALLS_DOCS = 'https://github.com/debpalash/VoiceStudio/blob/main/docs/calls.md';

const OUTCOME_KEYS: Record<CallOutcome, string> = {
  booked: 'calls.outcome_booked',
  done: 'calls.outcome_done',
  not_done: 'calls.outcome_not_done',
  needs_you: 'calls.outcome_needs_you',
  failed: 'calls.outcome_failed',
};
const OUTCOME_STYLES: Record<CallOutcome, string> = {
  booked: 'bg-success/15 text-success-foreground',
  done: 'bg-success/15 text-success-foreground',
  not_done: 'bg-muted text-muted-foreground',
  needs_you: 'bg-warning/15 text-warning-foreground',
  failed: 'bg-destructive/10 text-destructive',
};

export function OutcomeBadge({ outcome }: { outcome: CallOutcome | null }) {
  const { t } = useTranslation();
  if (!outcome || !(outcome in OUTCOME_KEYS)) return null;
  return (
    <Badge className={OUTCOME_STYLES[outcome]} data-outcome={outcome}>
      {t(OUTCOME_KEYS[outcome])}
    </Badge>
  );
}

const PHASE_KEYS = {
  dialing: 'calls.phase_dialing',
  ringing: 'calls.phase_ringing',
  in_call: 'calls.phase_in_call',
  ended: 'calls.phase_ended',
} as const;

/** Dialing → Ringing → In call → Ended, with the current step announced. */
export function StatusTimeline({ status, failed }: { status: string; failed?: boolean }) {
  const { t } = useTranslation();
  const current = CALL_PHASES.indexOf(callPhase(status));
  return (
    <ol
      aria-label={t('calls.status_timeline')}
      className="flex min-w-0 items-center gap-1.5 text-xs"
    >
      {CALL_PHASES.map((phase, index) => {
        const done = index < current || (index === current && phase === 'ended');
        const active = index === current;
        return (
          <li
            key={phase}
            aria-current={active ? 'step' : undefined}
            className="flex min-w-0 items-center gap-1.5"
          >
            {index > 0 && (
              <span
                aria-hidden="true"
                className={cn(
                  'h-px w-3 shrink-0 sm:w-6',
                  index <= current ? 'bg-primary' : 'bg-border',
                )}
              />
            )}
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 whitespace-nowrap',
                active && !done && 'border-primary/50 bg-primary/10 text-foreground',
                done &&
                  !(failed && phase === 'ended') &&
                  'border-transparent bg-muted text-foreground',
                failed && phase === 'ended' && 'border-destructive/40 text-destructive',
                index > current && 'border-border/60 text-muted-foreground',
              )}
            >
              {active && !done && (
                <span
                  aria-hidden="true"
                  className="size-1.5 animate-pulse rounded-full bg-primary motion-reduce:animate-none"
                />
              )}
              {done && <CheckIcon aria-hidden="true" className="size-3" />}
              {t(PHASE_KEYS[phase])}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

const AGENT_STATE = {
  listening: { key: 'calls.agent_listening', icon: EarIcon },
  thinking: { key: 'calls.agent_thinking', icon: BrainIcon },
  speaking: { key: 'calls.agent_speaking', icon: AudioLinesIcon },
} as const;

export function AgentStateIndicator({ state }: { state: AgentState | null }) {
  const { t } = useTranslation();
  if (!state) return null;
  const { key, icon: Icon } = AGENT_STATE[state];
  return (
    <span
      role="status"
      aria-live="polite"
      data-agent-state={state}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
        state === 'speaking' && 'border-primary/40 bg-primary/10 text-foreground',
        state === 'thinking' && 'border-warning/40 bg-warning/10 text-warning-foreground',
        state === 'listening' && 'border-border bg-muted/50 text-muted-foreground',
      )}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {t(key)}
    </span>
  );
}

const READINESS_LABELS: Record<ReadinessId, string> = {
  credentials: 'calls.ready_credentials',
  tunnel: 'calls.ready_tunnel',
  number: 'calls.ready_number',
  llm: 'calls.ready_llm',
  asr: 'calls.ready_asr',
  voice: 'calls.ready_voice',
};

type FixTarget =
  | { to: '/integrations/$slug'; params: { slug: string } }
  | { to: '/settings/models/$family'; params: { family: string } }
  | { to: '/personas' };

/** Where each failing check gets fixed. Every item has a destination: no dead ends. */
export function readinessFix(id: ReadinessId): FixTarget {
  switch (id) {
    case 'llm':
      return { to: '/settings/models/$family', params: { family: 'llm' } };
    case 'asr':
      return { to: '/settings/models/$family', params: { family: 'asr' } };
    case 'voice':
      return { to: '/personas' };
    default:
      return { to: '/integrations/$slug', params: { slug: 'twilio' } };
  }
}

export function ReadinessBanner({
  items,
  onRecheck,
  checking,
}: {
  items: ReadinessItem[];
  onRecheck: () => void;
  checking: boolean;
}) {
  const { t } = useTranslation();
  const failing = items.filter((item) => !item.ok);
  if (!failing.length) return null;
  return (
    <section
      aria-labelledby="calls-readiness-title"
      className="rounded-xl border border-warning/40 bg-warning-surface p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <CircleAlertIcon aria-hidden="true" className="size-4 text-warning-foreground" />
        <h2 id="calls-readiness-title" className="text-sm font-medium">
          {t('calls.readiness_title')}
        </h2>
        <span className="text-xs text-muted-foreground">
          {t('calls.readiness_count', {
            ready: items.length - failing.length,
            total: items.length,
          })}
        </span>
        <Button
          size="xs"
          variant="ghost"
          className="ml-auto"
          disabled={checking}
          aria-busy={checking}
          onClick={onRecheck}
        >
          <RefreshCwIcon aria-hidden="true" className={cn(checking && 'animate-spin')} />
          {t('calls.recheck')}
        </Button>
      </div>
      <ul className="mt-2 grid gap-1.5 [grid-template-columns:repeat(auto-fill,minmax(min(100%,18rem),1fr))]">
        {failing.map((item) => (
          <li
            key={item.id}
            className="flex min-w-0 items-start gap-2 rounded-lg bg-background/60 px-2.5 py-2 text-sm"
          >
            <span className="min-w-0 flex-1">
              <span className="block font-medium">{t(READINESS_LABELS[item.id] ?? item.id)}</span>
              {item.detail && (
                <span className="block text-xs break-words text-muted-foreground">
                  {item.detail}
                </span>
              )}
            </span>
            <Link
              {...readinessFix(item.id)}
              className={buttonVariants({ size: 'xs', variant: 'outline' })}
              aria-label={t('calls.fix_item', { item: t(READINESS_LABELS[item.id] ?? item.id) })}
            >
              <WrenchIcon aria-hidden="true" />
              {t('calls.fix')}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The backend predates the Calls API: explain and point at the fix. */
export function CallsUnavailable({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <section
      aria-labelledby="calls-unavailable-title"
      className="mx-auto my-10 w-full max-w-xl rounded-2xl border border-border/60 bg-card p-6 text-sm shadow-xs"
    >
      <PhoneOffIcon aria-hidden="true" className="size-6 text-muted-foreground" />
      <h2 id="calls-unavailable-title" className="mt-3 text-base font-semibold">
        {t('calls.unavailable_title')}
      </h2>
      <p className="mt-2 leading-6 text-muted-foreground">{t('calls.unavailable_body')}</p>
      <ol className="mt-3 list-decimal space-y-1 pl-5 text-muted-foreground">
        <li>{t('calls.unavailable_step_update')}</li>
        <li>{t('calls.unavailable_step_twilio')}</li>
      </ol>
      <div className="mt-5 flex flex-wrap gap-2">
        <Link
          to="/integrations/$slug"
          params={{ slug: 'twilio' }}
          className={buttonVariants({ size: 'sm' })}
        >
          {t('calls.setup_twilio')}
        </Link>
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCwIcon aria-hidden="true" />
          {t('calls.check_again')}
        </Button>
        <ExternalLink href={CALLS_DOCS}>{t('calls.read_guide')}</ExternalLink>
      </div>
    </section>
  );
}
