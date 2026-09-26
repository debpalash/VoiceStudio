import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CheckCircleIcon, TriangleAlertIcon, CircleXIcon, ScanSearchIcon } from 'lucide-react';
import type { PreflightReport } from '@shared/api/setup-types';
import { apiJson } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { SettingsRow, SettingsSection } from './settings-layout';

export function SystemPreflight() {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ['setup-preflight'],
    queryFn: ({ signal }) => apiJson<PreflightReport>('/setup/preflight', { signal }),
    enabled: false,
    retry: false,
  });
  const checks = Array.isArray(query.data?.checks) ? query.data.checks : [];
  return (
    <SettingsSection title={t('setup.system_preflight')} icon={ScanSearchIcon}>
      <SettingsRow
        id="system-preflight-check"
        title={t('setup.system_check')}
        description={t('setup.system_check_desc')}
      >
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          {t(
            query.isFetching
              ? 'setup.probing'
              : query.data
                ? 'setup.recheck'
                : 'setup.system_check',
          )}
        </Button>
      </SettingsRow>
      {query.isError && (
        <p role="alert" className="px-4 py-3 text-sm text-destructive">
          {t('backend.retry')}
        </p>
      )}
      {checks.length > 0 && (
        <ul className="divide-y divide-border/50 px-4">
          {checks.map((check) => {
            const Icon =
              check.status === 'pass'
                ? CheckCircleIcon
                : check.status === 'warn'
                  ? TriangleAlertIcon
                  : CircleXIcon;
            return (
              <li key={check.id} className="flex items-start gap-3 py-3">
                <Icon
                  aria-hidden="true"
                  className={
                    'mt-0.5 size-4 shrink-0 ' +
                    (check.status === 'pass'
                      ? 'text-success'
                      : check.status === 'warn'
                        ? 'text-warning'
                        : 'text-destructive')
                  }
                />
                <div className="min-w-0 space-y-1">
                  <h3 className="text-sm font-medium">{check.label}</h3>
                  <p className="break-words text-xs leading-5 text-muted-foreground">
                    {check.detail}
                  </p>
                  {check.fix && <p className="break-words text-xs leading-5">{check.fix}</p>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </SettingsSection>
  );
}
