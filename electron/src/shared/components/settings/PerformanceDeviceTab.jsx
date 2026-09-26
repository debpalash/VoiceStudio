/**
 * Settings → Performance & Device.
 *
 * Combines the torch.compile performance toggle (PerformancePanel) with the
 * device / RAM / VRAM / backend readouts that used to live in AboutTab. The
 * readouts are the natural neighbour of the performance toggle, and keeping a
 * single home avoids the old About/Performance duplication.
 *
 * Read-only data comes from the shared TanStack Query caches (useSysinfo /
 * useSystemInfo / useModelStatus) — same hooks App.jsx and Settings use, so no
 * duplicate requests.
 */
import React from 'react';
import { Gauge, CheckCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useSysinfo, useSystemInfo, useModelStatus } from '../../api/hooks';
import { Badge } from '../../ui';
import { SettingsSection } from './primitives';
import Row from './Row';
import PerformancePanel from './PerformancePanel';
import ComputeDevicePanel from './ComputeDevicePanel';
import GenerateBudgetPanel from './GenerateBudgetPanel';

export default function PerformanceDeviceTab() {
  const { t } = useTranslation();
  const { data: hw } = useSysinfo();
  const { data: info } = useSystemInfo();
  const { data: status } = useModelStatus();

  return (
    <>
      <PerformancePanel />

      <ComputeDevicePanel />

      <GenerateBudgetPanel />

      <SettingsSection
        icon={Gauge}
        title={t('settings.device', { defaultValue: 'Device & compute' })}
        description={t('settings.device_desc', {
          defaultValue: 'Live hardware and backend readouts.',
        })}
      >
        <Row label={t('about.platform')} value={info?.platform || '—'} />
        <Row label={t('about.architecture')} value={info?.arch || '—'} mono />
        <Row label={t('about.python')} value={info?.python || '—'} mono />
        <Row label={t('about.compute_device')} value={info?.device || '—'} mono />
        <Row
          label={t('about.gpu_active')}
          value={
            hw?.gpu_active ? (
              <Badge tone="success">
                <CheckCircle size={11} /> {t('about.yes')}
              </Badge>
            ) : (
              <Badge tone="neutral">{t('about.no')}</Badge>
            )
          }
        />
        <Row
          label={t('about.ram')}
          value={hw ? `${hw.ram?.toFixed(2)} / ${hw.total_ram?.toFixed(2)} GB` : '—'}
          mono
        />
        <Row label={t('about.vram')} value={hw ? `${hw.vram?.toFixed(2)} GB` : '—'} mono />
        <Row
          label={t('about.backend')}
          value={
            <Badge
              tone={
                status?.status === 'ready'
                  ? 'success'
                  : status?.status === 'loading'
                    ? 'warn'
                    : 'neutral'
              }
            >
              {status?.status === 'ready'
                ? t('models.ready_badge')
                : status?.status === 'loading'
                  ? t('models.loading_badge')
                  : status?.status === 'idle'
                    ? t('models.idle_badge')
                    : status?.status || t('common.unknown', { defaultValue: 'unknown' })}
            </Badge>
          }
        />
        <Row
          label={t('about.active_model')}
          value={status?.repo_id || info?.model_checkpoint || '—'}
          mono
        />
        <Row label={t('about.asr_model')} value={info?.asr_model || '—'} mono />
        <Row label={t('about.translator')} value={info?.translate_provider || '—'} />
      </SettingsSection>
    </>
  );
}
