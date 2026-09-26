import React from 'react';
import { RefreshCw, Trash2, ExternalLink, Download, X, Wrench } from 'lucide-react';
import { openExternal } from '../../../api/external';
import { Button, Badge, Progress } from '../../../ui';
import { fmtBytes, orgColor } from './format';
import { describeProgress } from './progressText';

/**
 * Build the TanStack column definitions for the model store table.
 *
 * The column cells close over runtime values, so this is a factory rather than
 * a static array: pass in the live callbacks/refs from the host component and
 * memoize the result with the same dependency array the inline definition used.
 */
export function makeModelColumns({
  t,
  getRowRuntime,
  speedRef,
  MODEL_ROLE_LABEL,
  onInstall,
  onDelete,
  onReinstall,
  onCancel,
  onDismissError,
  // Residency (optional): repo_id → its /model/loaded entry when that model
  // is resident in memory right now, else null/undefined. Legacy callers that
  // don't pass it render exactly as before.
  getResidency,
  onUnload,
}) {
  return [
    {
      id: 'name',
      accessorFn: (m) => `${m.label || ''} ${m.repo_id || ''}`,
      header: t('models.column_model'),
      size: 260,
      meta: { className: 'models-row__name' },
      cell: ({ row }) => {
        const m = row.original;
        const rt = getRowRuntime(m);
        return (
          <>
            {/* Keep the idle state to one scan-friendly line. The full label,
                repository and optional note remain available on hover. */}
            <span className="models-row__title" title={m.label}>
              <span
                className="models-row__avatar"
                style={{ background: orgColor(m.repo_id) }}
                title={m.repo_id.split('/')[0]}
              >
                {m.repo_id.split('/')[0].slice(0, 2).toUpperCase()}
              </span>
              <span className="models-row__label">{m.label}</span>
              {m.required && <span className="models-row__tag">{t('models.required_tag')}</span>}
              {/* Curated "best for your system" pick (curated_on in models.yaml).
                  Required rows already carry the stronger "required" tag. */}
              {!m.required && m.curated && (
                <span
                  className="models-row__tag models-row__tag--rec"
                  title={t('models.recommended_title')}
                  data-testid={`model-recommended-${m.repo_id}`}
                >
                  {t('voicePanel.badge_recommended')}
                </span>
              )}
            </span>
            <code
              className="models-row__repo"
              title={m.note ? `${m.repo_id} · ${m.note}` : m.repo_id}
              translate="no"
            >
              {m.repo_id}
            </code>
            {rt.showBar && (
              <div className="models-row__progressline flex flex-col gap-[3px] mt-[6px]">
                <Progress value={rt.aggPct} tone={rt.isDeleting ? 'warn' : 'brand'} size="xs" />
                <span className="models-row__progresstext">
                  {describeProgress(rt, t, speedRef, m.repo_id)}
                </span>
              </div>
            )}
            {rt.phase === 'install_error' && rt.rs?.error && (
              <div className="models-row__error flex flex-wrap items-start gap-[6px] mt-[6px]">
                <span className="models-row__error-text min-w-0 flex-1">
                  {t('models.install_error', { error: rt.rs.error })}
                </span>
                <span className="models-row__error-actions inline-flex shrink-0 items-center gap-[4px]">
                  <Button
                    size="sm"
                    variant="subtle"
                    onClick={() => onInstall?.(m.repo_id)}
                    leading={<RefreshCw size={10} />}
                  >
                    {t('models.retry_btn')}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onDismissError?.(m.repo_id)}>
                    {t('common.dismiss')}
                  </Button>
                </span>
              </div>
            )}
          </>
        );
      },
    },
    {
      id: 'role',
      accessorFn: (m) => (m.role || 'other').toLowerCase(),
      header: t('models.column_role'),
      size: 58,
      filterFn: (row, id, value) => !value || row.getValue(id) === value,
      cell: ({ row }) => (
        <span className="models-row__role">
          {MODEL_ROLE_LABEL[row.getValue('role')] || row.original.role || 'Other'}
        </span>
      ),
    },
    {
      id: 'size',
      accessorFn: (m) => (m.installed ? m.size_on_disk_bytes || 0 : (m.size_gb || 0) * 1024 ** 3),
      header: t('models.column_size'),
      size: 68,
      meta: { align: 'right', className: 'models-row__size' },
      cell: ({ row }) => {
        const m = row.original;
        const rt = getRowRuntime(m);
        // During active download, show live downloaded / total
        if (rt.showBar && rt.hasFiles && rt.totals.total > 0) {
          return (
            <span className="models-row__size-live">
              {fmtBytes(rt.totals.downloaded)}
              <span className="models-row__size-sep">/</span>
              {fmtBytes(rt.totals.total)}
            </span>
          );
        }
        return m.installed ? fmtBytes(m.size_on_disk_bytes) : `${m.size_gb} GB`;
      },
    },
    {
      id: 'status',
      accessorFn: (m) => (m.installed ? 2 : m.supported === false ? 0 : 1),
      header: t('models.column_status'),
      size: 96,
      meta: { align: 'center', className: 'models-row__status' },
      cell: ({ row }) => {
        const m = row.original;
        const rt = getRowRuntime(m);
        return rt.isInstalling ? (
          <Badge tone="warn" size="xs">
            <Download size={10} />{' '}
            {rt.aggPct != null ? `${Math.round(rt.aggPct)}%` : t('models.downloading')}
          </Badge>
        ) : rt.isDeleting ? (
          <Badge tone="warn" size="xs">
            <Trash2 size={10} /> {t('models.deleting')}
          </Badge>
        ) : rt.rowBusy ? (
          <Badge tone="warn" size="xs">
            <RefreshCw size={10} className="spinner" /> {t('models.working')}
          </Badge>
        ) : m.installed ? (
          getResidency?.(m) ? (
            // Installed AND resident in memory right now (/model/loaded
            // checkpoint match). Named so users know unloading is safe.
            <span className="inline-flex flex-col items-center gap-[3px]">
              <Badge tone="success" size="xs">
                {t('models.installed')}
              </Badge>
              <Badge
                tone="info"
                size="xs"
                title={t('models.in_memory_title')}
                data-testid={`model-resident-${m.repo_id}`}
              >
                {t('models.in_memory')}
              </Badge>
            </span>
          ) : (
            <Badge tone="success" size="xs">
              {t('models.installed')}
            </Badge>
          )
        ) : m.incomplete ? (
          // A truncated download (backend `incomplete`): config/tokenizer landed
          // but the weight shard didn't, so it still occupies disk yet can't be
          // used. Surface it as its own state — with the partial size — instead
          // of reading as a plain "not installed", so the user knows to repair it
          // rather than wonder why bytes are used (#622 UX follow-up).
          <Badge tone="warn" size="xs" title={t('models.incomplete_title')}>
            {m.size_on_disk_bytes > 0
              ? `${t('models.incomplete')} · ${fmtBytes(m.size_on_disk_bytes)}`
              : t('models.incomplete')}
          </Badge>
        ) : rt.unsupported ? (
          <Badge tone="neutral" size="xs">
            {(m.platforms || []).join(', ')}
          </Badge>
        ) : (
          <Badge tone="neutral" size="xs">
            {t('models.not_installed')}
          </Badge>
        );
      },
    },
    {
      id: 'actions',
      header: '',
      size: 90,
      enableSorting: false,
      meta: { align: 'right', className: 'models-row__actions' },
      cell: ({ row }) => {
        const m = row.original;
        const rt = getRowRuntime(m);
        return (
          <>
            <Button
              variant="icon"
              iconSize="sm"
              onClick={() => openExternal(`https://huggingface.co/${m.repo_id}`)}
              title={t('models.view_on_hf')}
              aria-label={t('models.view_on_hf')}
            >
              <ExternalLink size={11} />
            </Button>
            {!m.installed &&
              !rt.rowBusy &&
              !rt.isInstalling &&
              !rt.unsupported && (
                // For an `incomplete` (truncated) cache this is a repair, not a
                // fresh install: re-running snapshot_download completes the missing
                // weight shard without re-fetching the already-cached config files.
                <Button
                  variant="subtle"
                  size="sm"
                  onClick={() => onInstall(m.repo_id)}
                  leading={m.incomplete ? <Wrench size={11} /> : <Download size={11} />}
                  title={m.incomplete ? t('models.repair_title') : undefined}
                >
                  {m.incomplete ? t('models.repair_btn') : t('models.install_btn')}
                </Button>
              )}
            {/* Let the user clear a truncated download's partial bytes (it's not
                `installed`, so the delete control below never shows for it). */}
            {m.incomplete && !rt.rowBusy && !rt.isInstalling && !rt.isDeleting && (
              <Button
                variant="icon"
                iconSize="sm"
                onClick={() => onDelete(m.repo_id)}
                title={t('models.delete_btn')}
                aria-label={t('models.delete_btn')}
              >
                <Trash2 size={11} />
              </Button>
            )}
            {/* Cancel an in-flight install (P2-A / FDL-11) — shown for any
                in-progress phase (resolving / retry / downloading) but not
                while deleting. */}
            {rt.showBar && !rt.isDeleting && onCancel && (
              <Button
                variant="subtle"
                size="sm"
                onClick={() => onCancel(m.repo_id)}
                leading={<X size={11} />}
              >
                {t('models.cancel_btn')}
              </Button>
            )}
            {/* Free the memory this model occupies right now. Only when the
                /model/loaded entry says it's unloadable — it reloads lazily
                on next use, so this never loses data. */}
            {(() => {
              const resident = getResidency?.(m);
              return resident?.unloadable && !rt.rowBusy && !rt.isDeleting && onUnload ? (
                <Button
                  variant="subtle"
                  size="sm"
                  onClick={() => onUnload(m.repo_id)}
                  title={t('models.in_memory_title')}
                  aria-label={t('models.unload_aria', { repoId: m.repo_id })}
                >
                  {t('models.unload_btn')}
                </Button>
              ) : null;
            })()}
            {m.installed && !rt.rowBusy && !rt.isDeleting && (
              <>
                <Button
                  variant="icon"
                  iconSize="sm"
                  onClick={() => onReinstall(m.repo_id)}
                  title={t('models.reinstall_btn')}
                  aria-label={t('models.reinstall_btn')}
                >
                  <RefreshCw size={11} />
                </Button>
                <Button
                  variant="icon"
                  iconSize="sm"
                  onClick={() => onDelete(m.repo_id)}
                  title={t('models.delete_btn')}
                  aria-label={t('models.delete_btn')}
                >
                  <Trash2 size={11} />
                </Button>
              </>
            )}
          </>
        );
      },
    },
  ];
}
