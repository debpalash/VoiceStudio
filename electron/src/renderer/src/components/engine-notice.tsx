import { useCallback, useState, type ReactNode } from 'react';
import { Link } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { LoaderCircleIcon, TriangleAlertIcon, WrenchIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';
import { useModelInstall } from '@/hooks/use-model-install';
import { InstallProgress } from '@/components/model-install-progress';
import { apiJson, describeError } from '@/lib/api/client';
import { openRepairAgent } from '@/lib/repair-agent-events';
import type { EnginesResponse } from '@/lib/api/types';
import type { ModelCatalogueResponse } from '@/features/settings/model-catalogue-query';

interface RecommendedModel {
  repo_id: string;
  role: string;
  required: boolean;
  installed: boolean;
}

interface ModelRecommendations {
  models: RecommendedModel[];
}

export type EngineSetup = ReturnType<typeof useEngineSetup>;

/**
 * Readiness of text-to-speech for `operation` plus the one-click setup that
 * restores it (download the selected or recommended model, or activate an
 * installed engine). Shared by the standalone notice and by composers, whose
 * primary action becomes this setup while TTS is not ready.
 */
export function useEngineSetup(operation = 'tts') {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [fixing, setFixing] = useState(false);
  const blocker = useTtsReadiness(operation);

  const refreshReadiness = useCallback(
    () =>
      Promise.all(
        ['model-install-jobs', 'model-catalogue', 'model-recommendations', 'engines'].map((key) =>
          client.invalidateQueries({ queryKey: [key] }),
        ),
      ),
    [client],
  );

  const repair = useCallback(
    (detail: string) => {
      openRepairAgent(
        `ACTION_REQUEST: Restore text-to-speech readiness for operation "${operation}". ${detail}`,
        true,
      );
    },
    [operation],
  );

  const activateAvailableEngine = useCallback(
    async (known?: EnginesResponse): Promise<boolean> => {
      const engines = known ?? (await apiJson<EnginesResponse>('/engines'));
      const current = engines.tts.backends.find((backend) => backend.id === engines.tts.active);
      if (current?.available) {
        await refreshReadiness();
        return true;
      }
      const available = engines.tts.backends.filter(
        (backend) => backend.available && backend.routing_status !== 'unavailable',
      );
      const candidate = available.find((backend) => backend.id === 'omnivoice') ?? available[0];
      if (!candidate) return false;
      await apiJson('/engines/select', {
        method: 'POST',
        body: JSON.stringify({ family: 'tts', backend_id: candidate.id }),
      });
      await refreshReadiness();
      return true;
    },
    [refreshReadiness],
  );

  const install = useModelInstall(undefined, {
    onReady: () => {
      void (async () => {
        try {
          if (!(await activateAvailableEngine())) {
            repair('The model installed, but no compatible local TTS engine became available.');
          }
        } catch (error) {
          repair(`The model installed, but activation failed: ${describeError(error)}`);
        }
      })();
    },
    // A full disk or a gated repository has an exact next step on screen;
    // only an unexplained failure goes to the repair agent.
    onFailed: (view) => {
      if (!view.problem) repair(view.detail || 'The model installer stopped before completion.');
    },
  });

  const startInstall = async (repo: string) => {
    if ((await install.start(repo)) === 'started')
      toast.success(t('models.started_downloading', { count: 1 }));
  };
  const fix = async () => {
    setFixing(true);
    try {
      // Readiness also blocks when the selected engine is available but the
      // model chosen for it is not downloaded. Download that model first,
      // keeping the user's engine choice; a recommended model alone would not
      // unblock that engine.
      const engines = await apiJson<EnginesResponse>('/engines');
      const active = engines.tts?.backends?.find((backend) => backend.id === engines.tts.active);
      const selectedModel = active?.available ? engines.tts.active_model : null;
      if (selectedModel) {
        const catalogue = await apiJson<ModelCatalogueResponse>('/models');
        const entry = catalogue.models?.find((model) => model.repo_id === selectedModel);
        if (entry && (!entry.installed || entry.incomplete)) {
          await startInstall(selectedModel);
          return;
        }
      }
      const recommendation = await apiJson<ModelRecommendations>('/setup/recommendations');
      const missing = recommendation.models.find(
        (item) => item.required && item.role.toLowerCase() === 'tts' && !item.installed,
      );
      if (missing) {
        await startInstall(missing.repo_id);
        return;
      }
      if (!(await activateAvailableEngine(engines))) {
        repair(
          'The required model is installed, but no compatible engine is ready. Diagnose the live engine state and complete supported app-level recovery.',
        );
      }
    } catch (error) {
      repair(`Automatic model setup failed: ${describeError(error)}`);
    } finally {
      setFixing(false);
    }
  };
  const view = install.view;
  return {
    blocked: blocker === 'engine',
    busy: fixing || install.busy,
    /** A download this setup started or joined, until it ends or is dismissed. */
    install: view && view.stage !== 'ready' && view.stage !== 'cancelled' ? install : null,
    fix,
  };
}

/** Download progress for the setup, or nothing while none is running. */
export function EngineSetupProgress({
  setup,
  className,
}: {
  setup: EngineSetup;
  className?: string;
}) {
  const install = setup.install;
  if (!install?.view) return null;
  return (
    <InstallProgress
      className={className}
      view={install.view}
      busy={install.busy}
      onCancel={() => void install.cancel()}
      onRetry={() => void install.retry()}
      onDismiss={install.view.stage === 'failed' ? install.dismiss : undefined}
    />
  );
}

/** "Set up" in a notice, and the composer's primary action while TTS is not ready. */
export function EngineSetupButton({
  setup,
  primary = false,
  className,
}: {
  setup: EngineSetup;
  primary?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const working = setup.busy || Boolean(setup.install);
  return (
    <Button
      type="button"
      size={primary ? 'lg' : 'sm'}
      className={className}
      disabled={working}
      aria-busy={working || undefined}
      onClick={() => void setup.fix()}
    >
      {working ? (
        <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
      ) : (
        <WrenchIcon />
      )}
      <span className="truncate">{t('engines.set_up')}</span>
    </Button>
  );
}

/** One calm line above a composer whose primary action is the setup. */
export function EngineSetupStatus({ setup, id }: { setup: EngineSetup; id?: string }) {
  const { t } = useTranslation();
  return (
    <div id={id} className="mb-2.5 space-y-2 px-1">
      <p role="status" className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
        <TriangleAlertIcon aria-hidden="true" className="size-3.5 shrink-0 text-warning" />
        <span className="min-w-0 truncate">{t('engines.none_ready_title')}</span>
      </p>
      <EngineSetupProgress setup={setup} />
    </div>
  );
}

/** The one "Set up" action for a model family whose setup lives in Settings → Models. */
export function SetUpLink({
  family,
  className,
}: {
  family: 'tts' | 'asr' | 'dictation' | 'translation' | 'diarisation' | 'llm';
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <Link
      to="/settings/models/$family"
      params={{ family }}
      className={cn(buttonVariants({ size: 'sm' }), 'shrink-0', className)}
    >
      <WrenchIcon />
      {t('engines.set_up')}
    </Link>
  );
}

const noticeIcon = (
  <span
    aria-hidden="true"
    className="flex size-7 shrink-0 items-center justify-center rounded-full bg-warning/10 text-warning"
  >
    <TriangleAlertIcon className="size-3.5" />
  </span>
);

/**
 * The single "not ready" banner every workspace uses: a warning mark, what is
 * missing, an optional line on what happens next, and one action. Compact is
 * one slim row for pages where vertical space belongs to the work itself.
 */
export function NotReadyNotice({
  title,
  body,
  action,
  compact = false,
  className,
}: {
  title: string;
  body?: string;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}) {
  if (compact) {
    return (
      <div
        role="status"
        data-slot="not-ready-notice"
        className={cn(
          'flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border/60 bg-muted/20 px-2.5 py-1.5 text-sm',
          className,
        )}
      >
        {noticeIcon}
        <p className="min-w-40 flex-1 truncate font-medium text-foreground" title={body}>
          {title}
          {body && <span className="sr-only"> {body}</span>}
        </p>
        {action}
      </div>
    );
  }
  return (
    <div
      role="alert"
      data-slot="not-ready-notice"
      className={cn(
        'flex min-w-0 items-start gap-3 rounded-xl border border-border/60 bg-muted/20 px-3.5 py-3 text-sm',
        className,
      )}
    >
      {noticeIcon}
      <div className="min-w-0 flex-1 pt-0.5">
        <p className="font-medium text-foreground">{title}</p>
        {body && <p className="mt-0.5 text-muted-foreground">{body}</p>}
        {action && <div className="mt-2.5">{action}</div>}
      </div>
    </div>
  );
}

export function EngineNotice({
  operation = 'tts',
  compact = false,
  className,
}: {
  operation?: string;
  compact?: boolean;
  className?: string;
}) {
  return (
    <EngineSetupNotice setup={useEngineSetup(operation)} compact={compact} className={className} />
  );
}

/**
 * The notice for a setup owned higher up, so a download it started keeps its
 * progress and Cancel when the notice hands over to the composer.
 */
export function EngineSetupNotice({
  setup,
  compact = false,
  className,
}: {
  setup: EngineSetup;
  compact?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  if (!setup.blocked) return null;
  const action = setup.install ? (
    <EngineSetupProgress
      setup={setup}
      className={compact ? 'min-w-48 flex-1 basis-full' : undefined}
    />
  ) : (
    <EngineSetupButton setup={setup} className="shrink-0" />
  );
  return (
    <NotReadyNotice
      compact={compact}
      className={className}
      title={t('engines.none_ready_title')}
      body={t('engines.none_ready_body')}
      action={action}
    />
  );
}
