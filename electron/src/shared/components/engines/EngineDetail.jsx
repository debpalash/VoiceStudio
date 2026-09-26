import React from 'react';
import { Mic, Activity, RefreshCw, Volume2, Copy, Check } from 'lucide-react';
import { useAppStore } from '../../store';
import { openExternal } from '../../api/external';
import { Badge, Button, Select } from '../../ui';
import { cn } from '@/lib/utils';
import EngineMark from '../EngineMark';
import EngineWeights from './EngineWeights';
import {
  LABEL,
  LICENSE_DIALOGS,
  chipCls,
  deviceLabel,
  fmtDiskBytes,
  fmtDuration,
  reasonMentionsLicense,
  statusOf,
} from './engineDisplay';

/** The detail panel for one engine: status, compatibility, probes, install. */
export default function EngineDetail({
  b,
  family,
  isActive,
  inv,
  onSelect,
  t,
  i18n,
  diskOpen,
  onToggleDisk,
  weights = [],
  downloads = null,
}) {
  const health = inv.healthByEngine[b.id];
  const selfTest = inv.selfTestByEngine[b.id];
  const resident = inv.loadedByEngine[b.id] || null;
  const install = inv.installByEngine[b.id] || null;
  const installJob = install?.job || null;
  const installRunning = installJob?.state === 'running';
  const canSelfTest = family === 'tts' && b.available && b.isolation_mode !== 'subprocess';
  const hasDiskDetails = family === 'tts' && !!b.disk_usage;
  const status = statusOf(t, b, install);
  const setupSnippetBlock = b.setup_snippet ? (
    <div className="flex flex-col gap-[4px]" data-testid={`setup-snippet-${b.id}`}>
      <span className="text-[11px] text-muted-foreground">{t('engines.setupSnippetLabel')}</span>
      <div className="flex flex-wrap items-center gap-[6px]">
        <code className="break-all rounded bg-muted px-[6px] py-[2px] font-mono text-[11px] text-foreground">
          {b.setup_snippet}
        </code>
        <Button
          size="sm"
          variant="subtle"
          onClick={() => inv.copySetup(b.id, b.setup_snippet)}
          leading={inv.copiedId === b.id ? <Check size={11} /> : <Copy size={11} />}
          aria-label={t('engines.copySetup', { engine: b.display_name })}
        >
          {inv.copiedId === b.id ? t('engines.copied') : t('engines.copy')}
        </Button>
      </div>
    </div>
  ) : null;

  return (
    <aside
      id={`engine-detail-${b.id}`}
      data-testid={`engine-detail-${b.id}`}
      aria-label={t('engines.detailsFor', { engine: b.display_name })}
      className="flex min-w-0 flex-col gap-[14px] rounded-[14px] border border-border bg-card/40 p-[16px] text-sm"
    >
      <header className="flex min-w-0 items-start gap-[10px]">
        <EngineMark id={b.id} size={26} className="mt-[1px] shrink-0" />
        <div className="min-w-0 flex-1">
          <h3
            className="m-0 truncate text-[15px] font-semibold leading-[1.3] text-foreground"
            title={b.display_name}
          >
            {b.display_name}
          </h3>
          <div className="mt-[3px] flex flex-wrap items-center gap-[6px]">
            <code className="font-mono text-[11px] text-muted-foreground">{b.id}</code>
            {isActive && (
              <Badge tone="brand" size="xs">
                {t('engines.active')}
              </Badge>
            )}
            {resident && (
              <Badge tone="info" size="xs" title={t('engines.inMemoryTitle')}>
                {t('engines.inMemory')}
              </Badge>
            )}
            {b.isolation_mode === 'subprocess' && (
              <Badge tone="neutral" size="xs" title={t('engines.subprocessTitle')}>
                {t('engines.runsIsolated')}
              </Badge>
            )}
            {family === 'tts' && b.supports_cloning && (
              <Badge tone="neutral" size="xs" title={t('engines.cloneCapableTitle')}>
                <Mic size={10} /> {t('engines.cloneCapable')}
              </Badge>
            )}
          </div>
        </div>
      </header>

      {/* Status + what it runs on here */}
      <div className="flex flex-col gap-[6px]">
        <div className="flex flex-wrap items-center gap-[8px]">
          <span className={cn('text-xs font-medium', status.cls)}>{status.text}</span>
          {b.reason && !b.available && (
            <span className="text-xs text-warn" data-testid={`reason-${b.id}`}>
              {b.reason}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-[4px]">
          {b.routing_status === 'n/a' ? (
            <Badge tone="neutral" size="xs">
              {t('engines.routingRemote')}
            </Badge>
          ) : (
            b.gpu_compat.map((g) => {
              const isEffective =
                b.routing_status && b.routing_status !== 'unavailable' && g === b.effective_device;
              const label = deviceLabel(t, g);
              return (
                <span
                  key={g}
                  className={chipCls(g, isEffective)}
                  title={
                    isEffective ? t('engines.routingEffectiveChip', { device: label }) : undefined
                  }
                >
                  {label}
                </span>
              );
            })
          )}
        </div>
        {b.routing_reason &&
          b.available &&
          b.routing_status !== 'n/a' &&
          b.routing_status !== 'unavailable' && (
            <span
              className="text-[11px] leading-[1.4] text-muted-foreground"
              data-testid={`routing-reason-${b.id}`}
            >
              {b.routing_reason}
            </span>
          )}
        {b.available && b.hint && (
          <span
            className="text-[11px] leading-[1.4] text-muted-foreground"
            data-testid={`engine-hint-${b.id}`}
          >
            {b.hint}
          </span>
        )}
        {b.install_hint && b.install_hint !== b.reason && (
          <span className="text-[11px] leading-[1.4] text-muted-foreground">{b.install_hint}</span>
        )}
        {b.last_error && b.last_error !== b.reason && (
          <span className="text-[11px] text-destructive" data-testid="last-error">
            {t('engines.lastError', { error: b.last_error })}
          </span>
        )}
        {b.docs_url && (
          <button
            type="button"
            className="cursor-pointer self-start border-0 bg-transparent p-0 text-[11px] font-semibold text-accent hover:underline"
            data-testid={`engine-docs-${b.id}`}
            onClick={() => openExternal(b.docs_url)}
          >
            {t('common.learn_more', 'Learn more')}
          </button>
        )}
      </div>

      <EngineWeights engineId={b.id} models={weights} downloads={downloads} t={t} />

      {/* #981 — mlx-audio's curated-model picker */}
      {b.curated_models && b.curated_models.length > 0 && (
        <label className="flex flex-col gap-[4px]">
          <span className={LABEL}>{t('engines.curatedModelLabel')}</span>
          <Select
            size="sm"
            className="w-full"
            value={b.active_model_id || ''}
            disabled={!onSelect || !b.available}
            onChange={(e) => inv.changeModel(b.id, e.target.value)}
            aria-label={t('engines.curatedModelAria', { engine: b.display_name })}
            data-testid={`curated-model-select-${b.id}`}
          >
            {b.curated_models.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </Select>
        </label>
      )}

      {/* Probes + secondary actions */}
      <div className="flex flex-wrap items-center gap-[6px]">
        {b.available ? (
          <Button
            size="sm"
            variant="subtle"
            onClick={() => inv.testHealth(b.id)}
            disabled={!!health?.inflight}
            loading={!!health?.inflight}
            leading={!health?.inflight && <Activity size={11} />}
            aria-label={t('engines.ariaTest', { engine: b.display_name })}
          >
            {health?.inflight ? t('engines.testing') : t('engines.testEngine')}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="subtle"
            onClick={() => inv.testHealth(b.id)}
            disabled={!!health?.inflight}
            loading={!!health?.inflight}
            leading={!health?.inflight && <RefreshCw size={11} />}
            aria-label={t('engines.ariaRecheck', { engine: b.display_name })}
          >
            {health?.inflight ? t('engines.rechecking') : t('engines.recheck')}
          </Button>
        )}
        {health && !health.inflight && (
          <span
            className={cn('font-mono text-[11px]', health.ok ? 'text-success' : 'text-destructive')}
            data-testid={`health-result-${b.id}`}
            title={health.message}
          >
            {health.ok
              ? b.isolation_mode === 'subprocess'
                ? t('engines.latencyMs', { ms: health.latency_ms })
                : t('engines.depsOk')
              : t('engines.failed')}
          </span>
        )}
        {canSelfTest && (
          <Button
            size="sm"
            variant="subtle"
            onClick={() => inv.runSelfTest(b.id)}
            disabled={!!selfTest?.inflight}
            loading={!!selfTest?.inflight}
            leading={!selfTest?.inflight && <Volume2 size={11} />}
            aria-label={t('engines.ariaSelfTest', { engine: b.display_name })}
          >
            {selfTest?.inflight ? t('engines.selfTesting') : t('engines.selfTest')}
          </Button>
        )}
        {canSelfTest && selfTest && !selfTest.inflight && (
          <span
            className={cn(
              'font-mono text-[11px]',
              selfTest.ok ? 'text-success' : 'text-destructive',
            )}
            data-testid={`selftest-result-${b.id}`}
            title={selfTest.message}
          >
            {selfTest.ok
              ? t('engines.selfTestOk', {
                  seconds: Number(selfTest.audio_seconds ?? 0).toFixed(2),
                  khz: selfTest.sample_rate ? Math.round(selfTest.sample_rate / 1000) : '?',
                  took: fmtDuration(selfTest.duration_ms),
                })
              : selfTest.timed_out
                ? t('engines.selfTestTimedOut')
                : t('engines.selfTestFailed')}
          </span>
        )}
        {resident?.unloadable && (
          <Button
            size="sm"
            variant="subtle"
            onClick={() => inv.unloadEngine(b.id)}
            disabled={inv.unloadingId === b.id}
            loading={inv.unloadingId === b.id}
            title={t('engines.inMemoryTitle')}
            aria-label={t('engines.ariaUnload', { engine: b.display_name })}
          >
            {inv.unloadingId === b.id ? t('engines.unloading') : t('engines.unload')}
          </Button>
        )}
        {family === 'llm' && b.id === 'openai-compat' && (
          <Button
            size="sm"
            variant="subtle"
            onClick={() => useAppStore.getState().openSettingsTab?.('llm-providers')}
            aria-label={t('engines.configureProviders')}
            data-testid="configure-llm-providers"
          >
            {t('engines.configureProviders')}
          </Button>
        )}
        {!b.available && reasonMentionsLicense(b.reason) && LICENSE_DIALOGS[b.id] && (
          <Button
            size="sm"
            variant="subtle"
            onClick={() => inv.setLicenseDialogFor(b.id)}
            aria-label={t('engines.ariaAcceptLicense', { engine: b.display_name })}
          >
            {t('engines.acceptLicense')}
          </Button>
        )}
        {hasDiskDetails && (
          <Button size="sm" variant="ghost" onClick={onToggleDisk} aria-expanded={diskOpen}>
            {t('engines.diskDetails')}
          </Button>
        )}
      </div>

      {/* One-click install progress */}
      {b.one_click_install && installJob && (
        <div className="flex flex-col gap-[3px]" data-testid={`install-progress-${b.id}`}>
          <ul className="m-0 flex list-none flex-col gap-[1px] p-0 font-mono text-[11px]">
            {installJob.steps.map((s) => (
              <li
                key={s.id}
                className={cn(
                  s.state === 'error' && 'text-destructive',
                  s.state === 'done' && 'text-success',
                  (s.state === 'pending' || s.state === 'skipped') && 'text-muted-foreground',
                )}
                data-install-step={s.id}
                data-step-state={s.state}
              >
                {s.state === 'done'
                  ? '[x]'
                  : s.state === 'running'
                    ? '[>]'
                    : s.state === 'error'
                      ? '[!]'
                      : '[ ]'}{' '}
                {t(`engines.installStep_${s.id}`, { defaultValue: s.id })}
                {s.id === 'fetch_weights' &&
                  s.state === 'running' &&
                  installJob.weights_progress?.pct != null &&
                  ` — ${Math.round(installJob.weights_progress.pct * 100)}%`}
              </li>
            ))}
          </ul>
          {installRunning && installJob.log.length > 0 && (
            <code
              className="block max-w-full truncate font-mono text-[10px] text-muted-foreground"
              title={installJob.log.slice(-12).join('\n')}
            >
              {installJob.log[installJob.log.length - 1]}
            </code>
          )}
          {installJob.state === 'failed' && (
            <span className="block text-[11px] text-destructive">
              {installJob.error}
              {installJob.remediation ? ` — ${installJob.remediation}` : ''}
            </span>
          )}
          {installJob.state === 'succeeded' && (
            <span className="block text-[11px] text-success">{t('engines.installDone')}</span>
          )}
        </div>
      )}

      {b.local_install_required && !b.available && (
        <p className="text-xs text-muted-foreground">{t('engines.localInstallRequired')}</p>
      )}
      {/* Setup snippet: top-level on plain path-gated rows; a collapsed
          "Manual install" fallback on one-click rows (open when the install
          failed — the snippet IS the recovery path then). */}
      {setupSnippetBlock &&
        (b.one_click_install ? (
          <details
            data-testid={`manual-install-${b.id}`}
            {...(installJob?.state === 'failed' ? { open: true } : {})}
          >
            <summary className="cursor-pointer select-none text-[11px] text-muted-foreground">
              {t('engines.manualInstall')}
            </summary>
            <div className="mt-[6px]">{setupSnippetBlock}</div>
          </details>
        ) : (
          setupSnippetBlock
        ))}

      {hasDiskDetails && diskOpen && (
        <DiskUsage usage={inv.diskByEngine[b.id] || b.disk_usage} id={b.id} t={t} i18n={i18n} />
      )}
    </aside>
  );
}

function DiskUsage({ usage, id, t, i18n }) {
  const estimate = usage?.estimate || {};
  const actual = usage?.actual || {};
  const value = (bytes) => fmtDiskBytes(bytes, t('common.unknown'), i18n.resolvedLanguage);
  return (
    <div
      className="grid grid-cols-[max-content_1fr] gap-x-[12px] gap-y-[2px] text-[11px]"
      data-testid={`disk-usage-${id}`}
    >
      <span>{t('engines.diskModelDownload')}</span>
      <strong>{value(estimate.model_download_bytes)}</strong>
      <span>{t('engines.diskPackageDownload')}</span>
      <strong>{value(estimate.package_download_bytes)}</strong>
      <span>{t('engines.diskUniqueInstalled')}</span>
      <strong>{value(estimate.unique_installed_bytes)}</strong>
      <span>{t('engines.diskPotentiallyShared')}</span>
      <strong>{value(estimate.potentially_shared_bytes)}</strong>
      <span>{t('engines.diskTemporary')}</span>
      <strong>{value(estimate.temporary_free_bytes)}</strong>
      <span>{t('engines.diskEstimateConfidence')}</span>
      <strong>{t(`engines.diskConfidence_${estimate.confidence || 'unknown'}`)}</strong>
      <span>{t('engines.diskDestination')}</span>
      <strong>
        {estimate.destination && estimate.destination !== 'unknown'
          ? t(`engines.diskDestination_${estimate.destination}`)
          : t('common.unknown')}
        {estimate.destination_volume && estimate.destination_volume !== 'unknown' && (
          <code className="ml-[6px]">{estimate.destination_volume}</code>
        )}
      </strong>
      <span>{t('engines.diskActualModel')}</span>
      <strong>{value(actual.model_bytes)}</strong>
      <span>{t('engines.diskActualEnvironment')}</span>
      <strong>{value(actual.environment_bytes)}</strong>
      <span>{t('engines.diskActualCache')}</span>
      <strong>{value(actual.cache_bytes)}</strong>
      <span>{t('engines.diskActualTotal')}</span>
      <strong>{value(actual.total_owned_bytes)}</strong>
      <span>{t('engines.diskActualConfidence')}</span>
      <strong>{t(`engines.diskConfidence_${actual.confidence || 'unknown'}`)}</strong>
      {estimate.deduplication && (
        <span className="col-span-2 mt-[2px] text-muted-foreground">
          {t(`engines.diskDedup_${estimate.deduplication}`)}
        </span>
      )}
    </div>
  );
}
