import { useTranslation } from 'react-i18next';
import { getBridge } from './bridge';
import { Button } from './ui/button';
import { Switch } from './ui/switch';
import { SettingsRow } from '@/features/settings/settings-layout';
import { useDiagnostics } from '@/hooks/use-diagnostics';
import {
  refreshDiagnosticState,
  retryDiagnosticConsent,
  setDiagnosticConsent,
} from '@/lib/diagnostic-reports';
import { useVisibleInterval } from '@/lib/visible-interval';

function choose(enabled: boolean): void {
  void setDiagnosticConsent(enabled).catch(() => {});
}

export function DiagnosticConsent() {
  const { t } = useTranslation();
  const { status, loading, saving, error } = useDiagnostics();
  if (!status && error)
    return (
      <section className="rounded-lg border border-border/60 bg-muted/20 p-3">
        <p role="alert" className="text-xs text-destructive">
          {t('diagnostics.save_failed')}{' '}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={loading || saving}
            onClick={() => void retryDiagnosticConsent().catch(() => {})}
          >
            {t('common.retry')}
          </Button>
        </p>
      </section>
    );
  if (status?.prompted || status?.opted_in)
    return (
      <section className="rounded-lg border border-border/60 bg-muted/20">
        <DiagnosticPrivacySetting />
      </section>
    );
  if (
    !status?.available ||
    status.prompted ||
    (status.externally_managed && !getBridge()?.diagnostics)
  )
    return null;
  return (
    <section
      className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3 text-left"
      data-testid="diagnostic-consent"
    >
      <h2 className="text-sm font-medium">{t('diagnostics.title')}</h2>
      <p className="text-xs leading-5 text-muted-foreground">{t('diagnostics.body')}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={saving}
          onClick={() => choose(true)}
        >
          {t('diagnostics.allow')}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={saving}
          onClick={() => choose(false)}
        >
          {t('diagnostics.keep_off')}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {t('diagnostics.save_failed')}
        </p>
      )}
    </section>
  );
}

export function DiagnosticPrivacySetting() {
  const { t } = useTranslation();
  const { status, saving, error } = useDiagnostics();
  const managed = Boolean(status?.externally_managed && !getBridge()?.diagnostics);
  return (
    <div>
      <SettingsRow
        id="privacy-diagnostics"
        title={t('diagnostics.title')}
        description={t('diagnostics.body')}
      >
        <Switch
          aria-label={t('diagnostics.title')}
          checked={Boolean(status?.opted_in)}
          disabled={!status || saving || managed || (!status.available && !status.opted_in)}
          onCheckedChange={choose}
        />
      </SettingsRow>
      {status && !status.available && (
        <p className="px-4 pb-3 text-sm text-muted-foreground">{t('diagnostics.unavailable')}</p>
      )}
      {managed && (
        <p className="px-4 pb-3 text-sm text-muted-foreground">{t('diagnostics.managed')}</p>
      )}
      {error && (
        <p role="alert" className="px-4 pb-3 text-sm text-destructive">
          {t('diagnostics.save_failed')}{' '}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void retryDiagnosticConsent().catch(() => {})}
          >
            {t('common.retry')}
          </Button>
        </p>
      )}
    </div>
  );
}

/** Keep persisted consent current without coupling capture to usage analytics. */
export function DiagnosticRuntime() {
  useDiagnostics();
  // Source/web startup can precede the settings API; main IPC can also become ready later.
  useVisibleInterval(() => void refreshDiagnosticState(), 5000);
  return null;
}
