/**
 * Settings → Storage → Disk usage panel.
 *
 * Renders GET /api/settings/storage: severity-ranked warning banners, an
 * overall gauge for the volume holding the app data, and per-category usage
 * rows (HF model cache with its largest models, app-data subtotals, engine
 * venvs, temp files) with proportion bars, "Open folder" (the existing
 * /export/reveal pattern), a Model Store jump for reclaiming model space,
 * and the existing clear-logs action on the logs row.
 *
 * A "critical" warning is also surfaced OUTSIDE Settings via the app-wide
 * react-hot-toast overlay — once per session (module flag + fixed toast id,
 * so re-mounts and refreshes never spam).
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  FolderOpen,
  HardDrive,
  Package,
  RefreshCw,
  Trash2,
  Database,
  Boxes,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { apiJson } from '../../api/client';
import { exportReveal } from '../../api/exports';
import { clearSystemLogs } from '../../api/system';
import { useAppStore } from '../../store';
import { fmtBytes } from './models/format';
import { askConfirm } from './native';
import { SettingsSection } from './primitives';

// Once-per-session guard for the out-of-Settings critical toast.
let criticalToastShown = false;
/** Testing hook — reset the once-per-session toast guard. */
export function _resetCriticalToastForTests() {
  criticalToastShown = false;
}

const CATEGORY_ORDER = ['hf_cache', 'data', 'engine_venvs', 'temp'];
const CATEGORY_ICONS = {
  hf_cache: Package,
  data: Database,
  engine_venvs: Boxes,
  temp: Trash2,
};

function warningText(t, w) {
  const vars = {
    path: w.path,
    free: w.free_gb,
    min: w.min_free_gb,
    percent: w.used_percent,
  };
  if (w.kind === 'low_disk' && w.severity === 'critical') {
    return t('settings.storage_warn_critical', {
      defaultValue:
        'Critically low disk space: {{free}} GB free on {{path}} — VoiceStudio needs at least {{min}} GB. Generation and model downloads may fail.',
      ...vars,
    });
  }
  if (w.kind === 'low_disk') {
    return t('settings.storage_warn_low', {
      defaultValue:
        'Low disk space: {{free}} GB free on {{path}} — getting close to the {{min}} GB minimum.',
      ...vars,
    });
  }
  if (w.kind === 'volume_pressure') {
    return t('settings.storage_warn_pressure', {
      defaultValue: 'The disk holding {{path}} is {{percent}}% full.',
      ...vars,
    });
  }
  return t('settings.storage_warn_unreadable', {
    defaultValue: 'Could not fully scan {{path}} — the size shown may be incomplete.',
    ...vars,
  });
}

function WarningBanner({ warning, text }) {
  const critical = warning.severity === 'critical';
  const tone = critical ? 'var(--chrome-severity-err)' : 'var(--chrome-severity-warn)';
  return (
    <div
      role="alert"
      data-testid={`storage-warning-${warning.kind}`}
      className="mb-[var(--space-3)] flex items-start gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] px-[var(--space-4)] py-[var(--space-3)] text-[length:var(--text-sm)]"
      style={{
        border: `1px solid color-mix(in srgb, ${tone} 35%, transparent)`,
        background: `color-mix(in srgb, ${tone} 12%, transparent)`,
        color: tone,
      }}
    >
      <AlertTriangle size={14} className="mt-[2px] shrink-0" aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
}

function ProportionBar({ value, max }) {
  const pct = max > 0 ? Math.max(value > 0 ? 1.5 : 0, (value / max) * 100) : 0;
  return (
    <div
      className="h-[4px] w-full overflow-hidden rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)]"
      aria-hidden="true"
    >
      <div
        className="h-full rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-accent)]"
        style={{ width: `${Math.min(100, pct)}%` }}
      />
    </div>
  );
}

function SmallButton({ children, onClick, title, testId, disabled }) {
  return (
    <button
      className="inline-flex flex-none cursor-pointer items-center gap-[4px] rounded-[var(--chrome-radius-pill)] [border:1px_solid_var(--chrome-border)] bg-transparent px-[var(--space-3)] py-[2px] font-sans text-[length:var(--text-xs)] text-[var(--chrome-fg-muted)] hover:enabled:bg-[var(--chrome-hover-bg)] hover:enabled:text-[var(--chrome-fg)] disabled:cursor-default disabled:opacity-50"
      onClick={onClick}
      title={title}
      data-testid={testId}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

export default function StorageUsagePanel() {
  const { t } = useTranslation();
  const openCatalogue = useAppStore((s) => s.openCatalogue);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(
    async (refresh) => {
      if (refresh) setRefreshing(true);
      setError(null);
      try {
        const d = await apiJson(`/api/settings/storage${refresh ? '?refresh=1' : ''}`);
        setData(d);
        const critical = (d?.warnings || []).find((w) => w.severity === 'critical');
        if (critical && !criticalToastShown) {
          criticalToastShown = true;
          // Fixed id: even if a second toast fires before the flag settles,
          // react-hot-toast dedupes it into one visible notification.
          toast.error(warningText(t, critical), { id: 'storage-critical', duration: 8000 });
        }
      } catch (e) {
        setError(
          e?.message ||
            t('settings.storage_load_failed', { defaultValue: 'Failed to load storage info' }),
        );
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [t],
  );

  useEffect(() => {
    load(false);
  }, [load]);

  const openFolder = async (path) => {
    try {
      await exportReveal({ path });
    } catch (e) {
      toast.error(
        e?.message || t('settings.open_folder_failed', { defaultValue: 'Could not open folder' }),
      );
    }
  };

  // Same confirm gate as Settings → Logs → Clear: this truncates the crash log
  // too (the primary bug-report artifact), so it must never be one stray click.
  const clearLogs = async () => {
    const ok = await askConfirm(
      t('settings.clear_backend_confirm', {
        defaultValue: 'Clear the backend runtime + crash logs? This cannot be undone.',
      }),
      t('settings.clear_backend_title', { defaultValue: 'Clear logs' }),
    );
    if (!ok) return;
    try {
      await clearSystemLogs();
      toast.success(t('settings.storage_logs_cleared', { defaultValue: 'Logs cleared' }));
      load(true);
    } catch (e) {
      toast.error(
        e?.message || t('settings.clear_backend_failed', { defaultValue: 'Failed to clear logs' }),
      );
    }
  };

  const clearTemp = async () => {
    const ok = await askConfirm(
      t('settings.storage_clear_temp_confirm', {
        defaultValue:
          "Delete VoiceStudio's temporary working files? Don't do this while a dub or batch job is running.",
      }),
      t('settings.storage_clear_temp', { defaultValue: 'Clear temp files' }),
    );
    if (!ok) return;
    try {
      const r = await apiJson('/api/settings/storage/temp/clear', { method: 'POST' });
      toast.success(
        t('settings.storage_temp_cleared', {
          defaultValue: 'Temporary files cleared — {{freed}} freed',
          freed: fmtBytes(r?.freed_bytes || 0),
        }),
      );
      load(true);
    } catch (e) {
      toast.error(
        e?.message ||
          t('settings.storage_clear_temp_failed', {
            defaultValue: 'Could not clear temporary files',
          }),
      );
    }
  };

  const categories = CATEGORY_ORDER.map((id) =>
    (data?.categories || []).find((c) => c.id === id),
  ).filter(Boolean);
  const maxCatBytes = Math.max(1, ...categories.map((c) => c.bytes || 0));
  const dataVolume =
    (data?.volumes || []).find((v) => (v.roots || []).includes('data')) || data?.volumes?.[0];

  const catLabel = (id) =>
    t(`settings.storage_cat_${id}`, {
      defaultValue:
        {
          hf_cache: 'Model cache',
          data: 'App data',
          engine_venvs: 'Engine environments',
          temp: 'Temporary files',
        }[id] || id,
    });
  const childLabel = (id) =>
    t(`settings.storage_child_${id}`, {
      defaultValue:
        {
          voices: 'Voices',
          outputs: 'Outputs',
          dub_jobs: 'Dub jobs',
          batch: 'Batch jobs',
          preview: 'Previews',
          database: 'Database',
          logs: 'Logs',
          other: 'Other',
        }[id] || id,
    });

  const partialNote = (cat) =>
    cat.complete === false ? (
      <span className="ml-[var(--space-2)] text-[length:var(--text-xs)] text-[var(--chrome-severity-warn)]">
        {t('settings.storage_partial', { defaultValue: 'partial — scan timed out' })}
      </span>
    ) : null;

  return (
    <SettingsSection
      className="storage-usage-panel"
      icon={HardDrive}
      title={t('settings.storage_usage', { defaultValue: 'Disk usage' })}
      description={t('settings.storage_usage_desc', {
        defaultValue: 'What VoiceStudio stores on this machine, and how much space is left.',
      })}
      actions={
        <SmallButton
          onClick={() => load(true)}
          title={t('settings.storage_refresh_hint', { defaultValue: 'Rescan disk usage' })}
          testId="storage-refresh"
          disabled={loading || refreshing}
        >
          <RefreshCw size={12} className={refreshing ? 'spinner' : undefined} />
          {t('settings.storage_refresh', { defaultValue: 'Refresh' })}
        </SmallButton>
      }
    >
      {(data?.warnings || []).map((w, i) => (
        <WarningBanner key={`${w.kind}-${w.path}-${i}`} warning={w} text={warningText(t, w)} />
      ))}

      {error && (
        <div
          className="mb-[var(--space-3)] text-[length:var(--text-base)] text-[var(--chrome-severity-err)]"
          role="alert"
        >
          {error}
        </div>
      )}

      {loading && (
        <div data-testid="storage-skeleton" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="mb-[var(--space-3)] h-[36px] animate-pulse rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)]"
            />
          ))}
        </div>
      )}

      {!loading && dataVolume && (
        <div
          className="mb-[var(--space-4)] rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-bg)] p-[var(--space-5)]"
          data-testid="storage-disk-gauge"
        >
          <div className="mb-[var(--space-4)] flex flex-wrap items-start justify-between gap-[var(--space-3)]">
            <span className="flex min-w-0 flex-col gap-[var(--space-1)] text-[length:var(--text-md)] font-semibold text-[var(--chrome-fg)]">
              {t('settings.storage_data_volume', { defaultValue: 'Data volume' })}
              <span className="truncate font-[family-name:var(--chrome-font-mono)] text-[length:var(--text-xs)] font-normal text-[var(--chrome-fg-dim)]">
                {dataVolume.path}
              </span>
            </span>
            <span className="rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] px-[var(--space-3)] py-[var(--space-2)] font-[family-name:var(--chrome-font-mono)] text-[length:var(--text-sm)] text-[var(--chrome-fg)] tabular-nums">
              {t('settings.storage_volume_free', {
                defaultValue: '{{free}} free of {{total}}',
                free: fmtBytes(dataVolume.free_bytes),
                total: fmtBytes(dataVolume.total_bytes),
              })}
            </span>
          </div>
          <div className="h-[8px] w-full overflow-hidden rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)]">
            <div
              className="h-full rounded-[var(--chrome-radius-pill)]"
              style={{
                width: `${Math.min(100, dataVolume.used_percent)}%`,
                background:
                  dataVolume.used_percent > 90
                    ? 'var(--chrome-severity-err)'
                    : dataVolume.used_percent > 75
                      ? 'var(--chrome-severity-warn)'
                      : 'var(--chrome-accent)',
              }}
            />
          </div>
        </div>
      )}

      {!loading && categories.length > 0 && (
        <div
          className="settings-list-scroll max-h-[clamp(320px,58dvh,620px)] overflow-y-auto overscroll-contain rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-bg)] p-[var(--space-2)] [scrollbar-gutter:stable]"
          data-testid="storage-category-scroll"
          tabIndex={0}
          aria-label={t('settings.storage_usage', { defaultValue: 'Disk usage' })}
        >
          {categories.map((cat) => {
            const CategoryIcon = CATEGORY_ICONS[cat.id] || HardDrive;
            return (
              <div
                key={cat.id}
                className="rounded-[var(--chrome-radius-pill)] border-b border-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] p-[var(--space-4)] last:border-b-0"
                data-testid={`storage-cat-${cat.id}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-[var(--space-3)]">
                  <span className="flex min-w-0 flex-1 items-center gap-[var(--space-3)] text-[length:var(--text-md)] font-medium text-[var(--chrome-fg)]">
                    <span className="inline-flex h-[28px] w-[28px] shrink-0 items-center justify-center rounded-[var(--chrome-radius-pill)] bg-[color-mix(in_srgb,var(--chrome-accent)_10%,transparent)] text-[var(--chrome-accent)]">
                      <CategoryIcon size={14} aria-hidden="true" />
                    </span>
                    <span>
                      {catLabel(cat.id)}
                      {partialNote(cat)}
                    </span>
                  </span>
                  <span className="ml-auto flex flex-wrap items-center justify-end gap-[var(--space-2)] @max-[520px]/settings:ml-0 @max-[520px]/settings:w-full @max-[520px]/settings:justify-start">
                    <span className="font-[family-name:var(--chrome-font-mono)] text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)] tabular-nums">
                      {fmtBytes(cat.bytes)}
                    </span>
                    {cat.id === 'hf_cache' && (
                      <SmallButton
                        onClick={() => openCatalogue('tts')}
                        title={t('settings.storage_manage_models_hint', {
                          defaultValue: 'Reclaim space by removing models in the Model Store',
                        })}
                        testId="storage-manage-models"
                      >
                        <Package size={12} />
                        {t('settings.storage_manage_models', { defaultValue: 'Manage models' })}
                      </SmallButton>
                    )}
                    {cat.id === 'temp' && (
                      <SmallButton
                        onClick={clearTemp}
                        title={t('settings.storage_clear_temp_hint', {
                          defaultValue: "Delete VoiceStudio's own files in the temp directory",
                        })}
                        testId="storage-clear-temp"
                        disabled={(cat.bytes || 0) === 0}
                      >
                        <Trash2 size={12} />
                        {t('settings.storage_clear_temp', { defaultValue: 'Clear temp files' })}
                      </SmallButton>
                    )}
                    {cat.exists && (
                      <SmallButton
                        onClick={() => openFolder(cat.path)}
                        title={cat.path}
                        testId={`storage-open-${cat.id}`}
                      >
                        <FolderOpen size={12} />
                        {t('settings.storage_open_folder', { defaultValue: 'Open folder' })}
                      </SmallButton>
                    )}
                  </span>
                </div>
                <div className="mt-[var(--space-2)]">
                  <ProportionBar value={cat.bytes || 0} max={maxCatBytes} />
                </div>
                <p className="mx-0 mb-0 mt-[2px] truncate font-[family-name:var(--chrome-font-mono)] text-[length:var(--text-xs)] text-[var(--chrome-fg-dim)]">
                  {cat.path}
                </p>

                {cat.id === 'hf_cache' && (cat.items || []).length > 0 && (
                  <ul className="m-0 mt-[var(--space-2)] list-none p-0">
                    <li className="pb-[2px] text-[length:var(--text-xs)] text-[var(--chrome-fg-dim)]">
                      {t('settings.storage_top_models', { defaultValue: 'Largest models' })}
                    </li>
                    {cat.items.map((m) => (
                      <li
                        key={m.name}
                        className="flex items-center justify-between gap-[var(--space-3)] py-[1px] pl-[var(--space-3)] text-[length:var(--text-xs)]"
                      >
                        <span className="min-w-0 truncate font-[family-name:var(--chrome-font-mono)] text-[var(--chrome-fg-muted)]">
                          {m.name}
                        </span>
                        <span className="flex-none font-[family-name:var(--chrome-font-mono)] text-[var(--chrome-fg-dim)] tabular-nums">
                          {fmtBytes(m.bytes)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                {cat.id === 'data' && (cat.children || []).length > 0 && (
                  <ul className="m-0 mt-[var(--space-2)] list-none p-0">
                    {cat.children.map((c) => (
                      <li
                        key={c.id}
                        className="flex items-center justify-between gap-[var(--space-3)] py-[1px] pl-[var(--space-3)] text-[length:var(--text-xs)]"
                      >
                        <span className="min-w-0 truncate text-[var(--chrome-fg-muted)]">
                          {childLabel(c.id)}
                        </span>
                        <span className="flex flex-none items-center gap-[var(--space-2)]">
                          <span className="font-[family-name:var(--chrome-font-mono)] text-[var(--chrome-fg-dim)] tabular-nums">
                            {fmtBytes(c.bytes)}
                          </span>
                          {c.id === 'logs' && (
                            <SmallButton
                              onClick={clearLogs}
                              title={t('settings.storage_clear_logs_hint', {
                                defaultValue: 'Truncate the backend runtime and crash logs',
                              })}
                              testId="storage-clear-logs"
                            >
                              <Trash2 size={12} />
                              {t('settings.storage_clear_logs', { defaultValue: 'Clear logs' })}
                            </SmallButton>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                {cat.id === 'engine_venvs' && (cat.items || []).length > 0 && (
                  <ul className="m-0 mt-[var(--space-2)] list-none p-0">
                    {cat.items.map((m) => (
                      <li
                        key={m.name}
                        className="flex items-center justify-between gap-[var(--space-3)] py-[1px] pl-[var(--space-3)] text-[length:var(--text-xs)]"
                      >
                        <span className="min-w-0 truncate font-[family-name:var(--chrome-font-mono)] text-[var(--chrome-fg-muted)]">
                          {m.name}
                        </span>
                        <span className="flex-none font-[family-name:var(--chrome-font-mono)] text-[var(--chrome-fg-dim)] tabular-nums">
                          {fmtBytes(m.bytes)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </SettingsSection>
  );
}
