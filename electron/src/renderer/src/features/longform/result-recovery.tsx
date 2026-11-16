import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import type { LongformResultState } from './longform-session';

/** A saved filename never authorizes a request to its previous connection. */
export function LongformResultRecovery({
  state,
  disabled,
  onRecover,
}: {
  state: LongformResultState;
  disabled: boolean;
  onRecover: () => void;
}) {
  const { t } = useTranslation();
  const legacy = state.status === 'legacy' || state.availabilityOnly;
  const message = legacy
    ? 'audiobook.result_legacy'
    : state.status === 'other-backend'
      ? 'audiobook.result_other_connection'
      : state.status === 'unknown'
        ? 'audiobook.result_connection_unknown'
        : null;
  if (!message) return null;
  return (
    <section className="space-y-2 rounded-lg border border-border/60 bg-muted/30 p-3 text-xs">
      <p role="status">{t(message)}</p>
      {state.recoveryFailed && <p role="alert">{t('audiobook.result_recovery_failed')}</p>}
      {state.status === 'legacy' && (
        <Button
          className="h-auto w-full whitespace-normal"
          variant="outline"
          size="sm"
          disabled={disabled || state.recovering}
          onClick={onRecover}
        >
          {t(state.recovering ? 'common.loading' : 'audiobook.result_check_audio')}
        </Button>
      )}
    </section>
  );
}
