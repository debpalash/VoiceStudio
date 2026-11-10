import { Menu } from '@base-ui/react/menu';
import { useNavigate } from '@tanstack/react-router';
import {
  ExternalLinkIcon,
  Loader2Icon,
  LogOutIcon,
  UserRoundCogIcon,
  UserRoundIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getBridge } from '@/components/bridge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { initials, planLook, storageShare, storageSize } from '@/features/account/account-format';
import { useAccount } from '@/features/account/use-account';
import { runRendererTask } from '@/lib/global-error-recovery';
import { cn } from '@/lib/utils';

/** The profile photo from VoiceStudio Cloud, else initials, else a person icon. */
function Avatar({ name, url, large }: { name: string; url: string | null; large?: boolean }) {
  const box = large ? 'size-10 text-sm' : 'size-6 text-[11px]';
  if (url)
    return (
      <img
        src={url}
        alt=""
        draggable={false}
        className={cn(box, 'shrink-0 rounded-full object-cover ring-1 ring-border/60')}
      />
    );
  return (
    <span
      aria-hidden="true"
      className={cn(
        box,
        'grid shrink-0 place-items-center rounded-full bg-primary/15 font-semibold text-primary',
      )}
    >
      {name ? initials(name) : <UserRoundIcon className={large ? 'size-5' : 'size-4'} />}
    </span>
  );
}

const ITEM =
  'flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-highlighted:bg-accent';
const SEPARATOR = 'my-1 h-px bg-border/70';

/**
 * The account control in the window's top-right corner, icon only: the profile
 * photo (or initials). Its menu opens with an account card — the photo, name,
 * email, plan and cloud storage — then Account settings, Manage plan and Sign
 * out. Signed out, a person icon starts sign-in. The name or action is the
 * tooltip and accessible label. Nothing shows unless account features are on.
 */
export function AccountMenu({ className }: { className?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { state, signIn, signOut } = useAccount();
  if (!state?.available || !state.accountEnabled) return null;

  if (!state.signedIn) {
    const label = t(state.signingIn ? 'account.continue_in_browser' : 'account.sign_in');
    return (
      <Button
        type="button"
        size="icon-sm"
        variant="ghost"
        disabled={state.signingIn}
        onClick={() => void signIn()}
        aria-label={label}
        title={label}
        className={cn('shrink-0 rounded-full text-muted-foreground hover:text-foreground', className)}
      >
        {state.signingIn ? (
          <Loader2Icon className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        ) : (
          <UserRoundIcon className="size-4" aria-hidden="true" />
        )}
        {state.signingIn && (
          <span role="status" className="sr-only">
            {label}
          </span>
        )}
      </Button>
    );
  }

  const profile = state.profile;
  const name = profile?.displayName || profile?.email || '';
  const email = profile?.displayName ? profile.email : null;
  const plan = state.plan;
  const { pro, PlanIcon } = planLook(plan);
  const share = plan ? storageShare(plan) : 0;
  const planUrl = state.links?.plan;
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={name || t('account.menu_settings')}
        title={name || undefined}
        data-slot="account-menu"
        className={cn(
          'grid size-7 shrink-0 place-items-center rounded-full outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring',
          className,
        )}
      >
        <Avatar name={name} url={profile?.avatarUrl ?? null} />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={6} align="end" className="z-50">
          <Menu.Popup className="w-72 rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-lg outline-none">
            <div className="space-y-3 px-3 pt-3 pb-2.5" data-slot="account-card">
              <div className="flex min-w-0 items-center gap-3">
                <Avatar name={name} url={profile?.avatarUrl ?? null} large />
                <div className="min-w-0">
                  {name && (
                    <div className="truncate text-sm font-medium" data-slot="account-name">
                      {name}
                    </div>
                  )}
                  {email && (
                    <div className="truncate text-xs text-muted-foreground" data-slot="account-email">
                      {email}
                    </div>
                  )}
                </div>
              </div>
              {plan && (
                <div className="space-y-1.5" data-slot="account-plan">
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span
                      data-plan={pro ? 'pro' : 'free'}
                      className={cn(
                        'inline-flex h-5 items-center gap-1 rounded-full px-2 font-medium',
                        pro ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground',
                      )}
                    >
                      <PlanIcon className="size-3" aria-hidden="true" />
                      {plan.planName}
                    </span>
                    {plan.storageLimitBytes > 0 && (
                      <span className="truncate text-muted-foreground tabular-nums">
                        {t('account.storage_used', {
                          used: storageSize(plan.storageUsedBytes),
                          limit: storageSize(plan.storageLimitBytes),
                        })}
                      </span>
                    )}
                  </div>
                  {plan.storageLimitBytes > 0 && (
                    <Progress
                      value={share}
                      aria-label={t('account.storage')}
                      className={cn(
                        '[&_[data-slot=progress-track]]:bg-foreground/10',
                        share >= 90 && '[&_[data-slot=progress-indicator]]:bg-warning',
                      )}
                    />
                  )}
                </div>
              )}
            </div>
            <div role="separator" className={SEPARATOR} />
            <Menu.Item
              className={ITEM}
              onClick={() =>
                runRendererTask('Open account settings', () =>
                  navigate({ to: '/settings/account' }),
                )
              }
            >
              <UserRoundCogIcon className="size-4" aria-hidden="true" />
              {t('account.menu_settings')}
            </Menu.Item>
            {planUrl && (
              <Menu.Item
                className={ITEM}
                onClick={() =>
                  runRendererTask('Open plan page', () => getBridge()?.files.openExternal(planUrl))
                }
              >
                <PlanIcon className="size-4" aria-hidden="true" />
                <span className="flex-1">{t('account.manage_plan')}</span>
                <ExternalLinkIcon className="size-3.5 text-muted-foreground" aria-hidden="true" />
              </Menu.Item>
            )}
            <div role="separator" className={SEPARATOR} />
            <Menu.Item className={ITEM} onClick={() => void signOut()}>
              <LogOutIcon className="size-4" aria-hidden="true" />
              {t('account.sign_out')}
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
