import { getBridge } from './bridge';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { BarChart3Icon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { setupWasStarted } from '@/lib/setup-progress';
import {
  capture,
  capturePageview,
  disableAnalytics,
  enableAnalytics,
} from '@shared/utils/analytics';
import { routeBreadcrumb } from '@/lib/report-breadcrumb';

interface AnalyticsState {
  available: boolean;
  opted_in: boolean;
  prompted: boolean;
}
const analyticsKey = ['privacy', 'analytics'] as const;

/** Keep the hardened frontend analytics client aligned with backend consent. */
export function AnalyticsRuntime() {
  const backend = useBackendStatus();
  const analytics = useQuery({
    queryKey: analyticsKey,
    queryFn: ({ signal }) => apiJson<AnalyticsState>('/api/settings/analytics', { signal }),
    enabled: backend.stage === 'ready',
    retry: false,
  });
  useEffect(() => {
    if (!analytics.data) return;
    if (analytics.data.available && analytics.data.opted_in) {
      void enableAnalytics().then(() => {
        const screen = routeBreadcrumb(window.location.hash);
        capture('screen_viewed', { stage: screen });
        capturePageview(screen);
      });
    } else {
      disableAnalytics();
    }
  }, [analytics.data]);
  return null;
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
    enabled: backend.stage === 'ready',
    retry: false,
  });
  const setup = useQuery({
    queryKey: ['setup-status'],
    queryFn: ({ signal }) => apiJson<{ models_ready: boolean }>('/setup/status', { signal }),
    enabled: backend.stage === 'ready',
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
