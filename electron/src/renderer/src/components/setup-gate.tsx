import { ModelLibrary, PerformanceModelPacks } from '@/features/settings/model-library';
import { AnalyticsConsent } from './analytics-consent';
import { SetupRecovery } from './setup-recovery';
import { useEffect, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import type { PreflightReport } from '../../../../../frontend/src/api/setup-types';
import { SystemPreflight } from '@/features/settings/system-preflight';
import { PermissionsSettings } from '@/features/settings/permissions-settings';
import { SetupMediaEngine } from '@/features/settings/media-tools';
import { ModelSettings } from '@/features/settings/model-settings';
import { modelFamilies, type ModelFamily } from '@/features/settings/model-family';
import { MirrorSettings } from '@/features/settings/mirror-settings';
import { PrivacySettings } from '@/features/settings/privacy-settings';
import { ShortcutSettings } from '@/features/settings/shortcut-settings';
import { brandIcon } from '@/lib/brand';
import { appearanceScales, useAppearance } from '@/hooks/use-appearance';
import { FIRST_SOUND_EVENT } from '@/lib/first-sound';
import {
  rememberSetupCompleted,
  rememberSetupStarted,
  setupWasCompleted,
  setupWasStarted,
} from '@/lib/setup-progress';
import { AudioLinesIcon, CpuIcon, SparklesIcon, ShieldCheckIcon } from 'lucide-react';

interface SetupStatus {
  models_ready: boolean;
  missing: { repo_id: string; label: string }[];
}
const steps = [
  { label: 'setup.system_check', icon: CpuIcon },
  { label: 'models.pack_title', icon: AudioLinesIcon },
  { label: 'settings.privacy', icon: ShieldCheckIcon },
  { label: 'setup.enter_studio', icon: SparklesIcon },
] as const;

export function SetupGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const appearance = useAppearance();
  const [needed, setNeeded] = useState<boolean | null>(null);
  const [setupInProgress, setSetupInProgress] = useState(setupWasStarted);
  const [step, setStep] = useState(0);
  const [advanced, setAdvanced] = useState(false);
  const [dictationSetup, setDictationSetup] = useState(false);
  const [family, setFamily] = useState<ModelFamily>('tts');
  const [consentRequired, setConsentRequired] = useState(true);
  const [enteringStudio, setEnteringStudio] = useState(false);
  const status = useQuery({
    queryKey: ['setup-status'],
    queryFn: ({ signal }) => apiJson<SetupStatus>('/setup/status', { signal }),
    refetchInterval: needed && step === 1 ? 4000 : false,
    retry: 2,
  });
  const preflight = useQuery({
    queryKey: ['setup-preflight'],
    queryFn: ({ signal }) => apiJson<PreflightReport>('/setup/preflight', { signal }),
    enabled: needed === true && step === 0,
    retry: false,
  });
  useEffect(() => {
    if (needed !== null || !status.data) return;
    const completed = setupWasCompleted();
    if (!setupInProgress && !completed) {
      // This marker belongs to the Electron shell. A populated shared model
      // cache does not prove that this installation has completed permissions,
      // privacy, media-engine, and dictation setup.
      rememberSetupStarted(true);
      setSetupInProgress(true);
    }
    setNeeded(!completed);
  }, [needed, setupInProgress, status.data]);
  if (needed === false) return <>{children}</>;
  if (needed === null)
    return (
      <div className="flex h-full min-w-0 flex-1 flex-col items-center justify-center gap-4">
        <div className="flex items-center gap-2.5">
          <img src={brandIcon} alt="" className="size-9" />
          <span className="text-lg font-semibold tracking-tight">{t('app.name')}</span>
        </div>
        <Button variant="ghost" disabled={!status.isError} onClick={() => void status.refetch()}>
          {t(status.isError ? 'backend.retry' : 'preferences.loading')}
        </Button>
      </div>
    );
  const canContinue =
    !enteringStudio &&
    (step === 0
      ? preflight.data?.ok
      : step === 1
        ? status.data?.models_ready
        : step === 2
          ? !consentRequired
          : true);
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
      <header className="workspace-titlebar flex shrink-0 items-center gap-2 border-b border-border/50 px-5">
        <img src={brandIcon} alt="" className="size-6" />
        <h1 className="text-sm font-medium">{t('app.name')}</h1>
        {advanced && (
          <div className="ml-auto flex items-center gap-1" aria-label={t('preferences.ui_scale')}>
            <span className="mr-1 text-xs text-muted-foreground">{t('preferences.ui_scale')}</span>
            {appearanceScales.map((scale) => (
              <Button
                key={scale}
                size="sm"
                variant={appearance.scale === scale ? 'secondary' : 'ghost'}
                className="h-7 px-2 text-xs tabular-nums"
                aria-pressed={appearance.scale === scale}
                onClick={() => appearance.update({ scale })}
              >
                {scale}%
              </Button>
            ))}
          </div>
        )}
      </header>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav className="grid shrink-0 grid-cols-2 gap-1 border-b border-border/50 bg-sidebar p-3 md:flex md:w-48 md:flex-col md:border-r md:border-b-0">
          {steps.map(({ label, icon: Icon }, index) => (
            <Button
              key={label}
              className="h-auto min-h-11 w-full justify-start whitespace-normal text-left"
              aria-current={index === step ? 'step' : undefined}
              variant={index === step ? 'secondary' : 'ghost'}
              disabled={index > step}
              onClick={() => setStep(index)}
            >
              <Icon />
              <span className="tabular-nums text-muted-foreground">{index + 1}.</span>
              {t(label)}
            </Button>
          ))}
        </nav>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <main key={step} className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
            <div className="mx-auto max-w-3xl space-y-6">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">{t(steps[step].label)}</h2>
                <Button
                  variant={advanced ? 'secondary' : 'outline'}
                  aria-pressed={advanced}
                  onClick={() => setAdvanced((value) => !value)}
                >
                  {t('dub.advanced')}
                </Button>
              </div>
              {step === 0 && (
                <>
                  <SystemPreflight />
                  <SetupMediaEngine />
                  {preflight.data?.checks?.some(
                    (check) => check.id === 'network' && check.status !== 'pass',
                  ) && <MirrorSettings />}
                </>
              )}
              {step === 1 && (
                <>
                  <PerformanceModelPacks compact={!advanced} />
                  {advanced && (
                    <div className="space-y-4">
                      <ModelLibrary setup />
                      <div className="flex flex-wrap gap-1">
                        {modelFamilies.map((value) => (
                          <Button
                            key={value}
                            variant={family === value ? 'secondary' : 'ghost'}
                            size="sm"
                            onClick={() => setFamily(value)}
                          >
                            {t('engineSidebar.' + value)}
                          </Button>
                        ))}
                      </div>
                      <ModelSettings family={family} showLibrary={false} />
                    </div>
                  )}
                  {Boolean(status.data?.missing?.length) && (
                    <p role="status" className="text-sm text-muted-foreground">
                      {t('setup.still_needed')}{' '}
                      {status.data?.missing?.map((model) => model.label).join(', ')}
                    </p>
                  )}
                </>
              )}
              {step < 2 &&
                (advanced ||
                  preflight.isError ||
                  preflight.data?.ok === false ||
                  status.isError) && <SetupRecovery />}
              {step === 2 && (
                <>
                  <AnalyticsConsent onRequirementChange={setConsentRequired} />
                  {advanced && <PrivacySettings showAnalytics={false} />}
                </>
              )}
              {step === 3 && (
                <div className="space-y-6">
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {t('setup.ready_desc')}
                  </p>
                  <Button
                    variant="outline"
                    aria-expanded={dictationSetup}
                    onClick={() => setDictationSetup((value) => !value)}
                  >
                    {t('demo.dictation_title')} · {t('firstrun.chip_optional')}
                  </Button>
                  {(dictationSetup || advanced) && (
                    <>
                      <PermissionsSettings />
                      <ShortcutSettings />
                    </>
                  )}
                </div>
              )}
            </div>
          </main>
          <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-border/50 p-4">
            <Button
              variant="ghost"
              disabled={step === 0}
              onClick={() => setStep((value) => value - 1)}
            >
              {t('setup.back')}
            </Button>
            <Button
              disabled={!canContinue}
              onClick={async () => {
                if (step !== steps.length - 1) {
                  setStep((value) => value + 1);
                  return;
                }
                setEnteringStudio(true);
                try {
                  // The model job can mark setup ready just before its library
                  // invalidations settle. Refresh both gates before handing off
                  // so first sound never waits forever on stale "not installed" data.
                  await Promise.all([
                    queryClient.invalidateQueries({ queryKey: ['model-catalogue'] }),
                    queryClient.invalidateQueries({ queryKey: ['engines'] }),
                  ]);
                  rememberSetupCompleted();
                  setSetupInProgress(false);
                  setNeeded(false);
                  queueMicrotask(() => window.dispatchEvent(new Event(FIRST_SOUND_EVENT)));
                } finally {
                  setEnteringStudio(false);
                }
              }}
            >
              {t(step === steps.length - 1 ? 'setup.enter_studio' : 'setup.continue_ok')}
            </Button>
          </footer>
        </div>
      </div>
    </div>
  );
}
