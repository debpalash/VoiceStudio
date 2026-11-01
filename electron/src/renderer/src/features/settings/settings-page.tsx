import { SupportSettings } from './support-settings';
import { UpdateSettings } from './update-settings';
import { MirrorSettings } from './mirror-settings';
import { PrivacySettings } from './privacy-settings';
import { AccountSettings } from './account-settings';
import { useAccount } from '@/features/account/use-account';
import { PerformanceSettings } from './performance-settings';
import { CredentialSettings } from './credential-settings';
import { NetworkSettings } from './network-settings';
import { SharingSettings } from './sharing-settings';
import { StorageSettings } from './storage-settings';
import { MediaTools } from './media-tools';
import { LogSettings } from './log-settings';
import { PronunciationSettings } from './pronunciation-settings';
import { PauseFillSettings } from './pause-fill-settings';
import { PermissionsSettings } from './permissions-settings';
import { UsageSettings } from './usage-settings';
import { OpenApiSettings } from './openapi-settings';
import { DiagnosticsSettings } from './diagnostics-settings';
import { ModelSettings } from './model-settings';
import { modelFamilies } from './model-family';
import { Link, useRouterState } from '@tanstack/react-router';
import {
  BarChart3Icon,
  BracesIcon,
  ChevronRightIcon,
  CpuIcon,
  ExternalLinkIcon,
  HardDriveIcon,
  KeyRoundIcon,
  LockKeyholeIcon,
  RefreshCwIcon,
  Share2Icon,
  ShieldCheckIcon,
  SpellCheckIcon,
  StethoscopeIcon,
  UserRoundIcon,
  WifiIcon,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { SettingsContent, SettingsPageTitleContext } from './settings-layout';
import { SettingsNav, type SettingsLocation } from './settings-nav';
import { AppearanceResetButton, AppearanceSettings, GeneralSettings } from './settings-preferences';
import { ExistingPythonSettings } from './existing-python-settings';
import { rememberSettingsRoute } from '@/lib/settings-route';

function PerformanceAndPythonSettings() {
  return (
    <>
      <ExistingPythonSettings />
      <PerformanceSettings />
    </>
  );
}

// the generated speech sounds.
function SpeechOutputSettings() {
  return (
    <>
      <PauseFillSettings />
      <PronunciationSettings />
    </>
  );
}

const extraSettings = [
  {
    to: '/settings/pronunciation',
    label: 'pronunciation.title',
    Icon: SpellCheckIcon,
    Component: SpeechOutputSettings,
    fields: [
      'pronunciation.title',
      'pronunciation.help',
      'pronunciation.test_label',
      'pronunciation.backup_title',
      'pauseFill.title',
      'pauseFill.room_tone',
    ],
  },
  {
    to: '/settings/updates',
    label: 'updates.tab',
    Icon: RefreshCwIcon,
    Component: UpdateSettings,
    fields: ['updates.tab', 'updates.check_now'],
  },
  {
    to: '/settings/support',
    label: 'donate.title',
    Icon: ExternalLinkIcon,
    Component: SupportSettings,
    fields: ['donate.title', 'contact.channels_label'],
  },
  {
    to: '/settings/network',
    label: 'settings.network',
    Icon: WifiIcon,
    Component: NetworkSettings,
    fields: ['settings.network', 'settings.proxy'],
  },
  {
    to: '/settings/sharing',
    label: 'sharing.title',
    Icon: Share2Icon,
    Component: SharingSettings,
    fields: [
      'sharing.title',
      'sharing.local_network',
      'sharing.tailscale_title',
      'settings.remote_backend_title',
    ],
  },
  {
    to: '/settings/credentials',
    label: 'settings.credentials',
    Icon: KeyRoundIcon,
    Component: CredentialSettings,
    fields: [
      'settings.credentials',
      'settings.hf_token_input',
      'settings.translation_providers',
      'credentials.deepl_key',
      'credentials.microsoft_key',
    ],
  },
  {
    to: '/settings/account',
    label: 'account.title',
    Icon: UserRoundIcon,
    Component: AccountSettings,
    fields: ['account.title', 'account.features', 'account.sync', 'account.sign_in'],
    cloudAccountsOnly: true,
  },
  {
    to: '/settings/performance',
    label: 'settings.perf_title',
    Icon: CpuIcon,
    Component: PerformanceAndPythonSettings,
    fields: [
      'settings.perf_title',
      'settings.compute_device_title',
      'setup.system_preflight',
      'settings.perf_torch_compile',
      'settings.generate_budget_title',
    ],
  },
  {
    to: '/settings/usage',
    label: 'settings.usage',
    Icon: BarChart3Icon,
    Component: UsageSettings,
    fields: [
      'settings.usage',
      'settings.usage_takes',
      'settings.usage_audio',
      'settings.usage_by_language',
    ],
  },
  {
    to: '/settings/privacy',
    label: 'settings.privacy',
    Icon: ShieldCheckIcon,
    Component: PrivacySettings,
    fields: [
      'settings.privacy',
      'privacy.analytics_title',
      'privacy.watermark_title',
      'settings.history_retention',
    ],
  },
  {
    to: '/settings/permissions',
    label: 'permissions.title',
    Icon: LockKeyholeIcon,
    Component: PermissionsSettings,
    fields: ['permissions.title', 'permissions.microphone', 'permissions.accessibility'],
  },
  {
    to: '/settings/storage',
    label: 'settings.storage',
    Icon: HardDriveIcon,
    Component: StorageSettings,
    fields: [
      'settings.storage',
      'settings.storage_usage',
      'settings.history_retention',
      'updates.backup_line',
    ],
  },
  {
    to: '/settings/diagnostics',
    label: 'about.title',
    Icon: StethoscopeIcon,
    Component: DiagnosticsSettings,
    fields: ['about.title', 'about.diagnostics', 'about.self_check', 'about.save_bundle'],
  },
  {
    to: '/settings/openapi',
    label: 'openapi.title',
    Icon: BracesIcon,
    Component: OpenApiSettings,
    fields: ['openapi.title', 'openapi.copy_url', 'openapi.open_raw'],
  },
] as const;

/** Settings → Account is listed only when this build offers VoiceStudio Cloud accounts. */
export function extraSettingsFor(cloudAccountsAvailable: boolean) {
  return extraSettings.filter((item) => cloudAccountsAvailable || !('cloudAccountsOnly' in item));
}

export function SettingsPage() {
  const { t } = useTranslation();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const extras = extraSettingsFor(useAccount().state?.available === true);
  const extra = extras.find((item) => pathname === item.to);
  const isOpenApi = pathname === '/settings/openapi';
  const isModels = pathname.includes('/models');
  const isMedia = pathname.endsWith('/media');
  const isLogs = pathname.endsWith('/logs');
  const modelFamily = modelFamilies.find((family) => pathname.endsWith('/models/' + family));
  // /settings opens General, the first entry in the list.
  const section = pathname.endsWith('/appearance') ? 'appearance' : 'general';
  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => rememberSettingsRoute(pathname), [pathname]);
  useEffect(() => {
    if (!target) return;
    const field = document.getElementById(target);
    if (field) {
      field.scrollIntoView({ block: 'center' });
      field.focus({ preventScroll: true });
      setTarget(null);
    }
  }, [pathname, target]);
  const location: SettingsLocation = extra
    ? { kind: 'extra', to: extra.to }
    : isMedia
      ? { kind: 'media' }
      : isLogs
        ? { kind: 'logs' }
        : isModels
          ? { kind: 'models', family: modelFamily }
          : { kind: 'section', section };
  const title = extra
    ? t(extra.label)
    : isMedia
      ? t('settings.audio_tools')
      : isLogs
        ? t('settings.logs')
        : isModels
          ? t(modelFamily ? 'engineSidebar.' + modelFamily : 'modelSettings.models')
          : t(`preferences.${section}`);

  return (
    <div className="flex h-full min-h-0">
      <SettingsNav extras={extras} location={location} onTarget={setTarget} />
      <section className="flex min-w-0 flex-1 flex-col bg-background">
        <header
          className={cn(
            'workspace-titlebar flex shrink-0 items-center gap-2 px-5 text-sm',
            isMac() ? 'account-corner-safe' : 'native-controls-right',
          )}
        >
          <span className="text-muted-foreground">{t('nav.settings')}</span>
          <ChevronRightIcon aria-hidden="true" className="size-3.5 text-muted-foreground/60" />
          {modelFamily && (
            <>
              <Link
                to="/settings/models"
                className="text-muted-foreground hover:text-foreground hover:underline"
              >
                {t('modelSettings.models')}
              </Link>
              <ChevronRightIcon aria-hidden="true" className="size-3.5 text-muted-foreground/60" />
            </>
          )}
          <h1 className="font-medium">{title}</h1>
          {location.kind === 'section' && section === 'appearance' && <AppearanceResetButton />}
        </header>
        <SettingsPageTitleContext.Provider value={title}>
          <SettingsContent
            fullBleed={isOpenApi || isLogs}
            key={
              extra?.to ||
              (isMedia ? 'media' : isLogs ? 'logs' : isModels ? (modelFamily ?? 'models') : section)
            }
          >
            {extra ? (
              <extra.Component />
            ) : isMedia ? (
              <MediaTools />
            ) : isLogs ? (
              <LogSettings />
            ) : isModels ? (
              <>
                <ModelSettings key={modelFamily ?? 'all'} family={modelFamily} />
                {!modelFamily && <MirrorSettings />}
              </>
            ) : section === 'appearance' ? (
              <AppearanceSettings />
            ) : (
              <GeneralSettings />
            )}
          </SettingsContent>
        </SettingsPageTitleContext.Provider>
      </section>
    </div>
  );
}
