import { useState } from 'react';
import { ArrowRightIcon, LoaderCircleIcon, MemoryStickIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  FAILURE_CATALOG,
  isFailureTopic,
  type FailureEntry,
  type FailureTarget,
} from '@shared/utils/failureCatalog';
import { apiJson, describeError } from '@/lib/api/client';
import { Button } from './ui/button';
import { InstallSttButton } from './install-stt-button';

// Loaded on click: a static import would pull the whole route tree into every
// page that can show a failure (the same pattern as model-install-progress).
async function openTarget(target: FailureTarget) {
  const { router } = await import('@/router');
  switch (target) {
    case 'models_asr':
      return router.navigate({ to: '/settings/models/$family', params: { family: 'asr' } });
    case 'models_tts':
      return router.navigate({ to: '/settings/models/$family', params: { family: 'tts' } });
    case 'models_diarisation':
      return router.navigate({
        to: '/settings/models/$family',
        params: { family: 'diarisation' },
      });
    case 'models':
      return router.navigate({ to: '/settings/models' });
    case 'storage':
      return router.navigate({ to: '/settings/storage' });
    case 'performance':
      return router.navigate({ to: '/settings/performance' });
    case 'media':
      return router.navigate({ to: '/settings/media' });
    case 'credentials':
      return router.navigate({ to: '/settings/credentials' });
    case 'logs':
      return router.navigate({ to: '/settings/logs' });
  }
}

/** Unloads every model and clears caches, the same as Settings → Performance. */
function FreeMemoryButton() {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const free = async () => {
    setBusy(true);
    try {
      const result = await apiJson<{
        ram_after: number;
        vram_after: number;
        unloaded_model: boolean;
      }>('/system/flush-memory?unload_model=true', { method: 'POST' });
      toast.success(
        t('app.toast_flushed', {
          ram: result.ram_after,
          vram: result.vram_after,
          unloaded: result.unloaded_model ? t('app.toast_model_unloaded') : '',
        }),
      );
    } catch (error) {
      toast.error(t('app.toast_flush_failed', { message: describeError(error) }));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button type="button" size="sm" disabled={busy} onClick={() => void free()}>
      {busy ? (
        <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
      ) : (
        <MemoryStickIcon />
      )}
      {t('failureAction.free_memory')}
    </Button>
  );
}

/** The primary fix for a diagnosed failure class, or nothing when the app has none. */
export function FailureAction({ topic }: { topic: string | null | undefined }) {
  const { t } = useTranslation();
  if (!isFailureTopic(topic)) return null;
  const entry: FailureEntry = FAILURE_CATALOG[topic];
  const action = entry.action;
  if (!action) return null;
  if (action.kind === 'install_stt') return <InstallSttButton />;
  if (action.kind === 'free_memory') return <FreeMemoryButton />;
  return (
    <Button type="button" size="sm" onClick={() => void openTarget(action.target)}>
      <ArrowRightIcon />
      {t(`failureAction.${action.target}`)}
    </Button>
  );
}
