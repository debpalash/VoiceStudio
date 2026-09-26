import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useAppStore } from '../store';
import { Button } from '../ui';
import { askConfirm } from '../utils/dialog';
import { clearLongformProjects } from '../utils/longformPersistence';
import { reloadAfterApplicationPersistence } from '../utils/persistenceLifecycle';

/** Keep project editors unmounted until IndexedDB has been read successfully. */
export default function LongformPersistenceGate({
  children,
  clearProjects = clearLongformProjects,
  confirm = askConfirm,
  reload = reloadAfterApplicationPersistence,
}) {
  const { t } = useTranslation();
  const unavailable = useAppStore((state) => state.longformPersistenceError);
  const [retrying, setRetrying] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearFailed, setClearFailed] = useState(false);

  if (!unavailable) return children;

  const retry = async () => {
    setRetrying(true);
    try {
      await useAppStore.persist.rehydrate();
    } finally {
      setRetrying(false);
    }
  };

  const clearAndReload = async () => {
    const accepted = await confirm(
      t('longformPersistence.clear_confirm'),
      t('settings.reset_confirm_title'),
    );
    if (!accepted) return;
    setClearing(true);
    setClearFailed(false);
    try {
      await clearProjects();
      await reload();
    } catch {
      setClearFailed(true);
      setClearing(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <section
        className="w-full max-w-lg rounded-xl border border-border bg-card p-6 text-card-foreground shadow-lg"
        role="alert"
        data-testid="longform-persistence-gate"
      >
        <AlertTriangle className="mb-4 text-warning" aria-hidden="true" />
        <h1 className="text-lg font-semibold">
          {t('longformPersistence.storage_unavailable_title')}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {t('longformPersistence.storage_unavailable_body')}
        </p>
        {clearFailed && (
          <p className="mt-3 text-sm text-danger" role="alert">
            {t('settings.reset_failed', { message: t('bootstrap.unknown_error') })}
          </p>
        )}
        <div className="mt-5 flex flex-wrap gap-3">
          <Button variant="primary" loading={retrying} disabled={clearing} onClick={retry}>
            {retrying ? t('bootstrap.retrying') : t('bootstrap.retry')}
          </Button>
          <Button variant="danger" loading={clearing} disabled={retrying} onClick={clearAndReload}>
            {t('longformPersistence.clear_projects')}
          </Button>
        </div>
      </section>
    </main>
  );
}
