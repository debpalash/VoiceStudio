import { useStore } from '@tanstack/react-store';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { ConfirmDialog } from '@/features/clone/confirm-dialog';
import { restartConfirmation, restartIntoUpdate } from '@/lib/update-actions';

/** Asks before a restart-to-update stops running work; mounted once at the App root. */
export function UpdateRestartConfirm() {
  const { t } = useTranslation();
  const work = useStore(restartConfirmation, (value) => value);
  const close = () => restartConfirmation.setState(() => null);
  return (
    <ConfirmDialog
      open={work !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      title={t('update.restart_confirm_title')}
      description={
        work && work.count > 0
          ? t('update.restart_confirm', { count: work.count })
          : t('update.restart_confirm_unknown')
      }
      confirmLabel={t('update.restart_anyway')}
      cancelLabel={t('update.toast_later')}
      onConfirm={async () => {
        close();
        try {
          await restartIntoUpdate();
        } catch {
          toast.error(t('update.failed'));
        }
      }}
    />
  );
}
