import { UserRoundIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { useAccount } from '../account/use-account';
import { AccountDetails } from './account-details';
import { CloudSyncPanel } from './cloud-sync-panel';
import { SettingsRow, SettingsSection } from './settings-layout';

const CALLOUT =
  'rounded-xl border border-border/50 bg-card/20 px-4 py-3 text-[13px] leading-[1.45] text-muted-foreground';

export function AccountSettings() {
  const { t } = useTranslation();
  const { supported, state, setSwitches, signIn, cancelSignIn, signOut, refresh, updateProfile } =
    useAccount();
  if (!supported || !state?.available) return null;
  const who = state.profile?.displayName ?? state.profile?.email ?? '';
  const enabled = state.accountEnabled;
  // The identity card carries the session (name, Sign out). Until a profile
  // loads, or when it fails, the plain session row keeps Sign out reachable.
  const card = enabled && state.signedIn && Boolean(state.profile);
  return (
    <div className="space-y-4">
      {enabled && (
        <AccountDetails
          state={state}
          onRetry={refresh}
          onSignOut={card ? () => void signOut() : undefined}
          onRename={card ? (name) => updateProfile({ display_name: name }) : undefined}
        />
      )}
      <SettingsSection icon={UserRoundIcon} title={t('account.title')}>
        <SettingsRow
          id="account-features"
          title={t('account.features')}
          description={t('account.features_desc')}
        >
          <Switch
            aria-label={t('account.features')}
            checked={state.accountEnabled}
            onCheckedChange={(on) => void setSwitches({ accountEnabled: on })}
          />
        </SettingsRow>
        {enabled && !card && (
          <SettingsRow
            id="account-session"
            title={state.signedIn ? t('account.signed_in_as', { name: who }) : t('account.title')}
          >
            {state.signedIn ? (
              <Button variant="outline" onClick={() => void signOut()}>
                {t('account.sign_out')}
              </Button>
            ) : state.signingIn ? (
              <span className="flex items-center gap-2 text-sm text-muted-foreground">
                {t('account.continue_in_browser')}
                <Button variant="ghost" onClick={() => void cancelSignIn()}>
                  {t('account.cancel')}
                </Button>
              </span>
            ) : (
              <Button onClick={() => void signIn()}>{t('account.sign_in')}</Button>
            )}
          </SettingsRow>
        )}
        {enabled && (
          <SettingsRow
            id="account-sync"
            title={t('account.sync')}
            description={t('account.sync_desc')}
          >
            <Switch
              aria-label={t('account.sync')}
              checked={state.syncEnabled}
              // Turning sync off is always allowed; turning it on needs a session.
              disabled={!state.signedIn && !state.syncEnabled}
              onCheckedChange={(on) => void setSwitches({ syncEnabled: on })}
            />
          </SettingsRow>
        )}
        {enabled && state.signedIn && state.syncEnabled && (
          <CloudSyncPanel planUrl={state.links?.plan} />
        )}
      </SettingsSection>
      {enabled && state.notice && (
        <p role="status" className={CALLOUT}>
          {t(`account.${state.notice}`)}
        </p>
      )}
      {enabled && !state.remembered && <p className={CALLOUT}>{t('account.not_remembered')}</p>}
    </div>
  );
}
