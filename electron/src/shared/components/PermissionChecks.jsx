import React from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge, Button } from '../ui';
import usePermissions from '../hooks/usePermissions';
import { detectPlatform, micHintKey } from '../utils/micError';
import { openAccessibilitySettings, openMicrophoneSettings } from '../utils/permissions';

/**
 * PermissionChecks — OS-permission rows for the SetupWizard's System Check
 * step: Microphone (all platforms) and Accessibility (macOS only), in the
 * same LED-row grammar as PreflightPanel. Renders nothing outside the Tauri
 * shell (browser web UI / Docker have no OS grants to probe).
 *
 * Status → chip: granted = ok, denied = warn (+ an Open Settings deep-link),
 * prompt/unknown = neutral. Status refreshes on window focus (the user comes
 * back from System Settings and the chip is already up to date) plus an
 * explicit Recheck button.
 */

// Same LED classes as SetupWizard's CHECK_LED (kept local — the wizard is
// concurrently edited and doesn't export them).
const LED = {
  ok: 'bg-success shadow-[0_0_5px_1px_color-mix(in_srgb,var(--color-success)_50%,transparent)]',
  warn: 'bg-warn shadow-[0_0_5px_1px_color-mix(in_srgb,var(--color-warn)_50%,transparent)]',
  neutral: 'bg-fg-subtle/40',
};

/** Map a mic-style status string to { led, badgeTone, labelKey }. */
export function permissionChip(status) {
  if (status === 'granted') {
    return { led: 'ok', tone: 'success', labelKey: 'permissions.status_granted' };
  }
  if (status === 'denied') {
    return { led: 'warn', tone: 'warn', labelKey: 'permissions.status_denied' };
  }
  if (status === 'prompt') {
    return { led: 'neutral', tone: 'neutral', labelKey: 'permissions.status_prompt' };
  }
  return { led: 'neutral', tone: 'neutral', labelKey: 'permissions.status_unknown' };
}

function CheckRow({ id, label, status, detail, onOpenSettings, t }) {
  const chip = permissionChip(status);
  return (
    <div className="flex items-start gap-2 rounded-md px-2.5 py-2" data-testid={`perm-row-${id}`}>
      <span
        className={cn('mt-1 h-1.5 w-1.5 shrink-0 rounded-full', LED[chip.led])}
        aria-hidden="true"
      />
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="flex items-center gap-2 text-sm font-semibold">
          {label}
          <Badge tone={chip.tone} data-testid={`perm-chip-${id}`}>
            {t(chip.labelKey)}
          </Badge>
        </span>
        <span className="text-xs leading-snug text-fg-muted">{detail}</span>
        {status === 'denied' && onOpenSettings && (
          <span>
            <Button variant="ghost" size="sm" onClick={onOpenSettings}>
              {t('permissions.open_settings')}
            </Button>
          </span>
        )}
      </div>
    </div>
  );
}

export default function PermissionChecks({ platform = detectPlatform() }) {
  const { t } = useTranslation();
  const { available, mic, a11y, recheck } = usePermissions();
  if (!available) return null; // browser/dev — nothing to probe, no noise

  return (
    <section className="mt-5 flex flex-col gap-2.5" data-testid="permission-checks">
      <h2 className="m-0 flex items-center gap-2 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.18em] text-fg-muted">
        <span>{t('permissions.title')}</span>
        <span
          className="h-px flex-1 bg-gradient-to-r from-border-strong to-transparent"
          aria-hidden="true"
        />
        <Button variant="ghost" size="sm" onClick={recheck} leading={<RotateCw size={12} />}>
          {t('setup.recheck')}
        </Button>
      </h2>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] items-start gap-x-6 gap-y-2">
        <CheckRow
          id="microphone"
          label={t('permissions.microphone')}
          status={mic}
          // Granted → say what it's for; anything else → the per-OS fix path.
          detail={mic === 'granted' ? t('permissions.microphone_why') : t(micHintKey(platform))}
          onOpenSettings={async () => {
            if (!(await openMicrophoneSettings())) {
              // Linux: no mic-privacy pane — the row hint already points at
              // the system sound settings, nothing else to open.
              recheck();
            }
          }}
          t={t}
        />
        {platform === 'mac' && (
          <CheckRow
            id="accessibility"
            label={t('permissions.accessibility')}
            status={a11y ? 'granted' : 'denied'}
            detail={a11y ? t('permissions.accessibility_why') : t('capture.a11y_setup')}
            onOpenSettings={() => openAccessibilitySettings()}
            t={t}
          />
        )}
      </div>
    </section>
  );
}
