import React from 'react';
import {
  ShieldCheck,
  CheckCircle,
  AlertCircle,
  FolderOpen,
  Database,
  Wifi,
  EyeOff,
} from 'lucide-react';
import { Trans, useTranslation } from 'react-i18next';
import { Badge, Button } from '../../ui';
import { useAppStore } from '../../store';
import { SettingsSection } from './primitives';
import AnalyticsOptIn from './AnalyticsOptIn';
import WatermarkControl from './WatermarkControl';

// Providers that send dialogue text to a third-party service vs. the ones that
// run fully on-device (backend/api/routers/dub_translate.py). Anything else —
// including the backend's safe-defaults value 'unknown' or a missing
// system-info payload — must NOT get the confident green "offline" claim.
const ONLINE_PROVIDERS = ['google', 'deepl', 'mymemory', 'microsoft', 'openai'];
const OFFLINE_PROVIDERS = ['nllb', 'argos', 'libretranslate'];

export default function PrivacyTab({ info }) {
  const { t } = useTranslation();
  const openSettingsTab = useAppStore((s) => s.openSettingsTab);
  const provider = info?.translate_provider;

  let translatorBadge;
  if (provider && ONLINE_PROVIDERS.includes(provider)) {
    translatorBadge = (
      <span className="inline-flex flex-wrap items-center gap-[var(--space-2)]">
        <Badge tone="warn">
          <AlertCircle size={11} /> {t('privacy.translator_online', { provider })}
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => openSettingsTab('translation')}
          data-testid="privacy-change-translator"
        >
          {t('privacy.change_translator', { defaultValue: 'Change translator' })}
        </Button>
      </span>
    );
  } else if (provider && OFFLINE_PROVIDERS.includes(provider)) {
    translatorBadge = (
      <Badge tone="success">
        <CheckCircle size={11} /> {t('privacy.translator_offline')}
      </Badge>
    );
  } else {
    // Backend down, errored (translate_provider: 'unknown'), or an
    // unrecognized provider — don't render a privacy assurance without data.
    translatorBadge = (
      <Badge tone="neutral" data-testid="privacy-translator-unknown">
        {t('privacy.translator_unknown', { defaultValue: 'Unknown' })}
      </Badge>
    );
  }

  return (
    <SettingsSection icon={ShieldCheck} title={t('settings.privacy')}>
      <div className="flex items-start gap-[var(--space-4)] rounded-[calc(var(--chrome-radius-pill)*1.4)] bg-[color-mix(in_srgb,var(--color-success)_8%,var(--chrome-bg))] p-[var(--space-5)]">
        <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--chrome-radius-pill)] bg-[color-mix(in_srgb,var(--color-success)_14%,var(--chrome-bg))] text-[var(--color-success)]">
          <ShieldCheck size={20} aria-hidden="true" />
        </span>
        <p className="settings-prose m-0 self-center font-sans text-[length:var(--text-md)] leading-[1.6] text-[var(--chrome-fg-muted)] [text-wrap:pretty]">
          <Trans i18nKey="privacy.desc" components={{ 1: <strong /> }} />
        </p>
      </div>

      <dl className="my-[var(--space-4)] grid grid-cols-1 gap-[var(--space-3)] @min-[560px]/settings:grid-cols-2">
        <div className="min-w-0 rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] p-[var(--space-4)]">
          <dt className="flex items-center gap-[var(--space-2)] text-[length:var(--text-xs)] font-medium text-[var(--chrome-fg-dim)]">
            <FolderOpen size={13} aria-hidden="true" /> {t('privacy.uploads_at')}
          </dt>
          <dd className="m-0 mt-[var(--space-2)] break-words [font-family:var(--chrome-font-mono)] text-[length:var(--text-xs)] leading-[1.5] text-[var(--chrome-fg)]">
            {info?.data_dir ? `${info.data_dir}/` : '—'}
          </dd>
        </div>
        <div className="min-w-0 rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] p-[var(--space-4)]">
          <dt className="flex items-center gap-[var(--space-2)] text-[length:var(--text-xs)] font-medium text-[var(--chrome-fg-dim)]">
            <FolderOpen size={13} aria-hidden="true" /> {t('privacy.outputs_at')}
          </dt>
          <dd className="m-0 mt-[var(--space-2)] break-words [font-family:var(--chrome-font-mono)] text-[length:var(--text-xs)] leading-[1.5] text-[var(--chrome-fg)]">
            {info?.outputs_dir || '—'}
          </dd>
        </div>
        <div className="min-w-0 rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] p-[var(--space-4)]">
          <dt className="flex items-center gap-[var(--space-2)] text-[length:var(--text-xs)] font-medium text-[var(--chrome-fg-dim)]">
            <Database size={13} aria-hidden="true" /> {t('privacy.gen_history')}
          </dt>
          <dd className="m-0 mt-[var(--space-2)]">
            <Badge tone="neutral">{t('privacy.local_sqlite')}</Badge>
          </dd>
        </div>
        <div className="min-w-0 rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] p-[var(--space-4)]">
          <dt className="flex items-center gap-[var(--space-2)] text-[length:var(--text-xs)] font-medium text-[var(--chrome-fg-dim)]">
            <Wifi size={13} aria-hidden="true" /> {t('privacy.network_calls')}
          </dt>
          <dd className="m-0 mt-[var(--space-2)]">{translatorBadge}</dd>
        </div>
        <div className="min-w-0 rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] p-[var(--space-4)] @min-[560px]/settings:col-span-2">
          <dt className="flex items-center gap-[var(--space-2)] text-[length:var(--text-xs)] font-medium text-[var(--chrome-fg-dim)]">
            <EyeOff size={13} aria-hidden="true" /> {t('privacy.model_telemetry')}
          </dt>
          <dd className="m-0 mt-[var(--space-2)]">
            <Badge tone="success">
              <CheckCircle size={11} /> {t('privacy.no_tracking')}
            </Badge>
          </dd>
        </div>
      </dl>
      {/* Opt-in product analytics. Renders nothing when the build ships no
          destination, and is OFF until the user turns it on — so the
          "no tracking" default above stays true for everyone who doesn't. */}
      {/* The provenance mark. ON by default (the opposite of analytics
          below), and now actually controllable — errors.a_watermark has told
          users it lives here since watermarking shipped. */}
      <div className="rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-hover-bg)] px-[var(--space-4)] [&>[data-slot=setting-row]]:border-b [&>[data-slot=setting-row]]:border-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] [&>[data-slot=setting-row]:last-of-type]:border-b-0">
        <WatermarkControl />
        <AnalyticsOptIn />
      </div>
    </SettingsSection>
  );
}
