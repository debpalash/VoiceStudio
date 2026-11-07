import { getBridge } from './bridge';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { BarChart3Icon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { setupWasCompleted, setupWasStarted } from '@/lib/setup-progress';
import {
  analyticsAvailable,
  capture,
  capturePageview,
  disableAnalytics,
  enableAnalytics,
} from '@shared/utils/analytics';
import { routeBreadcrumb } from '@/lib/report-breadcrumb';
import {
  readSetupAnalytics,
  rememberSetupAnalytics,
  setupAnalyticsAllowed,
  SetupFunnel,
} from '@/lib/setup-analytics';

interface AnalyticsState {
  available: boolean;
  opted_in: boolean;
  prompted: boolean;
}
const analyticsKey = ['privacy', 'analytics'] as const;

/** Keep the hardened frontend analytics client aligned with backend consent. */
export function AnalyticsRuntime() {
  const backend = useBackendStatus();
  const client = useQueryClient();
  const funnel = useRef(new SetupFunnel());
  const handedOver = useRef(false);
  const analytics = useQuery({
    queryKey: analyticsKey,
    queryFn: ({ signal }) => apiJson<AnalyticsState>('/api/settings/analytics', { signal }),
    enabled: isBackendReachable(backend.stage),
    retry: false,
  });
  // The backend's first answer ends the setup copy's say. A choice it has not
  // heard yet is handed over once; one it already holds always wins.
  useEffect(() => {
    if (!analytics.data) return;
    const early = readSetupAnalytics();
    if (!early || early.settled) return;
    if (analytics.data.prompted || analytics.data.opted_in) {
      rememberSetupAnalytics({ enabled: analytics.data.opted_in, settled: true });
      return;
    }
    if (handedOver.current) return;
    handedOver.current = true;
    rememberSetupAnalytics({ ...early, settled: true });
    // Answered in the cache first: the wizard must not ask, and race, while this saves.
    client.setQueryData(analyticsKey, (state: AnalyticsState | undefined) => ({
      ...(state as AnalyticsState),
      opted_in: early.enabled,
      prompted: true,
    }));
    void apiJson('/api/settings/analytics', {
      method: 'PUT',
      body: JSON.stringify({ enabled: early.enabled }),
    }).catch(() => {
      // Off until the wizard asks again. The early answer stays for the next launch.
      rememberSetupAnalytics(early);
      client.setQueryData(analyticsKey, (state: AnalyticsState | undefined) => ({
        ...(state as AnalyticsState),
        opted_in: false,
        prompted: false,
      }));
    });
  }, [analytics.data, client]);
  useEffect(() => {
    if (!analytics.data) return;
    if (analytics.data.available && analytics.data.opted_in && !backend.analyticsDisabled) {
      void enableAnalytics().then(() => {
        const screen = routeBreadcrumb(window.location.hash);
        capture('screen_viewed', { stage: screen });
        capturePageview(screen);
      });
    } else {
      disableAnalytics();
    }
  }, [analytics.data, backend.analyticsDisabled]);
  // Setup and startup failures happen with no backend, so the funnel is sent
  // from here, on the early answer until the backend can give its own.
  const allowed = analytics.data
    ? analytics.data.available && analytics.data.opted_in && !backend.analyticsDisabled
    : analyticsAvailable() && setupAnalyticsAllowed(readSetupAnalytics(), backend.analyticsDisabled);
  useEffect(() => {
    const events = funnel.current.observe(backend, setupWasCompleted(), allowed);
    if (!allowed || !events.length) return;
    const common = {
      app_version: __APP_VERSION__,
      platform: getBridge()?.app.platform,
      install_channel: backend.runtimePath ? 'installer' : 'source',
    };
    void enableAnalytics().then(() => {
      for (const { event, props } of events) capture(event, { ...common, ...props });
    });
  }, [allowed, backend]);
  return null;
}

/** The first-run question, asked before setup so a failed install can be counted. */
export function SetupAnalyticsConsent({ analyticsDisabled }: { analyticsDisabled?: boolean }) {
  const { t } = useTranslation();
  const [answered, setAnswered] = useState(() => readSetupAnalytics() !== null);
  if (answered || analyticsDisabled || !analyticsAvailable() || setupWasCompleted()) return null;
  const choose = (enabled: boolean) => {
    rememberSetupAnalytics({ enabled, settled: false });
    setAnswered(true);
    if (enabled) void enableAnalytics();
    else disableAnalytics();
  };
  return (
    <section
      className="w-full space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3 text-left"
      data-testid="setup-analytics-consent"
    >
      <h2 className="text-sm font-medium">{t('consent.title')}</h2>
      <p className="text-xs leading-5 text-muted-foreground">
        {t('consent.body')} {t('consent.setup_note')}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => choose(true)}>
          {t('consent.yes')}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => choose(false)}>
          {t('consent.no')}
        </Button>
      </div>
    </section>
  );
}

export function AnalyticsConsent({
  onRequirementChange,
  onDone,
  compact = false,
}: {
  onRequirementChange?: (required: boolean) => void;
  onDone?: (enabled: boolean) => void;
  compact?: boolean;
} = {}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const pending = useRef(false);
  const query = useQuery({
    queryKey: analyticsKey,
    queryFn: ({ signal }) => apiJson<AnalyticsState>('/api/settings/analytics', { signal }),
    retry: false,
  });
  const consentRequired = Boolean(
    query.data?.available && !query.data.prompted && !query.data.opted_in,
  );
  useEffect(() => {
    if (query.isPending) return;
    onRequirementChange?.(consentRequired);
  }, [consentRequired, onRequirementChange, query.isPending]);
  const choose = async (enabled: boolean) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setFailed(false);
    try {
      await apiJson('/api/settings/analytics', {
        method: 'PUT',
        body: JSON.stringify({ enabled }),
      });
      client.setQueryData(analyticsKey, (state: AnalyticsState | undefined) => ({
        ...state,
        opted_in: enabled,
        prompted: true,
      }));
      onDone?.(enabled);
    } catch {
      setFailed(true);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  if (query.isError)
    return (
      <Button variant="ghost" onClick={() => void query.refetch()}>
        {t('backend.retry')}
      </Button>
    );
  if (!query.data?.available || query.data.prompted || query.data.opted_in) return null;
  return (
    <section className={compact ? 'space-y-2' : 'space-y-3'}>
      {!compact && <h2 className="text-base font-medium">{t('consent.title')}</h2>}
      <p className="text-sm leading-6 text-muted-foreground">{t('consent.body')}</p>
      <Button
        type="button"
        variant="link"
        className="h-auto p-0"
        onClick={() => {
          const url = 'https://github.com/debpalash/VoiceStudio#-faq';
          const bridge = getBridge();
          if (bridge) void bridge.files.openExternal(url).catch(() => setFailed(true));
          else window.open(url, '_blank', 'noopener,noreferrer');
        }}
      >
        {t('consent.learn_more')}
      </Button>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" disabled={busy} onClick={() => void choose(true)}>
          {t('consent.yes')}
        </Button>
        <Button type="button" variant="outline" disabled={busy} onClick={() => void choose(false)}>
          {t('consent.no')}
        </Button>
      </div>
      {failed && (
        <p role="alert" className="text-sm text-destructive">
          {t('common.error')}
        </p>
      )}
    </section>
  );
}

/** One-time, non-modal consent prompt for configured installs predating onboarding. */
export function AnalyticsConsentBanner() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const backend = useBackendStatus();
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const analytics = useQuery({
    queryKey: analyticsKey,
    queryFn: ({ signal }) => apiJson<AnalyticsState>('/api/settings/analytics', { signal }),
    enabled: isBackendReachable(backend.stage),
    retry: false,
  });
  const setup = useQuery({
    queryKey: ['setup-status'],
    queryFn: ({ signal }) => apiJson<{ models_ready: boolean }>('/setup/status', { signal }),
    enabled: isBackendReachable(backend.stage),
    retry: false,
  });
  const state = analytics.data;
  if (
    hidden ||
    setupWasStarted() ||
    setup.data?.models_ready !== true ||
    !state?.available ||
    state.prompted ||
    state.opted_in
  )
    return null;

  const dismiss = async () => {
    if (busy) return;
    setBusy(true);
    setHidden(true);
    try {
      await apiJson('/api/settings/analytics', {
        method: 'PUT',
        body: JSON.stringify({ enabled: false }),
      });
      client.setQueryData(analyticsKey, {
        ...state,
        opted_in: false,
        prompted: true,
      });
    } catch {
      // Analytics remains off; a failed choice can be offered again next launch.
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside
      aria-label={t('consent.title')}
      className="glass-panel fixed top-4 left-1/2 z-[70] flex w-[min(680px,calc(100vw-32px))] -translate-x-1/2 items-start gap-3 rounded-xl border border-border/60 bg-popover/85 p-4 shadow-xl backdrop-blur-2xl"
    >
      <BarChart3Icon className="mt-1 size-4 shrink-0 text-primary" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <h2 className="mb-1 text-sm font-semibold">{t('consent.title')}</h2>
        <AnalyticsConsent compact onDone={() => setHidden(true)} />
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        disabled={busy}
        aria-label={t('consent.dismiss')}
        title={t('consent.dismiss')}
        onClick={() => void dismiss()}
      >
        <XIcon />
      </Button>
    </aside>
  );
}
