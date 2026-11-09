import {
  CameraIcon,
  CheckIcon,
  CloudIcon,
  LogOutIcon,
  MailIcon,
  MinusIcon,
  PencilIcon,
  RefreshCwIcon,
  UserRoundCogIcon,
  XIcon,
} from 'lucide-react';
import { useState, type FormEvent, type ReactElement } from 'react';
import { useTranslation } from 'react-i18next';
import { getBridge } from '@/components/bridge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ExternalLink } from '@/components/external-link';
import {
  initials,
  planLook,
  storageShare,
  storageSize as size,
} from '@/features/account/account-format';
import { cn } from '@/lib/utils';
import type { AccountPublicState } from '../../../../preload/index.d';

export { initials } from '@/features/account/account-format';

/** The longest display name the account service accepts (main checks it too). */
export const DISPLAY_NAME_MAX = 200;

/** A trimmed, non-empty name within the limit, or null when it cannot be saved. */
export function cleanDisplayName(raw: string): string | null {
  const name = raw.trim().replace(/\s+/g, ' ');
  return name && name.length <= DISPLAY_NAME_MAX ? name : null;
}

/** An icon-only action with its name in a tooltip and as the accessible label. */
function IconAction({ label, children }: { label: string; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent surface="theme" side="bottom">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

/** One stat tile, in the compact card style the home page uses for recents. */
const TILE = 'min-w-0 rounded-xl border border-border/50 bg-card/20 px-4 py-3.5';
const TILE_LABEL = 'flex items-center gap-1.5 text-xs text-muted-foreground';

/**
 * Who is signed in, their plan and storage, and what the plan includes
 * (Settings → Account). Styled after the home page's primary cards: an accent
 * glow, an inset highlight, and stat tiles below the identity line.
 */
export function AccountDetails({
  state,
  onRetry,
  onSignOut,
  onRename,
}: {
  state: AccountPublicState;
  /** Reloads the account details; drives Retry and the Refresh button. */
  onRetry?: () => void | Promise<void>;
  /** Ends the session; shown as Sign out beside the identity. */
  onSignOut?: () => void;
  /** Saves a new display name to VoiceStudio Cloud; rejects when it was not saved. */
  onRename?: (name: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [refreshing, setRefreshing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [renameFailed, setRenameFailed] = useState(false);
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
  const { pro, PlanIcon } = planLook(plan);
  const share = plan ? storageShare(plan) : 0;
  const features =
    plan && plan.settingsSync !== null && plan.proDesktop !== null
      ? [
          { key: 'account.feature_settings_sync', on: plan.settingsSync },
          { key: 'account.feature_pro', on: plan.proDesktop },
        ]
      : [];
  const profileUrl = state.links?.profile;
  const cleaned = cleanDisplayName(draft);
  const startEditing = () => {
    setDraft(profile.displayName ?? '');
    setRenameFailed(false);
    setEditing(true);
  };
  const save = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!onRename || !cleaned || saving) return;
    if (cleaned === profile.displayName) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setRenameFailed(false);
    try {
      await onRename(cleaned);
      setEditing(false);
    } catch {
      setRenameFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const avatar = profile.avatarUrl ? (
    <img
      src={profile.avatarUrl}
      alt=""
      className="size-16 rounded-full object-cover ring-1 ring-border"
    />
  ) : (
    <span
      aria-hidden="true"
      className="grid size-16 place-items-center rounded-full bg-primary/15 text-xl font-semibold text-primary ring-1 ring-inset ring-primary/25 shadow-[inset_0_1px_0_rgb(255_255_255/10%)]"
    >
      {initials(name)}
    </span>
  );

  return (
    <div
      data-slot="account-card"
      className="relative isolate overflow-hidden rounded-2xl border border-[color-mix(in_oklab,var(--primary)_22%,var(--border))] bg-card bg-[radial-gradient(ellipse_at_100%_0%,color-mix(in_oklab,var(--primary)_10%,transparent),transparent_70%)] p-5 shadow-[inset_0_1px_0_color-mix(in_oklab,var(--foreground)_4%,transparent)]"
    >
      <div className="flex flex-wrap items-center gap-4">
        {profileUrl ? (
          // The photo is managed on the VoiceStudio profile page, so it syncs everywhere.
          <IconAction label={t('account.change_photo')}>
            <button
              type="button"
              data-testid="account-avatar"
              aria-label={t('account.change_photo')}
              className="group/avatar relative shrink-0 rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              onClick={() => {
                const bridge = getBridge();
                if (bridge) void bridge.files.openExternal(profileUrl).catch(() => {});
                else window.open(profileUrl, '_blank', 'noopener,noreferrer');
              }}
            >
              {avatar}
              <span
                aria-hidden="true"
                className="absolute inset-0 grid place-items-center rounded-full bg-black/50 text-white opacity-0 transition-opacity group-hover/avatar:opacity-100 group-focus-visible/avatar:opacity-100 motion-reduce:transition-none"
              >
                <CameraIcon className="size-5" />
              </span>
            </button>
          </IconAction>
        ) : (
          <span data-testid="account-avatar" className="shrink-0">
            {avatar}
          </span>
        )}
        <div className="min-w-0 flex-1">
          {editing ? (
            <form className="flex items-center gap-1.5" onSubmit={(event) => void save(event)}>
              <Input
                autoFocus
                aria-label={t('account.name_label')}
                value={draft}
                maxLength={DISPLAY_NAME_MAX}
                disabled={saving}
                className="h-8 max-w-72 text-base font-semibold"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Escape') {
                    event.preventDefault();
                    setEditing(false);
                  }
                }}
              />
              <IconAction label={t('common.save')}>
                <Button
                  type="submit"
                  size="icon-sm"
                  variant="secondary"
                  aria-label={t('common.save')}
                  aria-busy={saving}
                  disabled={!cleaned || saving}
                >
                  <CheckIcon aria-hidden="true" />
                </Button>
              </IconAction>
              <IconAction label={t('account.cancel')}>
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label={t('account.cancel')}
                  disabled={saving}
                  onClick={() => setEditing(false)}
                >
                  <XIcon aria-hidden="true" />
                </Button>
              </IconAction>
            </form>
          ) : (
            <div className="flex min-w-0 items-center gap-1">
              <p className="truncate text-lg font-semibold tracking-[-0.02em]">{name}</p>
              {onRename && (
                <IconAction label={t('account.edit_name')}>
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    aria-label={t('account.edit_name')}
                    className="text-muted-foreground"
                    onClick={startEditing}
                  >
                    <PencilIcon aria-hidden="true" />
                  </Button>
                </IconAction>
              )}
            </div>
          )}
          {profile.displayName && profile.email && (
            <p className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[13px] text-muted-foreground">
              <MailIcon className="size-3.5 shrink-0" aria-hidden="true" />
              <span className="truncate">{profile.email}</span>
            </p>
          )}
          {renameFailed && (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {t('account.rename_failed')}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {state.links && (
            <ExternalLink href={state.links.profile} variant="ghost">
              <UserRoundCogIcon aria-hidden="true" />
              {t('account.manage_profile')}
            </ExternalLink>
          )}
          {onRetry && (
            <IconAction label={t('account.refresh')}>
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                aria-label={t('account.refresh')}
                aria-busy={refreshing}
                disabled={refreshing}
                onClick={() => void refresh()}
              >
                <RefreshCwIcon
                  className={refreshing ? 'animate-spin motion-reduce:animate-none' : undefined}
                  aria-hidden="true"
                />
              </Button>
            </IconAction>
          )}
          {onSignOut && (
            <IconAction label={t('account.sign_out')}>
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                aria-label={t('account.sign_out')}
                className="text-muted-foreground hover:text-destructive"
                onClick={onSignOut}
              >
                <LogOutIcon aria-hidden="true" />
              </Button>
            </IconAction>
          )}
        </div>
      </div>
      {plan && (
        <dl className="mt-5 grid gap-2 @xl:grid-cols-2">
          <div className={TILE}>
            <dt className={TILE_LABEL}>
              <PlanIcon className="size-3.5" aria-hidden="true" />
              {t('account.plan')}
            </dt>
            <dd className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
              <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <Badge
                  data-testid="account-plan-badge"
                  data-plan={pro ? 'pro' : 'free'}
                  variant={pro ? 'default' : 'secondary'}
                  className={cn('h-6 gap-1 px-2.5 text-[13px]', pro && 'shadow-xs')}
                >
                  <PlanIcon aria-hidden="true" />
                  {plan.planName}
                </Badge>
                {plan.planEndsAt && (
                  <span className="text-xs text-muted-foreground">
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
            <dt className={cn(TILE_LABEL, 'justify-between')}>
              <span className="flex items-center gap-1.5">
                <CloudIcon className="size-3.5" aria-hidden="true" />
                {t('account.storage')}
              </span>
              <span className="tabular-nums">{Math.round(share)}%</span>
            </dt>
            <dd className="mt-2 space-y-2">
              <span className="block text-[13px] tabular-nums">
                {t('account.storage_used', {
                  used: size(plan.storageUsedBytes),
                  limit: size(plan.storageLimitBytes),
                })}
              </span>
              <Progress
                value={share}
                aria-label={t('account.storage')}
                className={cn(
                  '[&_[data-slot=progress-track]]:h-1.5 [&_[data-slot=progress-track]]:bg-foreground/10',
                  share >= 90 && '[&_[data-slot=progress-indicator]]:bg-warning',
                )}
              />
            </dd>
          </div>
          {features.length > 0 && (
            <div className={`${TILE} @xl:col-span-2`}>
              <dt className={TILE_LABEL}>{t('account.features_title')}</dt>
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
