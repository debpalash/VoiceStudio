import { compareNarration } from './dub-narration';
import { ArrowRightIcon, CheckIcon, LoaderCircleIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { segmentGenInputs } from '@shared/utils/segments';
import type { DubSession } from './dub-session';
import { getDubTranslationError } from './transcript-issues';
import { dubGenerationKey, dubExportRevision } from './dub-generation-request';

export type DubGuideStep = 'source' | 'voice' | 'text' | 'audio' | 'export';
const STEPS: DubGuideStep[] = ['source', 'text', 'voice', 'audio', 'export'];

/**
 * Projects generated before guided completion records have no request key.
 * Their saved per-segment audio fingerprints are the evidence instead: every
 * spoken line must have one, and the backend's incremental check (when the
 * page has it) must not report any line as changed since that render.
 */
function legacyAudioCurrent(
  session: DubSession,
  code: string,
  spoken: DubSession['segments'],
  staleIds?: readonly string[] | null,
) {
  const stored = session.fingerprintsByLang?.[code];
  return Boolean(
    !session.narration?.enabled &&
    stored &&
    spoken.length &&
    spoken.every((segment) => Boolean(stored[segment.id])) &&
    !staleIds?.length,
  );
}

export function dubGuideState(
  session: DubSession,
  code: string | undefined,
  staleIds?: readonly string[] | null,
) {
  const spoken = session.segments.filter((segment) => segment.text.trim());
  const source = Boolean(session.jobId && spoken.length);
  const voice =
    source &&
    Boolean(code) &&
    spoken.every((segment) => {
      const inputs = segmentGenInputs(segment);
      return Boolean(inputs.profile_id || inputs.instruct?.trim());
    });
  const sameLanguage = Boolean(
    code && [session.sourceLanguage, session.sourceLang, session.subtitleTargetLang].includes(code),
  );
  const text =
    source &&
    Boolean(code) &&
    (sameLanguage ||
      spoken.every(
        (segment) =>
          Boolean(segment.translations?.[code!]?.trim()) && !getDubTranslationError(segment, code),
      ));
  const record = code ? session.generatedRequestKeys?.[code] : undefined;
  const audio = Boolean(
    code &&
    session.tracks.includes(code) &&
    (record === undefined
      ? legacyAudioCurrent(session, code, spoken, staleIds)
      : record === dubGenerationKey(session, session.target, code)),
  );
  const exported = Boolean(
    audio && code && session.exportedRequestKeys?.[code] === dubExportRevision(session, code),
  );
  const script =
    (!session.narrationWanted || Boolean(session.narration?.enabled)) &&
    (!session.narration?.enabled ||
      compareNarration(session.narration.captions, session.segments).every((row) => row.matches));
  const completed = [source, text, voice && script, audio, exported];
  const allComplete = completed.every(Boolean);
  let next = allComplete ? 4 : completed.findIndex((value) => !value);
  if (session.phase === 'generating') next = 3;
  else if (session.phase === 'translating') next = 1;
  else if (['preparing', 'transcribing', 'importing'].includes(session.phase)) next = 0;
  return {
    completed,
    textReady: text,
    voiceReady: voice,
    scriptReady: script,
    audioReady: audio,
    allComplete,
    next,
    nextStep: STEPS[next],
    running: !['idle', 'editing', 'done'].includes(session.phase),
  };
}

export function DubProjectGuide({
  session,
  code,
  onSelect,
  disabled,
  audioBlocked,
  staleIds,
}: {
  staleIds?: readonly string[] | null;
  session: DubSession;
  code: string | undefined;
  onSelect: (step: DubGuideStep) => void;
  disabled: boolean;
  audioBlocked?: string | null;
}) {
  const { t } = useTranslation();
  const state = dubGuideState(session, code, staleIds);
  return (
    <nav
      aria-label={t('dub.guide_title')}
      className="shrink-0 border-b border-border/50 bg-muted/10 px-4 py-3"
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1 text-xs">
        <span className="font-medium">
          {state.allComplete ? (
            t('dub.guide_all_done')
          ) : (
            <>
              {t('dub.guide_next')} {t('dub.guide_' + state.nextStep)}
            </>
          )}
        </span>
        <span className="text-muted-foreground">
          {t('dub.guide_progress', { amount: state.completed.filter(Boolean).length })}
        </span>
      </div>
      <ol className="grid grid-cols-5 gap-1 max-[1100px]:grid-cols-3">
        {STEPS.map((step, index) => {
          const done = state.completed[index];
          const current = index === state.next;
          return (
            <li key={step}>
              <button
                type="button"
                onClick={() => onSelect(step)}
                disabled={disabled || index > state.next}
                aria-current={current ? 'step' : undefined}
                className={cn(
                  'flex h-full w-full items-start gap-1.5 rounded-lg border px-2 py-2 text-left text-[11px] disabled:cursor-default',
                  done
                    ? 'border-emerald-500/25 bg-emerald-500/10'
                    : current
                      ? 'border-primary/50 bg-primary/10'
                      : 'border-transparent text-muted-foreground',
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full text-[10px]',
                    done ? 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400' : 'bg-muted',
                  )}
                >
                  {done ? (
                    <CheckIcon className="size-3" />
                  ) : current && state.running ? (
                    <LoaderCircleIcon className="size-3 animate-spin" />
                  ) : (
                    index + 1
                  )}
                </span>
                <span>
                  <span className="block font-medium">{t('dub.guide_' + step)}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {t(
                      done
                        ? 'dub.guide_complete'
                        : current
                          ? state.running
                            ? 'dub.guide_running'
                            : 'dub.guide_now'
                          : 'dub.languages_pending',
                    )}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      <div className="mt-2 flex items-center gap-2">
        <p className="min-w-0 flex-1 text-xs text-muted-foreground">
          {state.nextStep === 'audio' && audioBlocked
            ? audioBlocked
            : t('dub.guide_' + state.nextStep + '_hint')}
        </p>
        {!state.running && (
          <Button
            size="xs"
            variant="secondary"
            disabled={disabled || (state.nextStep === 'audio' && Boolean(audioBlocked))}
            onClick={() => onSelect(state.nextStep)}
          >
            {t(state.nextStep === 'export' ? 'dub.listen_complete' : 'dub.guide_continue')}
            <ArrowRightIcon />
          </Button>
        )}
      </div>
    </nav>
  );
}
