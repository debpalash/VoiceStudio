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

/** One stat tile, in the compact card style the home page uses for recents. */
const TILE = 'min-w-0 rounded-xl border border-border/50 bg-card/20 px-4 py-3.5';

/**
 * Who is signed in, their plan and storage, and what the plan includes
 * (Settings → Account). Styled after the home page's primary cards: an accent
 * glow, an inset highlight, and stat tiles below the identity line.
 */
export function AccountDetails({
  state,
  onRetry,
  onSignOut,
}: {
  state: AccountPublicState;
  /** Reloads the account details; drives Retry and the Refresh button. */
  onRetry?: () => void | Promise<void>;
  /** Ends the session; shown as Sign out beside the identity. */
  onSignOut?: () => void;
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
        className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/[0.06] px-4 py-3 text-[13px]"
      >
        <span className="min-w-0 flex-1">
          {t('account.details_failed', { reason: state.profileError })}
        </span>
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
    <div
      data-slot="account-card"
      className="relative isolate overflow-hidden rounded-2xl border border-[color-mix(in_oklab,var(--primary)_22%,var(--border))] bg-card bg-[radial-gradient(ellipse_at_100%_0%,color-mix(in_oklab,var(--primary)_10%,transparent),transparent_70%)] p-5 shadow-[inset_0_1px_0_color-mix(in_oklab,var(--foreground)_4%,transparent)]"
    >
      <div className="flex flex-wrap items-center gap-4">
        {profile.avatarUrl ? (
          <img
            src={profile.avatarUrl}
            alt=""
            className="size-14 shrink-0 rounded-full object-cover ring-1 ring-border"
          />
        ) : (
          <span
            aria-hidden="true"
            className="grid size-14 shrink-0 place-items-center rounded-full bg-primary/15 text-lg font-semibold text-primary ring-1 ring-inset ring-primary/25 shadow-[inset_0_1px_0_rgb(255_255_255/10%)]"
          >
            {initials(name)}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-base font-semibold tracking-[-0.015em]">{name}</p>
          {profile.displayName && profile.email && (
            <p className="truncate text-[13px] text-muted-foreground">{profile.email}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {state.links && (
            <ExternalLink href={state.links.profile} variant="ghost">
              {t('account.manage_profile')}
            </ExternalLink>
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
              <RefreshCwIcon
                className={refreshing ? 'animate-spin motion-reduce:animate-none' : undefined}
                aria-hidden="true"
              />
            </Button>
          )}
          {onSignOut && (
            <Button type="button" size="sm" variant="outline" onClick={onSignOut}>
              {t('account.sign_out')}
            </Button>
          )}
        </div>
      </div>
      {plan && (
        <dl className="mt-5 grid gap-2 @xl:grid-cols-2">
          <div className={TILE}>
            <dt className="text-xs text-muted-foreground">{t('account.plan')}</dt>
            <dd className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
              <span className="min-w-0">
                <span className="block text-lg font-semibold tracking-[-0.02em]">
                  {plan.planName}
                </span>
                {plan.planEndsAt && (
                  <span className="block text-xs text-muted-foreground">
                    {t('account.plan_ends', {
                      date: new Date(plan.planEndsAt).toLocaleDateString(),
                    })}
                  </span>
                )}
              </span>
              {state.links && (
                <ExternalLink href={state.links.plan} variant="secondary">
                  {t('account.manage_plan')}
                </ExternalLink>
              )}
            </dd>
          </div>
          <div className={TILE}>
            <dt className="text-xs text-muted-foreground">{t('account.storage')}</dt>
            <dd className="mt-1 space-y-2.5">
              <span className="flex items-baseline justify-between gap-3">
                <span className="text-[13px] tabular-nums">
                  {t('account.storage_used', {
                    used: size(plan.storageUsedBytes),
                    limit: size(plan.storageLimitBytes),
                  })}
                </span>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {Math.round(share)}%
                </span>
              </span>
              <Progress
                value={share}
                aria-label={t('account.storage')}
                className="[&_[data-slot=progress-track]]:h-1.5 [&_[data-slot=progress-track]]:bg-foreground/10"
              />
            </dd>
          </div>
          {features.length > 0 && (
            <div className={`${TILE} @xl:col-span-2`}>
              <dt className="text-xs text-muted-foreground">{t('account.features_title')}</dt>
              <dd className="mt-2">
                <ul className="flex flex-wrap gap-2">
                  {features.map((feature) => (
                    <li
                      key={feature.key}
                      className="flex items-center gap-2 rounded-lg border border-border/50 bg-background/30 px-2.5 py-1.5 text-[13px]"
                    >
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
            </div>
          )}
        </dl>
      )}
    </div>
  );
}
