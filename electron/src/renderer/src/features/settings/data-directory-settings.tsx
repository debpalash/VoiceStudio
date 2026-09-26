import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ArrowRightIcon, DatabaseIcon, FolderOpenIcon, LoaderCircleIcon } from 'lucide-react';
import { toast } from 'sonner';
import { getBridge } from '@/components/bridge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { apiJson, describeError } from '@/lib/api/client';
import type { DataDirectorySelection, DataRelocationStage } from '../../../../preload/index.d';
import { fmtBytes } from '@shared/components/settings/models/format';
import { SettingsRow, SettingsSection } from './settings-layout';

function relocationError(t: (key: string) => string, error: unknown): string {
  const detail = describeError(error);
  for (const code of [
    'target_not_empty',
    'nested_path',
    'insufficient_space',
    'local_only',
    'backend_not_managed',
    'authorization_expired',
    'backend_verification_failed',
  ]) {
    if (detail.includes(code)) return t(`settings.data_move_error_${code}`);
  }
  return t('settings.data_move_error');
}

export function DataDirectorySettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const bridge = getBridge();
  const [selection, setSelection] = useState<DataDirectorySelection | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<DataRelocationStage | null>(null);

  useEffect(() => bridge?.maintenance.onRelocationProgress(setStage), [bridge]);

  const choose = async () => {
    if (!bridge || busy) return;
    try {
      const value = await bridge.maintenance.chooseDataDirectory();
      if (value) setSelection(value);
    } catch (error) {
      toast.error(relocationError(t, error));
    }
  };

  const move = async () => {
    if (!bridge || !selection || busy) return;
    setBusy(true);
    setStage('stopping');
    try {
      const result = await bridge.maintenance.relocateDataDirectory(selection.authorization);
      setSelection(null);
      await Promise.all([
        client.invalidateQueries({ queryKey: ['storage-report'] }),
        client.invalidateQueries({ queryKey: ['system-info'] }),
      ]);
      toast.success(t('settings.data_move_done', { path: result.path }));
      if (!result.removed_source) toast.warning(t('settings.data_move_old_copy'));
    } catch (error) {
      toast.error(relocationError(t, error));
    } finally {
      setBusy(false);
      setStage(null);
    }
  };

  return (
    <>
      <SettingsSection icon={DatabaseIcon} title={t('settings.data_directory')}>
        <SettingsRow
          id="data-directory"
          title={t('settings.data_directory')}
          description={t('settings.data_directory_desc')}
        >
          <DataDirectoryPath />
          {bridge && (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void choose()}>
              <FolderOpenIcon />
              {t('settings.data_move_choose')}
            </Button>
          )}
        </SettingsRow>
      </SettingsSection>

      <Dialog
        open={Boolean(selection)}
        onOpenChange={(open) => !open && !busy && setSelection(null)}
      >
        <DialogContent showCloseButton={!busy} className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('settings.data_move_title')}</DialogTitle>
            <DialogDescription>{t('settings.data_move_desc')}</DialogDescription>
          </DialogHeader>
          {selection && (
            <div className="space-y-3 rounded-xl border border-border/60 bg-muted/25 p-3.5">
              <div className="grid min-w-0 grid-cols-[1fr_auto_1fr] items-center gap-2">
                <code className="truncate text-xs text-muted-foreground" title={selection.source}>
                  {selection.source}
                </code>
                <ArrowRightIcon className="size-4 text-muted-foreground" />
                <code className="truncate text-xs" title={selection.target}>
                  {selection.target}
                </code>
              </div>
              <p className="text-xs text-muted-foreground">
                {t('settings.data_move_summary', {
                  size: fmtBytes(selection.size_bytes),
                  count: selection.file_count,
                })}
              </p>
            </div>
          )}
          {busy && stage && (
            <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircleIcon className="size-4 animate-spin" />
              {t(`settings.data_move_stage_${stage}`)}
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" disabled={busy} onClick={() => setSelection(null)}>
              {t('common.cancel')}
            </Button>
            <Button disabled={busy} onClick={() => void move()}>
              {busy ? <LoaderCircleIcon className="animate-spin" /> : <DatabaseIcon />}
              {busy ? t('settings.data_move_working') : t('settings.data_move_confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function DataDirectoryPath() {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ['system-info'],
    queryFn: ({ signal }) => apiJson<{ data_dir?: string }>('/system/info', { signal }),
  });
  const path = query.data?.data_dir || '';
  return (
    <code
      title={path}
      className="min-w-0 max-w-full flex-1 truncate rounded-md border border-border/60 bg-muted/35 px-2.5 py-1.5 text-xs text-muted-foreground @2xl:max-w-[32rem]"
    >
      {path || t('common.loading')}
    </code>
  );
}
