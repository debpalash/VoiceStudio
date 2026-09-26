/**
 * Settings → Storage → "Remove all data" (#1089).
 *
 * The in-app half of the uninstaller. A user who installed the .dmg / .msi /
 * AppImage has no repo, so `scripts/uninstall.sh` never reaches them — this is
 * the affordance they actually have. It asks the desktop shell for every folder
 * this install owns (honoring custom + portable locations), shows each with its
 * real size, and deletes them behind a typed confirmation.
 *
 * Two deliberate choices:
 *  - The **shared Hugging Face cache is opt-in**, on its own checkbox with the
 *    caveat spelled out: it's the standard HF cache other ML tools use, so
 *    removing it can delete models VoiceStudio never downloaded.
 *  - The confirmation requires **typing the word**, not just a click. This
 *    deletes voice profiles and projects that cannot be recovered.
 *
 * After the purge the app quits: the Python environment it runs on is gone, so
 * there is nothing to return to. Removing the app *binary* is a per-platform
 * step we link out to (docs/install/uninstall.md).
 *
 * Outside the Tauri shell (browser/Docker) there is no local install to remove,
 * so this renders nothing.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Trash2,
  AlertTriangle,
  Folder,
  Package,
  ScrollText,
  Database,
  KeyRound,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { Button, Dialog } from '../../ui';
import { SettingsSection } from './primitives';
import StorageTargetRow from './StorageTargetRow';
import { fmtBytes } from './bytes';

const inTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

// Re-exported: this was the panel's own helper before the Storage panels shared
// one formatter, and it is imported by name in the tests.
export { fmtBytes };

/** Bytes the purge will actually free, given the opt-in on the shared cache.
 *  Pure + exported: the number shown on the button must match what gets deleted. */
export function freedBytes(targets, includeModels) {
  return (targets || [])
    .filter((t) => t.exists && (!t.shared || includeModels))
    .reduce((sum, t) => sum + (t.size_bytes || 0), 0);
}

const ICONS = {
  data: Folder,
  env: Package,
  logs: ScrollText,
  userenv: KeyRound,
  models: Database,
};

export default function UninstallPanel() {
  const { t } = useTranslation();
  const [targets, setTargets] = useState(null);
  const [open, setOpen] = useState(false);
  const [includeModels, setIncludeModels] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);

  const CONFIRM_WORD = t('settings.uninstall_confirm_word', { defaultValue: 'DELETE' });

  const scan = useCallback(async () => {
    if (!inTauri()) return;
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      setTargets(await invoke('uninstall_scan'));
    } catch (e) {
      console.warn('[UninstallPanel] scan failed', e);
    }
  }, []);

  useEffect(() => {
    scan();
  }, [scan]);

  const purge = async () => {
    setBusy(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const report = await invoke('uninstall_purge', { includeModels });
      if (report?.failed?.length) {
        toast.error(
          t('settings.uninstall_partial', {
            defaultValue: 'Some folders could not be removed: {{paths}}',
            paths: report.failed.join(', '),
          }),
          { duration: 10000 },
        );
      }
      // The Python env we run on is gone — quit rather than pretend to carry on.
      await invoke('quit_app').catch(() => {});
    } catch (e) {
      setBusy(false);
      const message = e instanceof Error ? e.message : String(e);
      toast.error(
        t('settings.uninstall_failed', {
          defaultValue: 'Could not remove the data: {{message}}',
          message: message === 'uninstall_task_failed' ? t('bootstrap.unknown_error') : message,
        }),
      );
    }
  };

  if (!inTauri()) return null;

  const present = (targets || []).filter((x) => x.exists);
  const models = present.find((x) => x.shared);
  const owned = present.filter((x) => !x.shared);
  const willFree = freedBytes(targets, includeModels);
  const LABELS = {
    data: t('settings.uninstall_target_data', {
      defaultValue: 'Voices, projects, generated audio, history',
    }),
    env: t('settings.uninstall_target_env', {
      defaultValue: 'Settings + the managed Python environment',
    }),
    logs: t('settings.uninstall_target_logs', { defaultValue: 'Logs' }),
    userenv: t('settings.uninstall_target_userenv', {
      defaultValue: 'Saved environment (cache location, tokens)',
    }),
    models: t('settings.uninstall_target_models', {
      defaultValue: 'Downloaded model weights (shared Hugging Face cache)',
    }),
  };

  return (
    <>
      <SettingsSection
        icon={Trash2}
        title={t('settings.uninstall', { defaultValue: 'Remove all data' })}
        description={t('settings.uninstall_desc', {
          defaultValue: 'Delete everything VoiceStudio has written to this machine, then quit.',
        })}
      >
        <p className="m-0 mb-[var(--space-4)] [font-family:var(--font-sans)] text-[length:var(--text-md)] leading-[1.6] text-[var(--chrome-fg-muted)]">
          {t('settings.uninstall_body', {
            defaultValue:
              'VoiceStudio is fully local, so uninstalling is just deleting the folders it wrote. This removes your voice profiles, projects, and generated audio permanently — there is no undo. Removing the app itself is a separate step.',
          })}
        </p>
        {owned.length > 0 && (
          <div
            className="settings-list-scroll mb-[var(--space-4)] flex max-h-[420px] flex-col gap-[var(--space-2)] overflow-y-auto overscroll-contain rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-bg)] p-[var(--space-2)] [scrollbar-gutter:stable]"
            tabIndex={0}
            aria-label={t('settings.uninstall', { defaultValue: 'Remove all data' })}
          >
            {owned.map((tg) => (
              <StorageTargetRow
                key={tg.key}
                icon={ICONS[tg.key]}
                label={LABELS[tg.key] || tg.key}
                path={tg.path}
                size={tg.size_bytes}
                share={willFree > 0 ? tg.size_bytes / willFree : 0}
                testId={`uninstall-target-${tg.key}`}
              />
            ))}

            {/* The shared cache is a different KIND of thing, so it gets its own
                group and its own checkbox — here, in the list, not buried in the
                confirm dialog, so the total on the button moves when you tick it. */}
            {models && (
              <>
                <span className="mt-[var(--space-2)] [font-family:var(--font-sans)] text-[length:var(--text-xs)] uppercase tracking-[var(--chrome-label-track)] text-[var(--chrome-fg-dim)]">
                  {t('settings.uninstall_optional_group', { defaultValue: 'Optional' })}
                </span>
                <StorageTargetRow
                  icon={ICONS.models}
                  label={LABELS.models}
                  hint={t('settings.uninstall_models_caveat', {
                    defaultValue:
                      'The standard Hugging Face cache, shared with other AI tools on this machine — removing it may delete models VoiceStudio never downloaded. Anything VoiceStudio needs downloads again.',
                  })}
                  path={models.path}
                  size={models.size_bytes}
                  share={willFree > 0 ? models.size_bytes / willFree : 0}
                  checked={includeModels}
                  onToggle={(e) => setIncludeModels(e.target.checked)}
                  warn
                  testId="uninstall-include-models"
                />
              </>
            )}

            <p className="m-0 mt-[var(--space-2)] [font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)]">
              {t('settings.uninstall_total', {
                defaultValue: '{{count}} locations · {{size}} will be freed',
                count: present.filter((x) => !x.shared || includeModels).length,
                size: fmtBytes(willFree),
              })}
            </p>
          </div>
        )}
        <Button
          variant="danger"
          size="md"
          leading={<Trash2 size={13} />}
          onClick={() => {
            setTyped('');
            setOpen(true);
          }}
          data-testid="uninstall-open"
        >
          {t('settings.uninstall', { defaultValue: 'Remove all data' })}
        </Button>
      </SettingsSection>

      <Dialog
        open={open}
        onClose={() => !busy && setOpen(false)}
        title={t('settings.uninstall_confirm_title', {
          defaultValue: 'Remove all VoiceStudio data?',
        })}
        size="md"
        footer={
          <>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setOpen(false)}>
              {t('common.cancel', { defaultValue: 'Cancel' })}
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={busy}
              disabled={busy || typed.trim().toUpperCase() !== CONFIRM_WORD}
              onClick={purge}
              data-testid="uninstall-confirm"
            >
              {t('settings.uninstall_confirm', {
                defaultValue: 'Delete {{size}} and quit',
                size: fmtBytes(willFree),
              })}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-[var(--space-4)]">
          <p className="m-0 flex items-start gap-[var(--space-3)] [font-family:var(--font-sans)] text-[length:var(--text-md)] leading-[1.6] text-[var(--chrome-fg)]">
            <AlertTriangle size={16} className="mt-1 shrink-0 text-[var(--color-danger)]" />
            <span>
              {t('settings.uninstall_confirm_body', {
                defaultValue:
                  'Your voice profiles, projects, and generated audio will be permanently deleted. This cannot be undone.',
              })}
            </span>
          </p>

          {/* What is actually going, at the moment of no return. The opt-in for
              the shared cache lives in the list behind this dialog — asking twice
              invites the user to skim, and this is the screen to read. */}
          <ul className="m-0 list-none p-0" data-testid="uninstall-summary">
            {present
              .filter((x) => !x.shared || includeModels)
              .map((tg) => (
                <li
                  key={tg.key}
                  className="flex items-baseline justify-between gap-[var(--space-3)] py-[var(--space-1)] [font-family:var(--font-sans)] text-[length:var(--text-md)] text-[var(--chrome-fg)]"
                >
                  <span>{LABELS[tg.key] || tg.key}</span>
                  <span className="shrink-0 [font-family:var(--font-mono)] text-[length:var(--text-sm)] tabular-nums text-[var(--chrome-fg-muted)]">
                    {fmtBytes(tg.size_bytes)}
                  </span>
                </li>
              ))}
          </ul>

          {models && includeModels && (
            <p className="m-0 flex items-start gap-[var(--space-3)] [font-family:var(--font-sans)] text-[length:var(--text-sm)] leading-[1.6] text-[var(--chrome-fg-muted)]">
              <AlertTriangle
                size={16}
                className="mt-1 shrink-0 text-[var(--chrome-severity-warn)]"
              />
              <span data-testid="uninstall-models-warning">
                {t('settings.uninstall_models_warning', {
                  defaultValue:
                    'This includes the shared Hugging Face cache ({{size}}) — models other AI tools downloaded may go with it.',
                  size: fmtBytes(models.size_bytes),
                })}
              </span>
            </p>
          )}

          <label className="flex flex-col gap-[var(--space-2)]">
            <span className="[font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)]">
              {t('settings.uninstall_type_to_confirm', {
                defaultValue: 'Type {{word}} to confirm:',
                word: CONFIRM_WORD,
              })}
            </span>
            <input
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck="false"
              data-testid="uninstall-type-confirm"
              className="rounded-[var(--radius-md)] [border:1px_solid_var(--chrome-border)] bg-[var(--chrome-hover-bg)] px-[var(--space-3)] py-[var(--space-2)] [font-family:var(--font-mono)] text-[length:var(--text-md)] text-[var(--chrome-fg)] focus:outline-none"
            />
          </label>
        </div>
      </Dialog>
    </>
  );
}
