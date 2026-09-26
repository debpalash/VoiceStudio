import React from 'react';
import {
  Info,
  CheckCircle,
  AlertCircle,
  Download,
  Activity,
  Copy,
  ExternalLink,
  Building2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { openExternal } from '../../api/external';
import { resolveAboutVersion } from '../../utils/appVersion';
import { REPO_URL } from '../../utils/bugReport';
import { Button, Badge } from '../../ui';
import { SettingsSection } from './primitives';
import { CATEGORY_BY_ID } from './settingsCategories';
import { useAppStore } from '../../store';
import { isTauri } from './native';
import Row from './Row';
import VoiceStudioMark from '../brand/VoiceStudioMark';

/**
 * Where a failing self-check can be fixed inside the app — diagnose check id
 * (backend/core/diagnose.py) → Settings category id. Checks without an in-app
 * fix (python, backend, …) render their hint as plain text only.
 */
const CHECK_FIX_CATEGORY = {
  ffmpeg: 'network',
  hf_token: 'credentials',
  disk: 'storage',
  data_dir: 'storage',
  engines: 'engines',
  gpu_routing: 'engines',
  device: 'performance',
  ram: 'performance',
  deep_synth: 'logs',
};

/** Small "Open <category>" deep-link into the Settings hub. */
function OpenCategoryButton({ categoryId }) {
  const { t } = useTranslation();
  const cat = CATEGORY_BY_ID[categoryId];
  if (!cat) return null;
  return (
    <Button
      size="sm"
      variant="subtle"
      onClick={() => useAppStore.getState().openSettingsTab(categoryId)}
    >
      {t('about.open_fix_category', {
        defaultValue: 'Open {{category}}',
        category: t(cat.labelKey, { defaultValue: cat.defaultLabel }),
      })}
    </Button>
  );
}

/**
 * Settings → About.
 *
 * Identity + diagnostics only. The device / RAM / VRAM / backend readouts moved
 * to Performance & Device; the data/outputs paths moved to Storage; the update
 * channel + endpoint moved to Updates (the single update home). What remains is
 * app identity, the HF-token quick status, and the diagnostics actions.
 */
export default function AboutTab({
  appVersion,
  tauriVersion,
  info,
  checkForUpdates,
  updateState,
  selfCheck,
  selfCheckRunning,
  runSelfCheck,
  bundleBuilding,
  saveDiagnosticBundle,
  copyDiagnostics,
}) {
  const { t } = useTranslation();

  return (
    <SettingsSection icon={Info} title={t('settings.about')}>
      <Row
        label={t('about.app')}
        value={
          <span className="inline-flex items-center gap-[var(--space-2)]" translate="no">
            <VoiceStudioMark className="size-5 text-[var(--chrome-accent)]" />
            VoiceStudio
          </span>
        }
      />
      <Row label={t('about.version')} value={resolveAboutVersion(appVersion, info)} mono />
      <Row
        label={t('about.tauri_runtime')}
        value={tauriVersion || (isTauri() ? '—' : t('about.web_preview'))}
        mono
      />
      <Row
        label={t('about.hf_token')}
        value={
          info?.has_hf_token ? (
            t('about.yes')
          ) : (
            <span className="inline-flex flex-wrap items-center gap-[var(--space-3)]">
              {t('about.no')}
              <OpenCategoryButton categoryId="credentials" />
            </span>
          )
        }
      />

      <div className="settings-link-row mt-[var(--space-5)] flex flex-wrap gap-[var(--space-4)]">
        {isTauri() && (
          <Button
            variant="primary"
            size="md"
            leading={<Download size={12} />}
            onClick={checkForUpdates}
            loading={updateState === 'checking' || updateState === 'downloading'}
          >
            {updateState === 'downloading' ? t('about.downloading') : t('about.check_updates')}
          </Button>
        )}
        <Button
          variant="subtle"
          size="md"
          leading={!selfCheckRunning && <Activity size={12} />}
          onClick={runSelfCheck}
          loading={selfCheckRunning}
        >
          {t('about.self_check')}
        </Button>
        <Button
          variant="subtle"
          size="md"
          leading={!bundleBuilding && <Download size={12} />}
          onClick={saveDiagnosticBundle}
          loading={bundleBuilding}
        >
          {t('about.save_bundle')}
        </Button>
        <Button variant="subtle" size="md" leading={<Copy size={12} />} onClick={copyDiagnostics}>
          {t('about.copy_diagnostics')}
        </Button>
        <Button
          variant="subtle"
          size="md"
          leading={<ExternalLink size={12} />}
          onClick={() => openExternal(REPO_URL)}
        >
          {t('about.github')}
        </Button>
        <Button
          variant="subtle"
          size="md"
          leading={<Building2 size={12} />}
          onClick={() => {
            useAppStore.getState().setMode?.('enterprise');
          }}
        >
          {t('about.commercial_license')}
        </Button>
      </div>
      {selfCheck && (
        <div className="settings-selfcheck">
          {selfCheck.checks.map((c) => (
            <Row
              key={c.id}
              label={c.label}
              value={
                <span>
                  <Badge
                    tone={c.status === 'ok' ? 'success' : c.status === 'warn' ? 'warn' : 'danger'}
                  >
                    {c.status === 'ok' ? <CheckCircle size={11} /> : <AlertCircle size={11} />}{' '}
                    {t(`about.self_check_${c.status}`)}
                  </Badge>{' '}
                  {c.detail}
                  {c.hint && (
                    <span className="settings-muted font-sans text-[var(--text-md)] text-[var(--chrome-fg-dim)]">
                      {' '}
                      — {c.hint}
                    </span>
                  )}
                  {c.status !== 'ok' && CHECK_FIX_CATEGORY[c.id] && (
                    <>
                      {' '}
                      <OpenCategoryButton categoryId={CHECK_FIX_CATEGORY[c.id]} />
                    </>
                  )}
                </span>
              }
            />
          ))}
          <p className="settings-muted font-sans text-[var(--text-md)] text-[var(--chrome-fg-dim)]">
            {selfCheck.summary.ok
              ? t('about.self_check_healthy')
              : t('about.self_check_attention', { count: selfCheck.summary.failures })}
          </p>
        </div>
      )}
    </SettingsSection>
  );
}
