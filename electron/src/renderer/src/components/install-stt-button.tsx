import { useState } from 'react';
import { DownloadIcon, LoaderCircleIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { InstallProgress } from '@/components/model-install-progress';
import { useModelInstall } from '@/hooks/use-model-install';
import { apiJson, describeError } from '@/lib/api/client';

interface RecommendedModel {
  repo_id: string;
  role: string;
  installed: boolean;
}

/**
 * Offered beside the "reference transcription needs a speech-to-text model"
 * error so the fix is one click instead of a hunt through the Model Catalogue
 * (#2929, #2678). Starts the download of the first speech-to-text model the
 * host's curated preset lists and follows it inline; with nothing left to
 * install it opens the speech-to-text models, where one can be selected.
 */
export function InstallSttButton() {
  const { t } = useTranslation();
  const [resolving, setResolving] = useState(false);
  const install = useModelInstall(undefined, {
    onReady: () => toast.success(t('modelInstall.ready')),
  });
  const begin = async () => {
    setResolving(true);
    try {
      const { models } = await apiJson<{ models: RecommendedModel[] }>('/setup/recommendations');
      const missing = models.find(
        (model) => model.role.toLowerCase() === 'asr' && !model.installed,
      );
      if (!missing) {
        const { router } = await import('@/router');
        await router.navigate({ to: '/settings/models/$family', params: { family: 'asr' } });
        return;
      }
      if ((await install.start(missing.repo_id)) === 'started')
        toast.success(t('models.started_downloading', { count: 1 }));
    } catch (error) {
      toast.error(describeError(error));
    } finally {
      setResolving(false);
    }
  };
  const view = install.view;
  if (view && view.stage !== 'ready' && view.stage !== 'cancelled') {
    return (
      <InstallProgress
        className="min-w-48 flex-1"
        view={view}
        busy={install.busy}
        onCancel={() => void install.cancel()}
        onRetry={() => void install.retry()}
        onDismiss={view.stage === 'failed' ? install.dismiss : undefined}
      />
    );
  }
  return (
    <Button
      type="button"
      size="sm"
      disabled={resolving || install.busy}
      onClick={() => void begin()}
    >
      {resolving ? (
        <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
      ) : (
        <DownloadIcon />
      )}
      {t('tts_errors.install_speech_model')}
    </Button>
  );
}
