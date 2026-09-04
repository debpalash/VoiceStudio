/**
 * Settings → Performance & Device → Compute-time budget (#1787).
 *
 * Surfaces the two env vars that bound how long one synthesis job is allowed
 * to run before it is abandoned as "too heavy for the available compute" —
 * `OMNIVOICE_GENERATE_TIMEOUT_S` (accelerated/GPU-family hosts) and
 * `OMNIVOICE_CPU_GENERATE_TIMEOUT_S` (CPU-only hosts, which are legitimately
 * slower). Before this panel the only control was the env var itself, which a
 * desktop-installer user has no obvious way to set — the timeout error told
 * people to "raise the generation timeout" with no UI path to do it (#1774,
 * #1778 duplicates).
 *
 * Both values are read once by services/model_manager.py at backend IMPORT
 * time, so saving here (via the same /system/set-env + prefs.json persistence
 * every other row on this page uses) does not change the running process —
 * it takes effect on the next backend restart, which is what `env_prefs`
 * restores before `services.model_manager` is first imported. Hence the
 * `RestartBadge` on every row here: never imply an instant effect this
 * control cannot deliver.
 *
 * Review fix (#1787, CodeRabbit/Greptile P1 — a control that looks like it
 * works and doesn't is the exact defect this issue exists to remove):
 *
 *   1. An explicit CPU budget always governs CPU-family generation, even
 *      when the accelerated budget is also explicit (model_manager.py's
 *      `cpu_explicit` check) — but if the CPU row is left at its default
 *      (never saved), the accelerated budget is still the legacy fallback
 *      for CPU jobs too. The CPU row's note says so, so the relationship is
 *      never silently assumed.
 *   2. `/system/info`'s `*_shadowed` flags detect an external env var (shell,
 *      `.env`, Docker, …) already providing a key — `os.environ.setdefault`
 *      in core.prefs.restore_env() is then a permanent no-op for it, restart
 *      or not. A shadowed row shows a warning instead of the "Restart
 *      required" badge, and a save into it reports the same warning instead
 *      of a plain success toast — the panel must never claim a save applies
 *      when it demonstrably will not.
 */
import React, { useEffect, useState } from 'react';
import { Timer, AlertTriangle } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { apiJson, apiPost } from '../../api/client';
import { Button, Badge } from '../../ui';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import RestartBadge from './RestartBadge';

// Keep in sync with backend/api/routers/system.py's _MAX_GENERATE_TIMEOUT_S.
const MAX_TIMEOUT_S = 21600; // 6 hours

function BudgetRow({ envKey, label, note, currentValue, shadowed }) {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  // Sticky once observed shadowed for THIS key this session — a save while
  // shadowed must keep showing the warning even before the next /system/info
  // refetch confirms it again.
  const [saveShadowed, setSaveShadowed] = useState(false);

  // Prefill once the effective value arrives; don't clobber an in-progress
  // edit on a background refetch.
  useEffect(() => {
    setValue((prev) => (prev ? prev : currentValue != null ? String(currentValue) : ''));
  }, [currentValue]);

  const isShadowed = shadowed || saveShadowed;

  const save = async () => {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0 || n > MAX_TIMEOUT_S) {
      toast.error(
        t('settings.generate_timeout_invalid', {
          defaultValue: `Enter a number of seconds greater than 0 and at most ${MAX_TIMEOUT_S}.`,
          max: MAX_TIMEOUT_S,
        }),
      );
      return;
    }
    setSaving(true);
    try {
      const res = await apiPost('/system/set-env', { key: envKey, value: String(n) });
      if (res?.shadowed) {
        setSaveShadowed(true);
        toast(
          t('settings.generate_timeout_shadowed_toast', {
            defaultValue:
              'Saved, but an environment variable outside VoiceStudio is currently setting this — it will keep being used until that variable is removed.',
          }),
          { icon: '⚠️', duration: 10000 },
        );
      } else {
        setSaveShadowed(false);
        toast.success(
          t('settings.generate_timeout_saved', {
            defaultValue: 'Saved. Restart VoiceStudio for the new budget to take effect.',
          }),
        );
      }
    } catch (e) {
      toast.error(t('settings.save_failed', { message: e.message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingRow
      title={
        <>
          {label}
          {isShadowed ? (
            <Badge tone="warn" size="xs" data-testid={`generate-timeout-shadowed-${envKey}`}>
              <AlertTriangle size={10} aria-hidden="true" />{' '}
              {t('settings.generate_timeout_shadowed_badge', {
                defaultValue: 'Overridden externally',
              })}
            </Badge>
          ) : (
            <RestartBadge />
          )}
        </>
      }
      subtitle={
        <code className="rounded-[4px] bg-[var(--chrome-hover-bg)] px-[6px] py-[2px] font-mono text-[length:var(--text-xs)] text-[var(--chrome-fg-muted)]">
          {envKey}
        </code>
      }
      note={
        isShadowed
          ? t('settings.generate_timeout_shadowed_note', {
              defaultValue:
                'An environment variable outside VoiceStudio (shell, .env file, or container) is currently setting this — your saved value here is ignored until that variable is removed.',
            })
          : note
      }
      control={
        <>
          <SettingsInput
            type="number"
            min={1}
            max={MAX_TIMEOUT_S}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && save()}
            aria-label={label}
            data-testid={`generate-timeout-input-${envKey}`}
          />
          <Button
            size="sm"
            variant="subtle"
            onClick={save}
            loading={saving}
            disabled={!value.trim()}
            data-testid={`generate-timeout-save-${envKey}`}
          >
            {t('credentials.save')}
          </Button>
        </>
      }
    />
  );
}

export default function GenerateBudgetPanel() {
  const { t } = useTranslation();
  const [info, setInfo] = useState(null);

  useEffect(() => {
    const ctrl = { aborted: false };
    (async () => {
      try {
        const data = await apiJson('/system/info');
        if (!ctrl.aborted) setInfo(data);
      } catch {
        // Loopback-only; if unreachable just leave the inputs empty.
      }
    })();
    return () => {
      ctrl.aborted = true;
    };
  }, []);

  return (
    <SettingsSection
      icon={Timer}
      title={t('settings.generate_budget_title', { defaultValue: 'Compute-time budget' })}
      description={t('settings.generate_budget_desc', {
        defaultValue:
          'How long one generation may run before it is abandoned as too heavy for this hardware. CPU renders are legitimately slower than accelerated ones, so they get their own, higher floor.',
      })}
    >
      <BudgetRow
        envKey="OMNIVOICE_GENERATE_TIMEOUT_S"
        label={t('settings.generate_timeout_gpu', { defaultValue: 'Accelerated (GPU) budget' })}
        note={t('settings.generate_timeout_gpu_note', {
          defaultValue:
            'Used when generation runs on CUDA, ROCm, MPS, or another accelerator. Also used for CPU-family generation if no CPU budget below is set.',
        })}
        currentValue={info?.generate_timeout_s}
        shadowed={info?.generate_timeout_shadowed}
      />
      <BudgetRow
        envKey="OMNIVOICE_CPU_GENERATE_TIMEOUT_S"
        label={t('settings.generate_timeout_cpu', { defaultValue: 'CPU budget' })}
        note={t('settings.generate_timeout_cpu_note', {
          defaultValue:
            'Used when generation runs on the CPU. Once saved here, this always governs CPU-family generation — independent of the accelerated budget above.',
        })}
        currentValue={info?.cpu_generate_timeout_s}
        shadowed={info?.cpu_generate_timeout_shadowed}
      />
    </SettingsSection>
  );
}
