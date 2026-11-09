import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useAccount } from '@/features/account/use-account';

/** First-run choice (owner rule 2026-10-16): equal-weight Yes/No, skipping = off. */
export function AccountConsent({
  onRequirementChange,
}: {
  onRequirementChange?: (required: boolean) => void;
}) {
  const { t } = useTranslation();
  const { supported, state, setSwitches } = useAccount();
  const required = Boolean(supported && state?.available && !state.prompted);
  useEffect(() => {
    if (!supported || state) onRequirementChange?.(required);
  }, [required, supported, state, onRequirementChange]);
  if (!required) return null;
  return (
    <section className="space-y-3">
      <h2 className="text-base font-medium">{t('account.title')}</h2>
      <p className="text-sm leading-6 text-muted-foreground">{t('account.setup_body')}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => void setSwitches({ accountEnabled: true })}
        >
          {t('account.use_yes')}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => void setSwitches({ accountEnabled: false, syncEnabled: false })}
        >
          {t('account.use_no')}
        </Button>
      </div>
    </section>
  );
}
