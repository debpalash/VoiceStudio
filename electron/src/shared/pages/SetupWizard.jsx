import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader, RotateCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useSetupStatus, usePreflight } from '../api/hooks';
import { apiJson } from '../api/client';
import AnalyticsConsentCard from '../components/AnalyticsConsentCard';
import WizardLibrary from '../components/WizardLibrary';
import MediaEngineCard from '../components/MediaEngineCard';
import MirrorRescue from '../components/MirrorRescue';
import HfTokenCard from '../components/HfTokenCard';
import DictationDemo from '../components/DictationDemo';
import PermissionChecks from '../components/PermissionChecks';
import { APP_VERSION } from '../utils/appVersion';
import { Button } from '../ui';
import UiScaleControl from '../components/UiScaleControl';

// macOS convention: double-click the title-bar drag region to toggle zoom.
const doubleClickMaximize = async () => {
  try {
    if (!('__TAURI_INTERNALS__' in window)) return;
    const { getCurrentWindow } = await import('@tauri-apps/api/window');
    getCurrentWindow().toggleMaximize();
  } catch {
    /* non-tauri preview — ignore */
  }
};

/** Shorten an absolute path for display: /Users/foo/.cache/x → ~/.cache/x */
function shortenPath(p) {
  if (!p) return '~/.cache/huggingface';
  try {
    const home = p.match(/^(\/Users\/[^/]+|\/home\/[^/]+|C:\\Users\\[^\\]+)/)?.[0];
    if (home) return p.replace(home, '~');
  } catch {
    /* fallthrough */
  }
  return p;
}

/** Open a path in the OS file manager (Tauri only, no-op on web). */
async function revealPath(path) {
  try {
    if (!('__TAURI_INTERNALS__' in window)) return;
    const { revealItemInDir } = await import('@tauri-apps/plugin-opener');
    await revealItemInDir(path);
  } catch {
    /* ignore — probably web preview */
  }
}

/** Whisper waveform — the journey's signature, same as setup + install. */
function Waveform({ bars = 96 }) {
  const heights = useMemo(
    () =>
      Array.from({ length: bars }, (_, i) => {
        const t = i / bars;
        const v = Math.abs(
          Math.sin(t * Math.PI * 7.3) * 0.55 +
            Math.sin(t * Math.PI * 2.1 + 1.2) * 0.3 +
            Math.sin(t * Math.PI * 17.0 + 0.4) * 0.15,
        );
        return 0.18 + v * 0.82;
      }),
    [bars],
  );
  return (
    <div className="fr-wave" aria-hidden="true">
      {heights.map((h, i) => (
        <span
          key={i}
          className="fr-wave__bar"
          style={{ '--h': h, '--d': `${(i * 73) % 1400}ms` }}
        />
      ))}
    </div>
  );
}

const CHECK_LED = {
  pass: 'bg-success shadow-[0_0_5px_1px_color-mix(in_srgb,var(--color-success)_50%,transparent)]',
  warn: 'bg-warn shadow-[0_0_5px_1px_color-mix(in_srgb,var(--color-warn)_50%,transparent)]',
  fail: 'bg-danger shadow-[0_0_5px_1px_color-mix(in_srgb,var(--color-danger)_50%,transparent)]',
};

/* ── Preflight panel — LED check rows ──────────────────────────────────── */

function PreflightPanel({ report, loading, onRecheck }) {
  const { t } = useTranslation();
  if (loading && !report) {
    return (
      <div className="flex items-center gap-2 py-1 text-sm text-fg-muted">
        <Loader className="animate-spin" size={14} /> {t('setup.probing')}
      </div>
    );
  }
  if (!report) return null;
  return (
    <section className="flex flex-col gap-2.5">
      <h2 className="m-0 flex items-center gap-2 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.18em] text-fg-muted">
        <span>{t('setup.system_preflight')}</span>
        <span
          className="h-px flex-1 bg-gradient-to-r from-border-strong to-transparent"
          aria-hidden="true"
        />
        <Button variant="ghost" size="sm" onClick={onRecheck} leading={<RotateCw size={12} />}>
          {t('setup.recheck')}
        </Button>
      </h2>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] items-start gap-x-6 gap-y-2">
        {report.checks.map((c) => (
          <div key={c.id} className="flex items-start gap-2 rounded-md px-2.5 py-2">
            <span
              className={cn(
                'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
                CHECK_LED[c.status] || 'bg-fg-subtle/40',
              )}
              aria-hidden="true"
            />
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="text-sm font-semibold">{c.label}</span>
              {/* dir="rtl" moves the ellipsis to the START of the line so a long
                  path keeps its tail visible. The text itself must stay in an
                  isolate: in an RTL paragraph the bidi algorithm places a
                  LEADING run of numbers or neutrals (a digit, a "/") at the
                  visual right edge, so "48.0 GB total" renders "GB total 48.0".
                  <bdi> is dir="auto", so the run is ordered by its own content
                  and only the ellipsis side follows the rtl box. */}
              <span className="truncate text-xs text-fg-muted" dir="rtl" title={c.detail}>
                <bdi>{c.detail}</bdi>
              </span>
              {c.fix && c.status !== 'pass' && (
                <span
                  className={cn(
                    'text-xs leading-snug',
                    c.status === 'fail' ? 'text-danger' : 'text-warn',
                  )}
                >
                  → {c.fix}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── LED stepper rail ──────────────────────────────────────────────────── */

function StepperNav({ step, maxUnlockedStep, onStep, stepLabels }) {
  const { t } = useTranslation();
  return (
    <nav className="flex flex-wrap items-center gap-x-4 gap-y-2" data-tauri-drag-region>
      {stepLabels.map((label, i) => {
        const isActive = step === i;
        const isDone = step > i;
        const locked = i > maxUnlockedStep;
        return (
          <button
            key={label}
            type="button"
            // The rail mirrors the continue buttons' gates: jumping past an
            // unmet gate (preflight, required models) would let "Enter studio"
            // clear setupNeeded without the checks ever passing.
            disabled={locked}
            onClick={() => !locked && onStep(i)}
            aria-current={isActive ? 'step' : undefined}
            aria-label={
              t('setup.step_aria', {
                num: i + 1,
                label,
                defaultValue: 'Step {{num}}: {{label}}',
              }) + (isDone ? ` (${t('setup.step_completed', 'completed')})` : '')
            }
            className={cn(
              'inline-flex appearance-none items-center gap-1.5 border-0 bg-transparent p-0 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.14em] transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              locked && 'cursor-not-allowed',
              isActive ? 'text-fg' : isDone ? 'text-fg-muted hover:text-fg' : 'text-fg-subtle/60',
            )}
          >
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                isActive
                  ? 'bg-primary shadow-[0_0_6px_1px_var(--color-brand-glow)]'
                  : isDone
                    ? 'bg-success'
                    : 'bg-fg-subtle/40',
              )}
              aria-hidden="true"
            />
            {label}
          </button>
        );
      })}
    </nav>
  );
}

/** Section heading with engraved label + rule. */
function SectionHead({ children }) {
  return (
    <h2 className="m-0 flex items-center gap-2 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.18em] text-fg-muted">
      <span>{children}</span>
      <span
        className="h-px flex-1 bg-gradient-to-r from-border-strong to-transparent"
        aria-hidden="true"
      />
    </h2>
  );
}

/* ── Main wizard component ─────────────────────────────────────────────── */

/**
 * First-run / "no models installed" gate — the final act of the first-run
 * journey (setup → install → models/engines). Rendered in the same shadcn
 * design system as the install splash so the handoff is seamless.
 *
 * Flow (step ids — the consent step only exists when this build ships an
 * analytics destination AND the user has never been asked):
 *   system     — /setup/preflight results
 *   models     — required models (gates continue) + engines + the optional
 *                tail in one act
 *   consent    — first-run analytics ask: two equal-weight Yes/No buttons.
 *                Never defaults to yes; skipping the wizard (or jumping past
 *                via the rail) = not prompted = analytics stays OFF.
 *   dictation  — guided demo, then "Enter studio"
 */
export default function SetupWizard({ onReady }) {
  const { t } = useTranslation();
  const [step, setStep] = useState(0);

  // Whether to insert the analytics consent step. Resolved once at mount
  // (the user is on step 0 when this lands, so indices never shift underfoot):
  // only when the build CAN send and the user was never asked. Since #1193
  // every build has a destination (in-repo default token), so source builds
  // get this same ask; skipping the wizard still means analytics stays off.
  const [askConsent, setAskConsent] = useState(false);
  useEffect(() => {
    let cancelled = false;
    apiJson('/api/settings/analytics')
      .then((s) => {
        if (!cancelled && s?.available && !s?.prompted && !s?.opted_in) setAskConsent(true);
      })
      .catch(() => {
        /* backend unreachable → no consent step; the one-time banner asks later */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const stepIds = useMemo(
    () =>
      askConsent ? ['system', 'models', 'consent', 'dictation'] : ['system', 'models', 'dictation'],
    [askConsent],
  );
  const stepId = stepIds[Math.min(step, stepIds.length - 1)];

  // TanStack Query — shared cache, auto-refetch on step 2 (models)
  const setupQuery = useSetupStatus();
  const preQuery = usePreflight();
  const status = setupQuery.data ?? null;
  const pre = preQuery.data ?? null;
  const preLoading = preQuery.isLoading;

  // Poll setup status every 4s while on Models step
  useEffect(() => {
    if (stepId !== 'models') return;
    const iv = setInterval(() => setupQuery.refetch(), 4000);
    return () => clearInterval(iv);
  }, [stepId, setupQuery]);

  const recheckPreflight = useCallback(() => {
    preQuery.refetch();
  }, [preQuery]);

  const modelsReady = !!status?.models_ready;
  const preflightOk = !!pre?.ok;
  // Offer the mirror quick-pick whenever the HF endpoint probe didn't pass —
  // the wizard is the only surface these users can reach (Settings is gated
  // behind setup), so the escape hatch must live here.
  const networkDown = (pre?.checks || []).some((c) => c.id === 'network' && c.status !== 'pass');

  const cachePath = status?.hf_cache_dir || '~/.cache/huggingface';

  const STEP_SUBTITLES = {
    system: t('setup.system_check_desc'),
    models: t('setup.install_models_desc'),
    consent: t(
      'consent.desc',
      'Anonymous, content-free usage stats that help us fix bugs faster — entirely optional, off by default.',
    ),
    dictation: t(
      'setup.try_dictation_desc',
      'Press your shortcut once so you know dictation works before you finish setup.',
    ),
  };
  const STEP_LABELS = {
    system: t('setup.system_check'),
    models: t('firstrun.stage_models', 'Models & engines'),
    consent: t('consent.step_label', 'Improve VoiceStudio'),
    dictation: t('setup.dictation_step_label', 'Dictation'),
  };

  return (
    // `absolute`, not `fixed`: this mounts inside `.app-wizard-wrap`, and a
    // fixed root would ignore that box and lay its pinned footer out against
    // the viewport — putting Continue and the HF-token card behind the status
    // bar, off the bottom of the window. `pb-4` keeps the pinned row off the
    // very edge now that it really is the last thing on screen.
    <div className="absolute inset-0 flex flex-col items-center overflow-hidden bg-bg px-6 pb-4 pt-12 font-sans text-fg">
      {/* min-h-0 is THE fix for the pushed-off-screen Continue button: without
          it this wrapper's automatic minimum height is its CONTENT height (per
          flex spec, min-height:auto on a column-flex item resolves to
          min-content, and the step's flex-basis:auto contributes its full
          content) — so the wrapper silently grows past the root, the root's
          overflow-hidden clips everything below the window, and no inner
          min-h-0/overflow-y-auto clamp further down can ever engage. Measured
          in a real engine (Chromium): footer at y=3078 in a 900px window
          without this class; y=884 and the list scrolling with it. */}
      <div className="flex w-full min-h-0 max-w-[1100px] flex-1 flex-col">
        {/* ── Masthead: identical identity to setup + install acts ────────── */}
        <header
          className="fr-rise flex flex-col gap-3 pb-1"
          style={{ '--rise': 0 }}
          data-tauri-drag-region
          onDoubleClick={doubleClickMaximize}
        >
          <Waveform />
          <div className="mt-2 flex flex-wrap items-end justify-between gap-6">
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-2.5" data-tauri-drag-region>
                <h1
                  className="m-0 font-serif text-[clamp(1.6rem,3vw,2.2rem)] font-semibold leading-tight tracking-tight"
                  data-tauri-drag-region
                >
                  VoiceStudio
                </h1>
                {/* Same identity mark as the install splash footer. */}
                <span
                  className="font-mono text-[0.62rem] tracking-[0.14em] text-fg-subtle"
                  data-tauri-drag-region
                >
                  v{APP_VERSION}
                </span>
              </div>
              <p className="mt-1.5 text-sm leading-snug text-fg-muted" data-tauri-drag-region>
                {STEP_SUBTITLES[stepId]}
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-2">
              <UiScaleControl />
              <StepperNav
                step={step}
                maxUnlockedStep={preflightOk ? (modelsReady ? stepIds.length - 1 : 1) : 0}
                onStep={setStep}
                stepLabels={stepIds.map((id) => STEP_LABELS[id])}
              />
            </div>
          </div>
        </header>

        {/* System check — first thing a user sees: the probe auto-runs. */}
        {stepId === 'system' && (
          <div className="flex min-h-0 flex-auto flex-col gap-3" key="step-0">
            <div className="fr-rise min-h-0 flex-1 overflow-y-auto" style={{ '--rise': 1 }}>
              <PreflightPanel report={pre} loading={preLoading} onRecheck={recheckPreflight} />
              {/* OS permissions (mic + macOS Accessibility) — advisory rows
                  that never gate Continue; renders nothing outside Tauri. */}
              <PermissionChecks />
              {/* Invisible when the media engine is ready; a quiet progress
                  line while the backend fetches its own bundled build; an
                  actionable card only on failure. */}
              <MediaEngineCard />
              {networkDown && <MirrorRescue onApplied={recheckPreflight} />}
            </div>
            <div
              className="fr-rise flex shrink-0 items-center justify-between gap-4 border-t border-border pt-3"
              style={{ '--rise': 2 }}
            >
              <span />
              <Button
                variant="primary"
                onClick={() => setStep(1)}
                disabled={!preflightOk}
                title={preflightOk ? '' : t('setup.resolve_blockers')}
              >
                {preflightOk
                  ? pre?.has_warnings
                    ? t('setup.continue_warn')
                    : t('setup.continue_ok')
                  : t('setup.continue_blocked')}
              </Button>
            </div>
          </div>
        )}

        {/* Models & engines — ONE unified list: every installable is a
            row of the same grammar (LED · name · chip · size · action). */}
        {stepId === 'models' && (
          <div className="flex min-h-0 flex-auto flex-col gap-3" key="step-1">
            <section
              className="fr-rise flex min-h-0 flex-1 flex-col gap-2.5"
              style={{ '--rise': 1 }}
            >
              <SectionHead>{t('firstrun.stage_models', 'Models & engines')}</SectionHead>
              {/* The list scrolls; the Continue footer below stays pinned —
                  same pattern as the System step. Without this clamp the
                  curated rows push the footer below the viewport. */}
              <div className="min-h-0 flex-1 overflow-y-auto">
                <WizardLibrary />
              </div>
              {!modelsReady && status?.missing?.length > 0 && (
                <p className="m-0 text-xs leading-snug text-warn">
                  {t('setup.still_needed')} {status.missing.map((m) => m.label).join(', ')}
                </p>
              )}
            </section>
            {/* Pinned next to Continue (not buried in the scrolling model list)
                so it's visible without scrolling — drop a token right by the
                action. */}
            <HfTokenCard className="shrink-0" />
            <div
              className="fr-rise flex shrink-0 items-center justify-between gap-4 border-t border-border pt-3"
              style={{ '--rise': 2 }}
            >
              <Button variant="ghost" size="sm" onClick={() => setStep(0)}>
                ← {t('setup.back')}
              </Button>
              <Button
                variant="primary"
                onClick={() => setStep(step + 1)}
                disabled={!modelsReady}
                title={modelsReady ? '' : t('setup.install_required_models')}
              >
                {modelsReady ? t('setup.models_ready') : t('setup.waiting_models')}
              </Button>
            </div>
          </div>
        )}

        {/* Analytics consent — asked exactly once, only in builds that ship a
            destination. Both buttons advance; there is no "yes by default",
            and jumping past via the rail (skipping) leaves analytics OFF. */}
        {stepId === 'consent' && (
          <div className="flex min-h-0 flex-auto flex-col gap-3" key="step-consent">
            <section
              className="fr-rise flex min-h-0 flex-1 flex-col gap-2.5"
              style={{ '--rise': 1 }}
            >
              <div className="min-h-0 flex-1 overflow-y-auto pt-2">
                <AnalyticsConsentCard onDone={() => setStep(step + 1)} />
              </div>
            </section>
            <div
              className="fr-rise flex shrink-0 items-center justify-between gap-4 border-t border-border pt-3"
              style={{ '--rise': 2 }}
            >
              <Button variant="ghost" size="sm" onClick={() => setStep(step - 1)}>
                ← {t('setup.back')}
              </Button>
              <span />
            </div>
          </div>
        )}

        {/* Dictation — guided walkthrough. Skippable. */}
        {stepId === 'dictation' && (
          <div className="flex min-h-0 flex-auto flex-col gap-3" key="step-2">
            <section
              className="fr-rise flex min-h-0 flex-1 flex-col gap-2.5"
              style={{ '--rise': 1 }}
            >
              <div className="max-h-[min(58vh,640px)] min-w-0 overflow-y-auto rounded-lg">
                <DictationDemo />
              </div>
            </section>
            <div
              className="fr-rise flex shrink-0 items-center justify-between gap-4 border-t border-border pt-3"
              style={{ '--rise': 2 }}
            >
              <Button variant="ghost" size="sm" onClick={() => setStep(step - 1)}>
                ← {t('setup.back')}
              </Button>
              <div className="flex items-center gap-2">
                <Button variant="ghost" size="sm" onClick={onReady}>
                  {t('common.cancel')}
                </Button>
                <Button variant="primary" onClick={onReady}>
                  {t('setup.enter_studio')}
                </Button>
              </div>
            </div>
          </div>
        )}

        {!status && step > 0 && (
          <div className="flex items-center gap-2 py-1 text-sm text-fg-muted">
            <Loader className="animate-spin" size={14} /> {t('setup.checking')}
          </div>
        )}

        <footer className="shrink-0 py-3">
          <span className="inline-flex flex-wrap items-center gap-2 text-xs text-fg-muted">
            {t('setup.footer_downloads')}
            <span aria-hidden="true">·</span>
            {t('setup.cache_label', 'Model cache')}{' '}
            <code className="font-mono text-fg-subtle">{shortenPath(cachePath)}</code>
            {'__TAURI_INTERNALS__' in window && cachePath && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => revealPath(cachePath)}
                title={t('setup.open_finder')}
              >
                {t('setup.open')}
              </Button>
            )}
          </span>
        </footer>
      </div>
    </div>
  );
}
