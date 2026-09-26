import React, { useEffect } from 'react';
import { Check, Download, RefreshCw, Trash2, Wrench, X } from 'lucide-react';
import { useAppStore } from '../../store';
import { Badge, Button, Progress } from '../../ui';
import { cn } from '@/lib/utils';
import { fmtBytes } from '../settings/models/format';
import { describeProgress } from '../settings/models/progressText';

/** The catalogue weights an engine loads (models.yaml `engines:` mapping). */
export function weightsForEngine(models, engineId) {
  return (models || []).filter((m) => Array.isArray(m.engines) && m.engines.includes(engineId));
}

/** Which of an engine's weights is the dictation pick (rows carrying a
 *  `dictation_id`): the stored pref when it names one of them, else the
 *  curated row, else the first — the same fallback the quick-menu picker uses. */
export function dictationPick(rows, modelId) {
  const candidates = rows.filter((m) => m.dictation_id);
  if (candidates.length === 0) return null;
  return (
    candidates.find((m) => m.dictation_id === modelId) ||
    candidates.find((m) => m.curated) ||
    candidates[0]
  );
}

/** A row with a job in flight (download, cancel, removal, or a mutation
 *  that has not answered yet). Picking it again must not start a second
 *  install. */
export function rowWorking(rt) {
  return Boolean(rt?.rowBusy || rt?.isInstalling || rt?.isDeleting || rt?.showBar);
}

/**
 * EngineWeights — the downloadable weights of ONE engine, inside its detail
 * panel: one line per model (label, size, state, one action) with the
 * download progress or install error folded under the line while it lasts.
 *
 * This is the same install / cancel / remove / reinstall flow the model
 * store drives (useModelDownloads), so a download started here shows the
 * same progress everywhere. Weights that double as a *preference* — the
 * sherpa-onnx dictation models, which carry a `dictation_id` — add a
 * "Dictation" radio per row that writes the same `dictation.model_id` pref
 * the quick menu and Settings → Voice write, and starts the download when
 * the pick is not on disk yet.
 */
export default function EngineWeights({ engineId, models, downloads, t }) {
  const modelId = useAppStore((s) => s.dictationModelId);
  const setModelId = useAppStore((s) => s.setDictationModelId);
  const loadPrefs = useAppStore((s) => s.loadDictationPrefs);
  const dictationLoaded = useAppStore((s) => s.dictationLoaded);
  const rows = weightsForEngine(models, engineId);
  const hasDictation = rows.some((m) => m.dictation_id);
  useEffect(() => {
    if (hasDictation && !dictationLoaded) loadPrefs?.();
  }, [hasDictation, dictationLoaded, loadPrefs]);
  if (!downloads) return null;
  const picked = hasDictation ? dictationPick(rows, modelId) : null;
  const pickDictation = (m) => {
    if (m.dictation_id !== picked?.dictation_id) setModelId?.(m.dictation_id);
    // A row already downloading (or mid-mutation) keeps its one job.
    if (!m.installed && !rowWorking(downloads.getRowRuntime(m))) downloads.onInstall(m.repo_id);
  };
  return (
    <section className="flex flex-col gap-[4px]" data-testid={`engine-weights-${engineId}`}>
      <h4 className="m-0 font-mono text-[11px] font-medium uppercase tracking-[var(--chrome-label-track)] text-muted-foreground">
        {t('engines.weights')}
      </h4>
      {rows.length === 0 ? (
        <p
          className="m-0 text-[11px] leading-[1.5] text-muted-foreground"
          data-testid="engine-weights-none"
        >
          {t('engines.noWeights')}
        </p>
      ) : (
        <ul
          className="m-0 flex list-none flex-col p-0"
          role={hasDictation ? 'radiogroup' : undefined}
          aria-label={hasDictation ? t('engines.dictation_model') : undefined}
        >
          {rows.map((m) => (
            <WeightRow
              key={m.repo_id}
              m={m}
              downloads={downloads}
              t={t}
              dictation={
                m.dictation_id
                  ? {
                      picked: picked?.dictation_id === m.dictation_id,
                      pick: () => pickDictation(m),
                    }
                  : null
              }
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function WeightRow({ m, downloads, t, dictation }) {
  const rt = downloads.getRowRuntime(m);
  const resident = downloads.getResidency?.(m);
  const size = m.installed ? fmtBytes(m.size_on_disk_bytes) : `${m.size_gb} GB`;
  const quiet = !m.installed && !rt.showBar;
  return (
    <li
      className="flex flex-col gap-[4px] border-t border-border/70 py-[7px] first:border-0"
      data-testid={`weight-${m.repo_id}`}
      data-installed={m.installed ? 'true' : 'false'}
    >
      <div className="flex min-w-0 items-center gap-[8px]">
        {m.installed ? (
          <Check size={12} className="shrink-0 text-success" aria-label={t('models.installed')} />
        ) : (
          <span className="inline-block h-[12px] w-[12px] shrink-0" aria-hidden="true" />
        )}
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-[13px]',
            quiet ? 'text-muted-foreground' : 'text-foreground',
          )}
          title={m.note ? `${m.repo_id} · ${m.note}` : m.repo_id}
        >
          {m.label}
        </span>
        {m.required && (
          <Badge tone="brand" size="xs" className="shrink-0">
            {t('models.required_tag')}
          </Badge>
        )}
        {!m.required && m.curated && (
          <Badge
            tone="success"
            size="xs"
            className="shrink-0"
            title={t('models.recommended_title')}
          >
            {t('voicePanel.badge_recommended')}
          </Badge>
        )}
        {resident && (
          <Badge tone="info" size="xs" className="shrink-0" title={t('models.in_memory_title')}>
            {t('models.in_memory')}
          </Badge>
        )}
        {dictation && (
          // The preference lives on the row: which of these the live
          // dictation hotkey loads. Picking an undownloaded one also installs it.
          <button
            type="button"
            role="radio"
            aria-checked={dictation.picked}
            onClick={dictation.pick}
            disabled={rowWorking(rt)}
            title={t('engines.dictation_model_hint')}
            data-testid={`weight-dictation-${m.repo_id}`}
            className={cn(
              'inline-flex shrink-0 cursor-pointer items-center gap-[4px] rounded-[var(--chrome-radius-pill)] border px-[6px] py-px font-mono text-[10px] font-semibold uppercase tracking-[0.04em] transition-colors disabled:cursor-default disabled:opacity-60',
              dictation.picked
                ? 'border-primary/40 bg-primary/[0.12] text-primary'
                : 'border-border bg-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            <span
              aria-hidden="true"
              className={cn(
                'inline-block h-[6px] w-[6px] rounded-full',
                dictation.picked
                  ? 'bg-primary'
                  : 'bg-transparent shadow-[inset_0_0_0_1px_currentColor]',
              )}
            />
            {t('settings.dictation')}
          </button>
        )}
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
          {size}
        </span>
        <span className="inline-flex shrink-0 items-center gap-[2px]">
          {rt.isInstalling || (rt.showBar && !rt.isDeleting) ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => downloads.onCancel(m.repo_id)}
              leading={<X size={11} />}
              aria-label={`${t('models.cancel_btn')} ${m.label}`}
            >
              {rt.aggPct != null ? `${Math.round(rt.aggPct)}%` : t('models.cancel_btn')}
            </Button>
          ) : rt.isDeleting || rt.rowBusy ? (
            <span className="font-mono text-[11px] text-muted-foreground">
              {t('models.working')}
            </span>
          ) : m.installed ? (
            <>
              {resident?.unloadable && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => downloads.onUnload(m.repo_id)}
                  aria-label={t('models.unload_aria', { repoId: m.repo_id })}
                >
                  {t('models.unload_btn')}
                </Button>
              )}
              <Button
                variant="icon"
                iconSize="sm"
                onClick={() => downloads.onReinstall(m.repo_id)}
                title={t('models.reinstall_btn')}
                aria-label={`${t('models.reinstall_btn')} ${m.label}`}
              >
                <RefreshCw size={11} />
              </Button>
              <Button
                variant="icon"
                iconSize="sm"
                onClick={() => downloads.onDelete(m.repo_id)}
                title={t('models.delete_btn')}
                aria-label={`${t('models.delete_btn')} ${m.label}`}
              >
                <Trash2 size={11} />
              </Button>
            </>
          ) : rt.unsupported ? (
            <span className="font-mono text-[11px] text-muted-foreground">
              {(m.platforms || []).join(', ')}
            </span>
          ) : (
            <Button
              size="sm"
              variant="subtle"
              onClick={() => downloads.onInstall(m.repo_id)}
              leading={m.incomplete ? <Wrench size={11} /> : <Download size={11} />}
              title={m.incomplete ? t('models.repair_title') : undefined}
              aria-label={`${m.incomplete ? t('models.repair_btn') : t('models.install_btn')} ${m.label}`}
              data-testid={`weight-install-${m.repo_id}`}
            >
              {m.incomplete ? t('models.repair_btn') : t('models.install_btn')}
            </Button>
          )}
        </span>
      </div>
      {rt.showBar && (
        <div
          className="flex flex-col gap-[3px] pl-[20px]"
          data-testid={`weight-progress-${m.repo_id}`}
        >
          <Progress value={rt.aggPct} tone={rt.isDeleting ? 'warn' : 'brand'} size="xs" />
          <span className="font-mono text-[11px] leading-[1.4] tabular-nums text-muted-foreground">
            {describeProgress(rt, t, downloads.speedRef, m.repo_id)}
          </span>
        </div>
      )}
      {rt.phase === 'install_error' && rt.rs?.error && (
        <div
          className="flex flex-wrap items-start gap-[6px] pl-[20px] text-[11px] text-destructive"
          role="alert"
        >
          <span className="min-w-0 flex-1">
            {t('models.install_error', { error: rt.rs.error })}
          </span>
          <span className="inline-flex shrink-0 items-center gap-[4px]">
            <Button
              size="sm"
              variant="subtle"
              onClick={() => downloads.onInstall(m.repo_id)}
              leading={<RefreshCw size={10} />}
            >
              {t('models.retry_btn')}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => downloads.onDismissError(m.repo_id)}>
              {t('common.dismiss')}
            </Button>
          </span>
        </div>
      )}
    </li>
  );
}
