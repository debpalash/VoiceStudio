import { NativeDictationSync } from '@/hooks/use-native-dictation';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { GenerationProvider } from '@/hooks/use-generate';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { queryClient } from '@/lib/query';
import { router } from './router';
import { ErrorBoundary } from '@/components/error-boundary';
import { UpdatePrompt } from '@/components/update-prompt';
import { AnalyticsConsentBanner, AnalyticsRuntime } from '@/components/analytics-consent';
import { GpuOfferBanner } from '@/components/gpu-offer';
import { CloudSyncRuntime } from '@/features/account/cloud-sync-runtime';
import { getBridge } from '@/components/bridge';
import { FirstSoundHandoff } from '@/components/first-sound-handoff';
import { stepAppearanceScale } from '@/hooks/use-appearance';
import { recordRouteBreadcrumb } from '@/lib/report-breadcrumb';
import { ModelInstallSync } from '@/hooks/use-model-install-sync';
import { RealtimeEventSync } from '@/hooks/use-realtime-events';
import { GenerateBudgetSync } from '@/hooks/use-generate-budget-sync';
import { runRendererTask } from '@/lib/global-error-recovery';
import { WebAuthGate } from '@/components/web-auth-gate';
import { ModelLicenceGate } from '@/features/settings/model-licence-acceptance';

export function App() {
  const { t } = useTranslation();
  useEffect(() => {
    const record = () => recordRouteBreadcrumb();
    record();
    window.addEventListener('hashchange', record);
    return () => window.removeEventListener('hashchange', record);
  }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
      const direction =
        event.key === '0' || event.code === 'Digit0' || event.code === 'Numpad0'
          ? 0
          : event.key === '-' || event.code === 'Minus' || event.code === 'NumpadSubtract'
            ? -1
            : event.key === '+' || event.code === 'Equal' || event.code === 'NumpadAdd'
              ? 1
              : null;
      if (direction === null) return;
      event.preventDefault();
      event.stopPropagation();
      stepAppearanceScale(direction);
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, []);
  useEffect(() => {
    void window.voicestudio?.capture
      ?.labels({
        show: `${t('common.open')} ${t('app.name')}`,
        start: t('transcriptions.capture'),
        stop: t('clone.stop_recording'),
        settings: t('nav.settings'),
        exit: t('crash.field_exit'),
      })
      .catch(() => {});
  }, [t]);
  useEffect(() => {
    const app = getBridge()?.app;
    if (!app) return;
    return app.onNavigate((path) => {
      if (path === '/settings')
        runRendererTask('Native navigation to settings', () =>
          router.navigate({ to: '/settings' }),
        );
    });
  }, []);
  useEffect(() => {
    const app = getBridge()?.app;
    if (!app?.onPersistenceFlush) return;
    return app.onPersistenceFlush(async () => {
      const [{ flushLongformSessionPersistence }, { projectLibrary }] = await Promise.all([
        import('@/features/longform/longform-session'),
        import('@/features/longform/project-library'),
      ]);
      flushLongformSessionPersistence();
      await projectLibrary.flush();
    });
  }, []);
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <WebAuthGate>
          <NativeDictationSync />
          <ModelInstallSync />
          <RealtimeEventSync />
          <GenerateBudgetSync />
          <UpdatePrompt />
          <AnalyticsRuntime />
          <CloudSyncRuntime />
          <AnalyticsConsentBanner />
          <GpuOfferBanner />
          <GenerationProvider>
            <FirstSoundHandoff />
            <TooltipProvider>
              <RouterProvider router={router} />
              <ModelLicenceGate />
              <Toaster richColors position="bottom-right" />
            </TooltipProvider>
          </GenerationProvider>
        </WebAuthGate>
      </ErrorBoundary>
    </QueryClientProvider>
  );
}
