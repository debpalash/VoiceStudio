import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { CheckIcon, DownloadIcon, MicIcon } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { AgentFixButton } from '@/components/agent-fix-button';
import { InstallProgress } from '@/components/model-install-progress';
import { useModelInstall } from '@/hooks/use-model-install';
import { apiJson, describeError } from '@/lib/api/client';
import { fmtBytes } from '@shared/components/settings/models/format';

interface DictationModel {
  id: string;
  repo_id: string;
  label: string;
  tag: 'offline' | 'streaming';
  recommended: boolean;
  size_gb: number;
  languages?: string;
  installed: boolean;
}

interface DictationCatalogue {
  models: DictationModel[];
  engine_available: boolean;
}

export function DictationSetup({ onReady }: { onReady: () => void }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [failed, setFailed] = useState<string | null>(null);
  const catalogue = useQuery({
    queryKey: ['dictation-setup-models'],
    queryFn: () => apiJson<DictationCatalogue>('/dictation/models'),
  });
  const install = useModelInstall(undefined, {
    onReady: () => {
      void Promise.all(
        [
          'dictation-setup-models',
          'transcription-readiness',
          'settings-dictation',
          'sidebar-dictation',
          'model-catalogue',
        ].map((key) => client.invalidateQueries({ queryKey: [key] })),
      )
        .then(onReady)
        .catch((error) => setFailed(describeError(error)));
    },
  });
  const models = Array.isArray(catalogue.data?.models) ? catalogue.data.models : [];
  const activeModel = models.find((model) => model.repo_id === install.repoId);
  const installing = install.active;

  const primary = useMemo(
    () => models.find((model) => model.installed) ?? models.find((model) => model.recommended),
    [models],
  );
  const others = models.filter((model) => model.id !== primary?.id);

  const choose = async (model: DictationModel) => {
    if (installing) return;
    setFailed(null);
    try {
      await apiJson('/dictation/prefs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: model.id, enabled: true }),
      });
      if (model.installed) {
        await client.invalidateQueries({
          queryKey: ['transcription-readiness'],
        });
        onReady();
        return;
      }
      await install.start(model.repo_id);
    } catch (error) {
      setFailed(describeError(error));
    }
  };

  const choice = (model: DictationModel) => (
    <div
      key={model.id}
      className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg border border-border/50 bg-background/25 px-3 py-2"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{model.label}</p>
        <p className="truncate text-xs text-muted-foreground">
          {model.languages || t(`asr_missing.group_${model.tag}`)} ·{' '}
          {model.size_gb < 1 ? fmtBytes(model.size_gb * 1024 ** 3) : `${model.size_gb} GB`}
        </p>
      </div>
      {model.installed && (
        <span className="inline-flex items-center gap-1 text-xs text-primary">
          <CheckIcon aria-hidden="true" className="size-3.5" />
          {t('asr_missing.installed')}
        </span>
      )}
      <Button
        size="sm"
        variant={model === primary ? 'default' : 'outline'}
        disabled={installing}
        onClick={() => void choose(model)}
      >
        {model.installed ? <CheckIcon /> : <DownloadIcon />}
        {t(model.installed ? 'asr_missing.use' : 'asr_missing.download', {
          label: model.label,
        })}
      </Button>
    </div>
  );

  if (catalogue.isPending) {
    return (
      <div
        role="status"
        className="border-b border-border/50 px-6 py-4 text-sm text-muted-foreground"
      >
        {t('preferences.loading')}
      </div>
    );
  }

  if (catalogue.isError || !catalogue.data?.engine_available || !primary) {
    return (
      <div className="flex flex-wrap items-center gap-3 border-b border-border/50 px-6 py-4 text-sm">
        <span>{t('asr_missing.message')}</span>
        <Link
          to="/settings/models/$family"
          params={{ family: 'dictation' }}
          className={buttonVariants({ variant: 'outline', size: 'sm' })}
        >
          {t('nav.settings')}
        </Link>
        <AgentFixButton request="Restore local dictation readiness. Diagnose the unavailable model catalogue or runtime, then install and activate the best supported dictation model through VoiceStudio's supported setup interfaces." />
      </div>
    );
  }

  return (
    <div className="space-y-3 border-b border-border/50 bg-muted/15 px-6 py-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <MicIcon aria-hidden="true" className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{t('asr_missing.message')}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t('asr_missing.choose')}</p>
        </div>
      </div>
      {choice(primary)}
      {install.view && install.view.stage !== 'ready' && (
        <InstallProgress
          view={install.view}
          name={activeModel?.label}
          busy={install.busy}
          onCancel={() => void install.cancel()}
          onRetry={() => void install.retry()}
          onDismiss={install.view.stage === 'failed' ? install.dismiss : undefined}
        />
      )}
      {install.view?.stage === 'failed' && !install.view.problem && (
        <AgentFixButton
          request={`Restore local dictation readiness after the selected model install failed: ${install.view.detail ?? install.view.reason ?? ''}`}
        />
      )}
      {failed && (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-destructive">
          <span className="min-w-0 flex-1">
            {t('asr_missing.install_failed', { message: failed })}
          </span>
          <AgentFixButton
            request={`Restore local dictation readiness after the selected model install failed: ${failed}`}
          />
        </div>
      )}
      {others.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {t('asr_missing.other_models')}
          </summary>
          <div className="mt-2 grid gap-2 @2xl:grid-cols-2">{others.map(choice)}</div>
        </details>
      )}
    </div>
  );
}
