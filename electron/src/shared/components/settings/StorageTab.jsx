/**
 * Settings → Storage (System group).
 *
 * Shows where VoiceStudio keeps its data and outputs (read-only, from systemInfo,
 * each with an Open-folder affordance via the /export/reveal endpoint), then the
 * two destructive affordances, in escalating order:
 *
 *   ResetPanel     — scoped reset. Anything from "forget my theme" to "back to a
 *                    fresh install", per-scope, with real sizes. Leaves a working
 *                    app behind: the shell restarts the backend afterwards.
 *   UninstallPanel — the door out (#1089). Deletes everything including the
 *                    managed Python environment, then quits.
 *
 * NOTE: the models *cache* directory lives in the Models category (StoragePanel).
 */
import React from 'react';
import { FolderOpen, HardDrive, Database, FolderOutput, FileText } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { useSystemInfo } from '../../api/hooks';
import { exportReveal } from '../../api/exports';
import { Button } from '../../ui';
import { SettingsSection } from './primitives';
import HistoryRetentionPanel from './HistoryRetentionPanel';
import ResetPanel from './ResetPanel';
import UninstallPanel from './UninstallPanel';

export default function StorageTab() {
  const { t } = useTranslation();
  const { data: info } = useSystemInfo();

  const openFolder = async (path) => {
    try {
      await exportReveal({ path });
    } catch (e) {
      toast.error(
        e?.message || t('settings.open_folder_failed', { defaultValue: 'Could not open folder' }),
      );
    }
  };

  const pathCard = (label, path, testId, Icon) => (
    <div className="flex min-w-0 flex-col gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-bg)] p-[var(--space-4)]">
      <div className="flex items-center gap-[var(--space-3)]">
        <span className="inline-flex h-[28px] w-[28px] shrink-0 items-center justify-center rounded-[var(--chrome-radius-pill)] bg-[color-mix(in_srgb,var(--chrome-accent)_10%,transparent)] text-[var(--chrome-accent)]">
          <Icon size={14} aria-hidden="true" />
        </span>
        <span className="text-[length:var(--text-sm)] font-medium text-[var(--chrome-fg)]">
          {label}
        </span>
      </div>
      <code className="min-h-[2.8em] break-words text-[length:var(--text-xs)] leading-[1.4] text-[var(--chrome-fg-dim)]">
        {path || '—'}
      </code>
      {path && (
        <Button
          variant="ghost"
          size="sm"
          className="self-start"
          leading={<FolderOpen size={12} />}
          onClick={() => openFolder(path)}
          title={path}
          data-testid={testId}
        >
          {t('settings.storage_open_folder', { defaultValue: 'Open folder' })}
        </Button>
      )}
    </div>
  );

  return (
    <>
      <SettingsSection
        icon={HardDrive}
        title={t('settings.storage', { defaultValue: 'Storage' })}
        description={t('settings.storage_desc', {
          defaultValue: 'Where VoiceStudio keeps your data and outputs.',
        })}
      >
        <div className="grid grid-cols-1 gap-[var(--space-3)] @min-[560px]/settings:grid-cols-3">
          {pathCard(
            t('settings.data_dir_at', { defaultValue: 'App data stored at' }),
            info?.data_dir ? `${info.data_dir}/` : '',
            'storage-open-data-dir',
            Database,
          )}
          {pathCard(
            t('privacy.outputs_at'),
            info?.outputs_dir || '',
            'storage-open-outputs-dir',
            FolderOutput,
          )}
          {pathCard(
            t('about.crash_log'),
            info?.crash_log_path || '',
            'storage-open-crash-log',
            FileText,
          )}
        </div>
      </SettingsSection>

      <HistoryRetentionPanel />

      {/* Scoped reset: preferences → settings → assets → everything. */}
      <ResetPanel />

      {/* The door out (#1089): everything, including the Python env, then quit. */}
      <UninstallPanel />
    </>
  );
}
