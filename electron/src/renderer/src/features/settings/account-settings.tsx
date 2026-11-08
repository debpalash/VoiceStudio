import { UserRoundIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { useAccount } from '../account/use-account';
import { AccountDetails } from './account-details';
import { CloudSyncPanel } from './cloud-sync-panel';
import { SettingsRow, SettingsSection } from './settings-layout';

export function AccountSettings() {
  const { t } = useTranslation();
  const { supported, state, setSwitches, signIn, cancelSignIn, signOut, refresh } = useAccount();
  if (!supported || !state?.available) return null;
  const who = state.profile?.displayName ?? state.profile?.email ?? '';
  return (
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
      {state.accountEnabled && (
        <>
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
              <Button variant="outline" onClick={() => void signIn()}>
                {t('account.sign_in')}
              </Button>
            )}
          </SettingsRow>
          <AccountDetails state={state} onRetry={refresh} />
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
          {state.notice && (
            <p role="status" className="text-sm text-muted-foreground">
              {t(`account.${state.notice}`)}
            </p>
          )}
          {!state.remembered && (
            <p className="text-sm text-muted-foreground">{t('account.not_remembered')}</p>
          )}
          {state.signedIn && state.syncEnabled && <CloudSyncPanel />}
        </>
      )}
    </SettingsSection>
  );
}
