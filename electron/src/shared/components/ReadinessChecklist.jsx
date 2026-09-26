import React from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle, AlertTriangle, XCircle, Loader, Search, Lightbulb } from 'lucide-react';
import { usePreflight, useModelStatus } from '../api/hooks';

/**
 * ReadinessChecklist — system readiness panel.
 *
 * Consumes the existing /setup/preflight endpoint (OS, RAM, GPU, ffmpeg,
 * yt-dlp, network) plus /model/status, and renders a compact pass/warn/fail
 * checklist. Mirrors into Settings and renders as empty-state on the
 * launchpad when no project is loaded.
 *
 * Hides itself when all gates are green (user doesn't need to see
 * "everything is fine" every time they open the app).
 */

const StatusIcon = ({ status, size = 14 }) => {
  switch (status) {
    case 'pass':
      return <CheckCircle size={size} />;
    case 'warn':
      return <AlertTriangle size={size} />;
    case 'fail':
      return <XCircle size={size} />;
    case 'loading':
      return <Loader size={size} />;
    default:
      return <Loader size={size} />;
  }
};

export default function ReadinessChecklist({ compact = false, showWhenAllPass = false }) {
  const { t } = useTranslation();
  const { data: preflight, isLoading: preflightLoading } = usePreflight();
  const { data: modelData, isLoading: modelLoading } = useModelStatus();

  const isLoading = preflightLoading || modelLoading;
  const modelStatus = modelData?.status ?? 'idle';
  const modelFailed = modelStatus === 'error' || ['error', 'failed'].includes(modelData?.sub_stage);

  // Build the checklist from preflight data + model status
  const checks = [];

  // Model readiness (from /model/status)
  const modelDetail = modelData?.detail || '';
  const modelErr = modelData?.error || null;
  const modelCheck = {
    id: 'tts-model',
    label: t('readiness.tts_model'),
    status:
      modelStatus === 'ready'
        ? 'pass'
        : modelStatus === 'loading'
          ? 'loading'
          : modelFailed
            ? 'fail'
            : 'warn',
    detail:
      modelStatus === 'ready'
        ? t('readiness.loaded_ready')
        : modelStatus === 'loading'
          ? modelDetail || t('readiness.loading_first_run')
          : modelFailed
            ? modelErr || t('readiness.failed_to_load')
            : t('readiness.tts_not_loaded_yet'),
    fix: modelFailed
      ? modelErr
        ? t('readiness.error_check_logs', { error: modelErr })
        : t('readiness.check_logs_restart')
      : null,
  };
  checks.push(modelCheck);

  // Add preflight checks
  if (preflight?.checks) {
    // Filter to the most relevant checks for the checklist
    const relevant = ['gpu', 'ffmpeg', 'yt-dlp', 'ram'];
    for (const check of preflight.checks) {
      if (relevant.includes(check.id)) {
        checks.push(check);
      }
    }
  }

  // LLM configuration (check for translate endpoint)
  const llmCheck = {
    id: 'llm',
    label: t('readiness.llm_cinematic'),
    status: 'warn',
    detail: t('readiness.llm_configure'),
    fix: t('readiness.llm_set_env'),
  };
  // If we have preflight and there's a network check passing, LLM is at least possible
  if (preflight?.checks) {
    const netCheck = preflight.checks.find((c) => c.id === 'network');
    if (netCheck?.status === 'pass') {
      llmCheck.detail = t('readiness.llm_optional');
    }
  }
  checks.push(llmCheck);

  // Determine if all critical checks pass
  const allPass = checks.every((c) => c.status === 'pass' || c.status === 'warn');
  const anyFail = checks.some((c) => c.status === 'fail');

  // Hide when everything is fine (unless explicitly asked to show)
  if (!showWhenAllPass && allPass && !isLoading) return null;

  if (isLoading) {
    return (
      <div className="readiness-checklist">
        <div className="flex items-center gap-[var(--space-3)] font-semibold [font-size:var(--text-md)] text-fg m-0 mb-[var(--space-2)]">
          <span className="[font-size:var(--text-lg)]">
            <Search size={14} />
          </span>
          {t('readiness.checking_system')}
        </div>
      </div>
    );
  }

  if (compact) {
    // Compact mode: just show failing/warning items
    const issues = checks.filter((c) => c.status !== 'pass');
    if (issues.length === 0) {
      return (
        <div className="flex items-center gap-[var(--space-3)] py-[var(--space-3)] px-[var(--space-4)] bg-[rgba(142,192,124,0.08)] border border-solid border-transparent rounded-md text-success font-medium [font-size:var(--text-sm)]">
          <CheckCircle size={14} />
          {t('readiness.all_ready')}
        </div>
      );
    }
    return (
      <div className="readiness-checklist">
        <ul className="list-none m-0 p-0 flex flex-col gap-[var(--space-2)]">
          {issues.map((check) => (
            <li
              key={check.id}
              className="flex items-start gap-[var(--space-3)] py-[var(--space-2)] px-[var(--space-3)] rounded-sm [transition:background_var(--dur-fast)_var(--ease-out)] hover:bg-bg-elev-3"
            >
              <span
                className={`shrink-0 w-[16px] h-[16px] flex items-center justify-center mt-[1px] readiness-checklist__status readiness-checklist__status--${check.status}`}
              >
                <StatusIcon status={check.status} />
              </span>
              <div>
                <div className="font-medium text-fg">{check.label}</div>
                {check.fix && (
                  <div className="[font-size:var(--text-xs)] text-accent mt-[2px]">{check.fix}</div>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="readiness-checklist">
      <div className="flex items-center gap-[var(--space-3)] font-semibold [font-size:var(--text-md)] text-fg m-0 mb-[var(--space-2)]">
        <span className="[font-size:var(--text-lg)]">
          {anyFail ? <AlertTriangle size={14} /> : <CheckCircle size={14} />}
        </span>
        {t('readiness.system_readiness')}
      </div>
      <ul className="list-none m-0 p-0 flex flex-col gap-[var(--space-2)]">
        {checks.map((check) => (
          <li
            key={check.id}
            className="flex items-start gap-[var(--space-3)] py-[var(--space-2)] px-[var(--space-3)] rounded-sm [transition:background_var(--dur-fast)_var(--ease-out)] hover:bg-bg-elev-3"
          >
            <span
              className={`shrink-0 w-[16px] h-[16px] flex items-center justify-center mt-[1px] readiness-checklist__status readiness-checklist__status--${check.status}`}
            >
              <StatusIcon status={check.status} />
            </span>
            <div>
              <div className="font-medium text-fg">{check.label}</div>
              <div className="[font-size:var(--text-xs)] text-fg-muted mt-[1px]">
                {check.detail}
              </div>
              {check.fix && (
                <div className="[font-size:var(--text-xs)] text-accent mt-[2px]">
                  <Lightbulb size={12} /> {check.fix}
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
