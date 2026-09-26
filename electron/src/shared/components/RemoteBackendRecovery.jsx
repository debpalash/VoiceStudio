import { ServerOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '../ui';
import { disableRemoteBackend } from '../utils/remoteBackendProbe';
import { reloadAfterApplicationPersistence } from '../utils/persistenceLifecycle';

const DETAIL_KEYS = {
  tls: 'settings.remote_backend_error_tls',
  cors: 'settings.remote_backend_error_cors',
  network: 'settings.remote_backend_error_network',
  timeout: 'settings.remote_backend_error_timeout',
  http: 'settings.remote_backend_error_http',
  wrong_port: 'settings.remote_backend_error_wrong_port',
};

export default function RemoteBackendRecovery({
  failure,
  onRetry,
  onOpenSettings,
  reload = reloadAfterApplicationPersistence,
}) {
  const { t } = useTranslation();
  const detail = t(DETAIL_KEYS[failure.kind], { status: failure.status });
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4">
      <section className="w-full max-w-lg rounded-xl bg-bg-elev-1 p-6 shadow-xl" role="alert">
        <ServerOff size={28} aria-hidden="true" className="mb-3 text-danger" />
        <h1 className="m-0 text-lg font-semibold">{t('settings.remote_backend_recovery_title')}</h1>
        <p className="text-sm text-fg-muted">{detail}</p>
        <code className="block break-all text-xs text-fg-subtle">{failure.target}</code>
        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant="primary" onClick={onRetry}>
            {t('bootstrap.retry')}
          </Button>
          <Button variant="subtle" onClick={() => void disableRemoteBackend(reload)}>
            {t('settings.remote_backend_use_local')}
          </Button>
          <Button variant="ghost" onClick={onOpenSettings}>
            {t('nav.settings')}
          </Button>
        </div>
        <p className="mb-0 mt-4 text-xs text-fg-subtle">
          {t('settings.remote_backend_recovery_hint')}
        </p>
      </section>
    </main>
  );
}
