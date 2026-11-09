import { Menu } from '@base-ui/react/menu';
import { useNavigate } from '@tanstack/react-router';
import { Loader2Icon, LogOutIcon, UserRoundCogIcon, UserRoundIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useAccount } from '@/features/account/use-account';
import { runRendererTask } from '@/lib/global-error-recovery';
import { cn } from '@/lib/utils';

function initials(name: string): string {
  const words = name.includes('@') ? [name] : name.trim().split(/\s+/);
  return words
    .slice(0, 2)
    .map((word) => word[0] ?? '')
    .join('')
    .toUpperCase();
}

function Avatar({ name, url }: { name: string; url: string | null }) {
  if (!url && !name)
    return (
      <span className="grid size-6 shrink-0 place-items-center rounded-full bg-primary/15 text-primary">
        <UserRoundIcon className="size-4" aria-hidden="true" />
      </span>
    );
  return url ? (
    <img src={url} alt="" className="size-6 shrink-0 rounded-full object-cover" />
  ) : (
    <span
      aria-hidden="true"
      className="grid size-6 shrink-0 place-items-center rounded-full bg-primary/15 text-[11px] font-semibold text-primary"
    >
      {initials(name)}
    </span>
  );
}

const ITEM =
  'flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-highlighted:bg-accent';

/**
 * The account control in the window's top-right corner, icon only: the profile
 * photo (or initials), whose menu shows the name, Account settings and Sign out;
 * signed out, a person icon that starts sign-in. The name or action is the
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

  const name = state.profile?.displayName || state.profile?.email || '';
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
        <Avatar name={name} url={state.profile?.avatarUrl ?? null} />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={6} align="end" className="z-50">
          <Menu.Popup className="min-w-52 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg outline-none">
            {name && (
              <div className="truncate px-2 py-1.5 text-sm font-medium" data-slot="account-name">
                {name}
              </div>
            )}
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
