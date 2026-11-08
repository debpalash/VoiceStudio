import { CheckIcon, MinusIcon, RefreshCwIcon } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { ExternalLink } from '@/components/external-link';
import type { AccountPublicState } from '../../../../preload/index.d';

function size(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(0, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function initials(name: string): string {
  const words = name.includes('@') ? [name] : name.trim().split(/\s+/);
  return words
    .slice(0, 2)
    .map((w) => w[0] ?? '')
    .join('')
    .toUpperCase();
}

/** Who is signed in, their plan and storage, and what the plan includes (Settings → Account). */
export function AccountDetails({
  state,
  onRetry,
}: {
  state: AccountPublicState;
  /** Reloads the account details; drives Retry and the Refresh button. */
  onRetry?: () => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    if (!onRetry || refreshing) return;
    setRefreshing(true);
    try {
      await onRetry();
    } finally {
      setRefreshing(false);
    }
  };
  const profile = state.profile;
  if (state.signedIn && !profile && state.profileError)
    return (
      <div
        role="alert"
        className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 p-4 text-sm"
      >
        <span>{t('account.details_failed', { reason: state.profileError })}</span>
        <Button type="button" size="sm" variant="outline" onClick={onRetry}>
          {t('account.retry')}
        </Button>
      </div>
    );
  if (!state.signedIn || !profile) return null;
  const name = profile.displayName || profile.email || '';
  const plan = state.plan;
  const share =
    plan && plan.storageLimitBytes > 0
      ? Math.min(100, (plan.storageUsedBytes / plan.storageLimitBytes) * 100)
      : 0;
  const features =
    plan && plan.settingsSync !== null && plan.proDesktop !== null
      ? [
          { key: 'account.feature_settings_sync', on: plan.settingsSync },
          { key: 'account.feature_pro', on: plan.proDesktop },
        ]
      : [];
  return (
    <div className="space-y-4 rounded-lg border border-border/60 p-4">
      <div className="flex items-center gap-3">
        {profile.avatarUrl ? (
          <img src={profile.avatarUrl} alt="" className="size-12 rounded-full object-cover" />
        ) : (
          <span
            aria-hidden="true"
            className="grid size-12 place-items-center rounded-full bg-primary/15 text-base font-semibold text-primary"
          >
            {initials(name)}
          </span>
        )}
        <div className="min-w-0 flex-1">
          {profile.displayName && <p className="truncate font-medium">{profile.displayName}</p>}
          {profile.email && (
            <p className="truncate text-sm text-muted-foreground">{profile.email}</p>
          )}
        </div>
        {state.links && (
          <ExternalLink href={state.links.profile}>{t('account.manage_profile')}</ExternalLink>
        )}
        {onRetry && (
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            aria-label={t('account.refresh')}
            title={t('account.refresh')}
            aria-busy={refreshing}
            disabled={refreshing}
            onClick={() => void refresh()}
          >
            <RefreshCwIcon className={refreshing ? 'animate-spin' : undefined} aria-hidden="true" />
          </Button>
        )}
      </div>
      {plan && (
        <dl className="grid gap-3 text-sm sm:grid-cols-[8rem_1fr]">
          <dt className="text-muted-foreground">{t('account.plan')}</dt>
          <dd className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="font-medium">{plan.planName}</span>
            {plan.planEndsAt && (
              <span className="text-muted-foreground">
                {t('account.plan_ends', { date: new Date(plan.planEndsAt).toLocaleDateString() })}
              </span>
            )}
            {state.links && (
              <ExternalLink href={state.links.plan}>{t('account.manage_plan')}</ExternalLink>
            )}
          </dd>
          <dt className="text-muted-foreground">{t('account.storage')}</dt>
          <dd className="space-y-1.5">
            <Progress value={share} aria-label={t('account.storage')} />
            <span className="text-muted-foreground">
              {t('account.storage_used', {
                used: size(plan.storageUsedBytes),
                limit: size(plan.storageLimitBytes),
              })}
            </span>
          </dd>
          {features.length > 0 && (
            <>
              <dt className="text-muted-foreground">{t('account.features_title')}</dt>
              <dd>
                <ul className="space-y-1">
                  {features.map((feature) => (
                    <li key={feature.key} className="flex items-center gap-2">
                      {feature.on ? (
                        <CheckIcon className="size-4 text-success" aria-hidden="true" />
                      ) : (
                        <MinusIcon className="size-4 text-muted-foreground" aria-hidden="true" />
                      )}
                      <span>{t(feature.key)}</span>
                      <span className="text-xs text-muted-foreground">
                        {t(feature.on ? 'account.included' : 'account.not_included')}
                      </span>
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
        </dl>
      )}
    </div>
  );
}
