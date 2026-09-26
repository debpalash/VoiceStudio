/**
 * Settings → Storage → "Reset & remove".
 *
 * Factory reset used to do exactly one thing — clear localStorage — while the
 * only other option was deleting everything and starting over. Between "forget
 * my theme" and "wipe the machine" sat every reset a user actually needs: drop a
 * corrupt model download, remove a wedged sidecar engine, put the settings back
 * without losing a single voice. This panel is that middle ground: four presets
 * for the common cases, and a per-scope checklist for everything else.
 *
 * Three rules it keeps:
 *  - **The number is the truth.** Every scope shows its real on-disk size, and
 *    the confirm button shows the sum of what is actually ticked. A reset that
 *    says "14.2 GB" frees 14.2 GB.
 *  - **The shared model cache is never swept up silently.** On macOS and Linux
 *    the Hugging Face cache is shared with every other ML tool on the machine,
 *    so it is its own checkbox and says so. (On Windows and in portable installs
 *    the cache is app-private — the shell computes that, and the caveat is not
 *    shown when it does not apply.)
 *  - **Nothing irreversible happens on one click.** Removing voices, projects or
 *    models needs the word typed.
 *
 * Disk scopes are executed by the Rust shell (`reset.rs`), which stops the
 * backend, deletes, and starts it again — a running backend cannot delete the
 * weights it has mapped into memory, nor recreate the directories it lost. The
 * Browser-owned state is handled here too: UI preferences (localStorage),
 * long-form projects (IndexedDB), and history (the DELETE endpoints, which
 * take rows and audio together).
 *
 * Outside the Tauri shell (browser / Docker) there is no local install to clear,
 * so only the preferences tier is offered.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  RotateCcw,
  AlertTriangle,
  ChevronRight,
  Palette,
  SlidersHorizontal,
  History,
  Folder,
  Boxes,
  Wrench,
  Database,
  Archive,
  ScrollText,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { Button, Dialog } from '../../ui';
import { SettingsSection } from './primitives';
import { fmtBytes } from './bytes';
import StorageTargetRow from './StorageTargetRow';
import { clearLocalPreferences } from '../../utils/prefKeys';
import {
  clearLongformProjects,
  flushLongformPendingWrites,
  LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR,
} from '../../utils/longformPersistence';
import { reloadAfterApplicationPersistence } from '../../utils/persistenceLifecycle';
import { clearHistory } from '../../api/generate';
import { clearDubHistory } from '../../api/dub';

const inTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

/** Scopes that own no files — cleared in the browser, never sent to the shell. */
export const FRONTEND_SCOPES = ['ui_prefs', 'history'];

/** Deleting these cannot be undone, so they gate the typed confirmation. */
export const IRREVERSIBLE_SCOPES = ['content'];

/** One glyph per scope, so a nine-row list can be scanned instead of read. */
const SCOPE_ICONS = {
  ui_prefs: Palette,
  settings: SlidersHorizontal,
  history: History,
  content: Folder,
  engines: Boxes,
  tools: Wrench,
  models: Database,
  caches: Archive,
  logs: ScrollText,
};

/** Render order. Cheapest and safest first, so the list reads as an escalation. */
export const SCOPE_ORDER = [
  'ui_prefs',
  'settings',
  'history',
  'content',
  'engines',
  'tools',
  'models',
  'caches',
  'logs',
];

/**
 * The four one-click tiers. `everything` deliberately stops short of the managed
 * Python environment: that is the interpreter the app runs on, and rebuilding it
 * is a multi-GB download. A reset should hand back a working app on the far
 * side — "Remove all data" below is the door out of that.
 */
export const PRESETS = {
  ui: ['ui_prefs'],
  settings: ['ui_prefs', 'settings'],
  assets: ['models', 'engines', 'tools', 'caches'],
  everything: ['ui_prefs', 'settings', 'content', 'engines', 'tools', 'models', 'caches', 'logs'],
};

/** Bytes the reset will actually free — the sum of exactly what is ticked. */
export function selectedBytes(scopes, selected) {
  return (scopes || [])
    .filter((s) => selected.includes(s.key) && s.exists)
    .reduce((sum, s) => sum + (s.size_bytes || 0), 0);
}

/** Typing the word is required the moment something unrecoverable is in scope. */
export function needsTypedConfirm(selected) {
  return selected.some((k) => IRREVERSIBLE_SCOPES.includes(k));
}

/**
 * Split a selection into the work each half of the app is responsible for.
 * `content` wipes the whole database, so an explicit `history` tick alongside it
 * would be a redundant round-trip against rows that are about to be deleted.
 */
export function plan(selected) {
  const disk = selected.filter((k) => !FRONTEND_SCOPES.includes(k));
  return {
    disk,
    prefs: selected.includes('ui_prefs'),
    history: selected.includes('history') && !selected.includes('content'),
    longformProjects: selected.includes('content'),
    restart: disk.length > 0,
  };
}

/** The preset whose scope set matches the current ticks exactly, if any. */
export function matchingPreset(selected) {
  const same = (a, b) =>
    a.length === b.length && [...a].sort().every((v, i) => [...b].sort()[i] === v);
  return Object.keys(PRESETS).find((p) => same(PRESETS[p], selected)) || null;
}

// `_forceAdvanced` starts the "choose exactly what to remove" list expanded —
// used only by the visual-regression harness so a snapshot shows the full row
// treatment. It has no effect on the real toggle.
export default function ResetPanel({ _forceAdvanced = false } = {}) {
  const { t } = useTranslation();
  const [scopes, setScopes] = useState(null);
  const [selected, setSelected] = useState(PRESETS.ui);
  const [advanced, setAdvanced] = useState(_forceAdvanced);
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  // Post-reset page reload runs on a short timer; hold its id so unmount can
  // clear it — a dangling reload timer that fires after the component is gone
  // (e.g. a test env torn down before 400ms) throws from `window.location`.
  const reloadTimerRef = useRef(null);
  useEffect(() => () => clearTimeout(reloadTimerRef.current), []);

  const CONFIRM_WORD = t('settings.reset_confirm_word', { defaultValue: 'DELETE' });

  const scan = useCallback(async () => {
    if (!inTauri()) return;
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      setScopes(await invoke('reset_scan'));
    } catch (e) {
      console.warn('[ResetPanel] scan failed', e);
    }
  }, []);

  useEffect(() => {
    scan();
  }, [scan]);

  const byKey = useMemo(() => Object.fromEntries((scopes || []).map((s) => [s.key, s])), [scopes]);
  const willFree = selectedBytes(scopes, selected);
  const sharedModels = byKey.models?.shared && selected.includes('models');
  const typedOk = !needsTypedConfirm(selected) || typed.trim().toUpperCase() === CONFIRM_WORD;

  const toggle = (key) =>
    setSelected((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));

  const run = async () => {
    const steps = plan(selected);
    setBusy(true);
    try {
      // History first: its endpoints take the DB rows and their audio together,
      // and they need a backend that is still alive to do it.
      if (steps.history) {
        await Promise.all([clearHistory(), clearDubHistory()]);
      }
      if (steps.disk.length) {
        const { invoke } = await import('@tauri-apps/api/core');
        const report = await invoke('reset_purge', { scopes: steps.disk });
        if (report?.refused?.length || report?.failed?.length) {
          toast.error(
            t('settings.reset_partial', {
              defaultValue: 'Some items could not be removed: {{paths}}',
              paths: [...(report.refused || []), ...(report.failed || [])].join(', '),
            }),
            { duration: 10000 },
          );
        }
      }
      if (steps.longformProjects) await clearLongformProjects();
      if (steps.prefs) {
        // If projects are being retained, settle them first so an IndexedDB
        // failure can materialize its full fallback before preference reset
        // decides what to preserve. clearLongformProjects already performs the
        // ordered flush when content is intentionally being deleted.
        if (!steps.longformProjects) await flushLongformPendingWrites();
        clearLocalPreferences();
      }

      toast.success(
        steps.restart
          ? t('settings.reset_done_restart', {
              defaultValue: 'Reset complete — restarting VoiceStudio…',
            })
          : t('settings.reset_done', { defaultValue: 'Reset complete — reloading…' }),
      );
      setOpen(false);
      // The backend is coming back up behind us; the bootstrap splash and the
      // reconnecting banner (#1094) own that wait, so all this has to do is get
      // the UI back to a clean slate.
      reloadTimerRef.current = setTimeout(() => void reloadAfterApplicationPersistence(), 400);
    } catch (e) {
      setBusy(false);
      const message =
        e?.name === LONGFORM_LOCAL_FALLBACK_CLEAR_ERROR
          ? t('settings.reset_longform_failed')
          : t('settings.reset_failed', {
              defaultValue: 'Reset failed: {{message}}',
              message: e?.message || String(e),
            });
      toast.error(message);
    }
  };

  const LABELS = {
    ui_prefs: t('settings.reset_scope_ui_prefs', {
      defaultValue: 'UI preferences — theme, language, layout, dub settings',
    }),
    settings: t('settings.reset_scope_settings', {
      defaultValue: 'App settings — engine choices, voice defaults, saved options',
    }),
    history: t('settings.reset_scope_history', {
      defaultValue: 'Generation & dub history, with their audio',
    }),
    content: t('settings.reset_scope_content', {
      defaultValue: 'Voices, projects, generated audio, and the app database',
    }),
    engines: t('settings.reset_scope_engines', {
      defaultValue: 'Installed sidecar engines (IndexTTS-2 and friends)',
    }),
    tools: t('settings.reset_scope_tools', {
      defaultValue: 'Downloaded audio tools (ffmpeg, ffprobe, yt-dlp)',
    }),
    models: t('settings.reset_scope_models', {
      defaultValue: 'Downloaded model weights',
    }),
    caches: t('settings.reset_scope_caches', {
      defaultValue: 'Caches and temporary files',
    }),
    logs: t('settings.reset_scope_logs', { defaultValue: 'Logs and crash reports' }),
  };

  const TIERS = [
    {
      id: 'ui',
      label: t('settings.reset_tier_ui', { defaultValue: 'UI preferences only' }),
      hint: t('settings.reset_tier_ui_hint', {
        defaultValue: 'Theme, layout and dub knobs go back to defaults. Nothing on disk changes.',
      }),
    },
    {
      id: 'settings',
      label: t('settings.reset_tier_settings', { defaultValue: 'All settings' }),
      hint: t('settings.reset_tier_settings_hint', {
        defaultValue:
          'Every preference, in the app and on disk. Your voices, projects and models are untouched.',
      }),
    },
    {
      id: 'assets',
      label: t('settings.reset_tier_assets', { defaultValue: 'Downloaded assets & models' }),
      hint: t('settings.reset_tier_assets_hint', {
        defaultValue:
          'Model weights, sidecar engines, audio tools and caches. Everything you made stays. They re-download when next needed.',
      }),
    },
    {
      id: 'everything',
      label: t('settings.reset_tier_everything', { defaultValue: 'Everything VoiceStudio did' }),
      hint: t('settings.reset_tier_everything_hint', {
        defaultValue:
          'Back to a fresh install: settings, voices, projects, audio, models, engines, logs. The app restarts on the first-run screen.',
      }),
    },
  ];

  // No shell → no local install to clear. Preferences are all we own here.
  const shellless = !inTauri();
  const activePreset = matchingPreset(selected);

  return (
    <>
      <SettingsSection
        icon={RotateCcw}
        title={t('settings.reset', { defaultValue: 'Reset & remove' })}
        description={t('settings.reset_desc', {
          defaultValue: 'Put part — or all — of VoiceStudio back to how it shipped.',
        })}
      >
        {shellless ? (
          <p className="m-0 mb-[var(--space-4)] [font-family:var(--font-sans)] text-[length:var(--text-md)] leading-[1.6] text-[var(--chrome-fg-muted)]">
            {t('settings.reset_body_web', {
              defaultValue:
                'Clears locally-saved preferences (theme, language, dub settings) and reloads. Your voices, projects and generated audio are not affected.',
            })}
          </p>
        ) : (
          <>
            <div className="mb-[var(--space-4)] grid grid-cols-1 gap-[var(--space-2)] @min-[620px]/settings:grid-cols-2">
              {TIERS.map((tier) => {
                const size = selectedBytes(scopes, PRESETS[tier.id]);
                const on = activePreset === tier.id;
                return (
                  <label
                    key={tier.id}
                    className={`flex cursor-pointer items-start gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] border p-[var(--space-4)] transition-colors ${
                      on
                        ? 'border-[color-mix(in_srgb,var(--chrome-accent)_32%,transparent)] bg-[var(--chrome-accent-bg)] shadow-[inset_2px_0_0_var(--chrome-accent)]'
                        : 'border-[color-mix(in_srgb,var(--chrome-fg)_7%,transparent)] bg-[var(--chrome-bg)] hover:bg-[var(--chrome-hover-bg)]'
                    }`}
                  >
                    <input
                      type="radio"
                      name="reset-tier"
                      checked={on}
                      onChange={() => setSelected(PRESETS[tier.id])}
                      data-testid={`reset-tier-${tier.id}`}
                      className="mt-1"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-[var(--space-3)]">
                        <span className="[font-family:var(--font-sans)] text-[length:var(--text-md)] text-[var(--chrome-fg)]">
                          {tier.label}
                        </span>
                        <span className="shrink-0 [font-family:var(--font-mono)] text-[length:var(--text-sm)] tabular-nums text-[var(--chrome-fg-muted)]">
                          {tier.id === 'ui' ? '—' : fmtBytes(size)}
                        </span>
                      </span>
                      <span className="block [font-family:var(--font-sans)] text-[length:var(--text-sm)] leading-[1.5] text-[var(--chrome-fg-muted)]">
                        {tier.hint}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>

            <button
              type="button"
              onClick={() => setAdvanced((v) => !v)}
              data-testid="reset-advanced-toggle"
              className="mb-[var(--space-3)] flex items-center gap-[var(--space-1)] border-0 bg-transparent p-0 [font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)] hover:text-[var(--chrome-fg)]"
            >
              <ChevronRight
                size={13}
                className={advanced ? 'rotate-90 transition-transform' : 'transition-transform'}
              />
              {t('settings.reset_advanced', { defaultValue: 'Choose exactly what to remove' })}
            </button>

            {advanced && (
              <div
                className="settings-list-scroll mb-[var(--space-4)] flex max-h-[420px] flex-col gap-[var(--space-2)] overflow-y-auto overscroll-contain rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-bg)] p-[var(--space-2)] [scrollbar-gutter:stable]"
                data-testid="reset-advanced"
                tabIndex={0}
                aria-label={t('settings.reset_advanced', {
                  defaultValue: 'Choose exactly what to remove',
                })}
              >
                {SCOPE_ORDER.map((key) => {
                  const s = byKey[key];
                  const fileScope = !FRONTEND_SCOPES.includes(key);
                  return (
                    <StorageTargetRow
                      key={key}
                      icon={SCOPE_ICONS[key]}
                      label={LABELS[key]}
                      hint={
                        s?.shared
                          ? t('settings.reset_models_shared', {
                              defaultValue:
                                'Shared Hugging Face cache — may hold models other AI tools downloaded.',
                            })
                          : undefined
                      }
                      size={fileScope ? (s?.size_bytes ?? 0) : undefined}
                      share={willFree > 0 && fileScope ? (s?.size_bytes ?? 0) / willFree : 0}
                      checked={selected.includes(key)}
                      onToggle={() => toggle(key)}
                      disabled={Boolean(s && !s.exists && fileScope)}
                      warn={Boolean(s?.shared)}
                      testId={`reset-scope-${key}`}
                    />
                  );
                })}
              </div>
            )}
          </>
        )}

        <Button
          variant="danger"
          size="md"
          leading={<RotateCcw size={13} />}
          disabled={selected.length === 0}
          onClick={() => {
            setTyped('');
            setOpen(true);
          }}
          data-testid="factory-reset-open"
        >
          {t('settings.reset', { defaultValue: 'Reset & remove' })}
        </Button>
      </SettingsSection>

      <Dialog
        open={open}
        onClose={() => !busy && setOpen(false)}
        title={t('settings.reset_confirm_title', { defaultValue: 'Reset VoiceStudio?' })}
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
              disabled={busy || !typedOk}
              onClick={run}
              data-testid="factory-reset-confirm"
            >
              {plan(selected).restart
                ? t('settings.reset_confirm_restart', {
                    defaultValue: 'Remove {{size}} and restart',
                    size: fmtBytes(willFree),
                  })
                : t('settings.reset_confirm', { defaultValue: 'Reset and reload' })}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-[var(--space-4)]">
          <ul className="m-0 list-none p-0" data-testid="reset-summary">
            {selected.map((key) => (
              <li
                key={key}
                className="flex items-baseline justify-between gap-[var(--space-3)] py-[var(--space-1)] [font-family:var(--font-sans)] text-[length:var(--text-md)] text-[var(--chrome-fg)]"
              >
                <span>{LABELS[key]}</span>
                <span className="shrink-0 [font-family:var(--font-mono)] text-[length:var(--text-sm)] tabular-nums text-[var(--chrome-fg-muted)]">
                  {FRONTEND_SCOPES.includes(key) ? '—' : fmtBytes(byKey[key]?.size_bytes ?? 0)}
                </span>
              </li>
            ))}
          </ul>

          {sharedModels && (
            <p className="m-0 flex items-start gap-[var(--space-3)] [font-family:var(--font-sans)] text-[length:var(--text-sm)] leading-[1.6] text-[var(--chrome-fg-muted)]">
              <AlertTriangle
                size={16}
                className="mt-1 shrink-0 text-[var(--chrome-severity-warn)]"
              />
              <span data-testid="reset-shared-warning">
                {t('settings.reset_models_shared_warning', {
                  defaultValue:
                    'The model cache is the standard Hugging Face cache, shared with other AI tools on this machine — removing it may delete models VoiceStudio never downloaded. Everything VoiceStudio needs will download again on next use.',
                })}
              </span>
            </p>
          )}

          {needsTypedConfirm(selected) && (
            <>
              <p className="m-0 flex items-start gap-[var(--space-3)] [font-family:var(--font-sans)] text-[length:var(--text-md)] leading-[1.6] text-[var(--chrome-fg)]">
                <AlertTriangle size={16} className="mt-1 shrink-0 text-[var(--color-danger)]" />
                <span>
                  {t('settings.reset_irreversible', {
                    defaultValue:
                      'Your voice profiles, projects and generated audio will be permanently deleted. This cannot be undone.',
                  })}
                </span>
              </p>
              <label className="flex flex-col gap-[var(--space-2)]">
                <span className="[font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)]">
                  {t('settings.reset_type_to_confirm', {
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
                  data-testid="reset-type-confirm"
                  className="rounded-[var(--radius-md)] [border:1px_solid_var(--chrome-border)] bg-[var(--chrome-hover-bg)] px-[var(--space-3)] py-[var(--space-2)] [font-family:var(--font-mono)] text-[length:var(--text-md)] text-[var(--chrome-fg)] focus:outline-none"
                />
              </label>
            </>
          )}

          {plan(selected).restart && (
            <p className="m-0 [font-family:var(--font-sans)] text-[length:var(--text-sm)] leading-[1.6] text-[var(--chrome-fg-muted)]">
              {t('settings.reset_restart_note', {
                defaultValue:
                  'VoiceStudio will restart its engine to finish. This takes a few seconds.',
              })}
            </p>
          )}
        </div>
      </Dialog>
    </>
  );
}
