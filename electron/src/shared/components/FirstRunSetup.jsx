/**
 * First-run install setup screen.
 *
 * Rendered by BootstrapSplash while the Rust side is parked in the
 * `awaiting_setup` stage — nothing has been downloaded or installed yet.
 * The user picks install mode (installed/portable), storage locations,
 * compute variant, network mirrors and update channel; every chosen
 * directory is live-checked for free space against the minimum the install
 * needs (Rust re-validates on submit — the UI gate is a mirror, not the
 * authority). "Start installation" is the only thing that kicks off the
 * bootstrap.
 *
 * Built on standard shadcn primitives (Button/Input/Select/Progress/Badge)
 * + Tailwind utilities themed by the VoiceStudio palette tokens. The only
 * bespoke CSS left is the breathing-waveform / rise-in keyframes in
 * firstrun.css. All motion honors prefers-reduced-motion; every asset is
 * bundled — a first run may be on a restricted network.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import i18n, { LANGUAGES } from '../i18n';
import { useAppStore } from '../store';
import { Badge, Button, Input, Progress, Select } from '../ui';
import UiScaleControl from './UiScaleControl';

const APP_VERSION = __APP_VERSION__ || '0.0.0';
const GIB = 1024 * 1024 * 1024;

const fmtGB = (bytes) =>
  bytes == null ? '—' : `${(bytes / GIB).toFixed(bytes < 10 * GIB ? 1 : 0)} GB`;

const invoke = async (...args) => {
  const { invoke: tauriInvoke } = await import('@tauri-apps/api/core');
  return tauriInvoke(...args);
};

/** Debounced live probe of one install target (free space / writability). */
function useTargetCheck(path) {
  const [check, setCheck] = useState(null);
  useEffect(() => {
    if (!path) {
      setCheck(null);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const res = await invoke('check_install_target', { path });
        if (!cancelled) setCheck(res);
      } catch {
        if (!cancelled) setCheck(null);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [path]);
  return check;
}

/** Breathing waveform masthead — bar heights are stable per mount. */
function Waveform({ bars = 96 }) {
  const heights = useMemo(
    () =>
      Array.from({ length: bars }, (_, i) => {
        // Deterministic pseudo-random silhouette: layered sines read as speech
        // cadence (syllables + phrase envelope) rather than white noise.
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

/** Capacity meter: how much of the volume's free space this install consumes.
 *  Switches to the danger tone when the install would overflow the disk. */
function CapacityMeter({ need, free }) {
  const ratio = free > 0 ? need / free : 1;
  const pct = Math.min(100, Math.max(3, ratio * 100));
  return (
    <Progress
      value={pct}
      tone={ratio > 1 ? 'danger' : 'brand'}
      size="md"
      className="w-full"
      aria-label={`${fmtGB(need)} / ${fmtGB(free)}`}
    />
  );
}

/** Engraved mono section label with a rule trailing off to the right. */
function SectionLabel({ children }) {
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

/** One first-run section: rise-in stagger + engraved label + content. */
function Section({ title, delay = 0, className = '', children }) {
  return (
    <section className={cn('fr-rise flex flex-col gap-2.5', className)} style={{ '--rise': delay }}>
      <SectionLabel>{title}</SectionLabel>
      {children}
    </section>
  );
}

/** One storage location row: label, path, space readout, Change… picker. */
function StorageRow({ label, desc, path, need, check, onPick }) {
  const { t } = useTranslation();
  const lowSpace = check?.freeBytes != null && check.freeBytes < need;
  const notWritable = check && !check.writable;
  const blocked = lowSpace || notWritable;
  const tight = check?.freeBytes != null && need / check.freeBytes > 0.35;
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md px-3 py-2 transition-colors hover:bg-bg-elev-3',
        blocked &&
          'bg-danger/[0.06] shadow-[inset_2px_0_0_var(--color-danger)] hover:bg-danger/[0.06]',
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0.5" title={desc}>
        <span className="text-sm font-semibold">{label}</span>
        {/* rtl for a start-side ellipsis; <bdi> keeps the path's own order
            (see the preflight detail in SetupWizard.jsx for why). */}
        <code className="truncate font-mono text-[0.66rem] text-fg-muted" title={path} dir="rtl">
          <bdi>{path}</bdi>
        </code>
      </div>
      <div className="flex min-w-[170px] shrink-0 flex-col items-end gap-1">
        {(blocked || tight) && check?.freeBytes != null && (
          <CapacityMeter need={need} free={check.freeBytes} />
        )}
        <span
          className={cn(
            'whitespace-nowrap font-mono text-[0.64rem] tabular-nums text-fg-muted',
            lowSpace && 'font-bold text-danger',
          )}
        >
          {check == null ? (
            t('firstrun.checking', 'checking…')
          ) : notWritable ? (
            t('firstrun.not_writable', 'not writable')
          ) : (
            <>
              {t('firstrun.needs', { size: fmtGB(need), defaultValue: 'needs ~{{size}}' })}
              {' · '}
              {t('firstrun.free', { size: fmtGB(check.freeBytes), defaultValue: '{{size}} free' })}
            </>
          )}
        </span>
      </div>
      {onPick && (
        <Button variant="ghost" size="sm" onClick={onPick}>
          {t('firstrun.change', 'Change…')}
        </Button>
      )}
    </div>
  );
}

/** Arrow-key navigation for a radio group (WAI-ARIA radio pattern):
 *  Left/Up selects the previous enabled option, Right/Down the next.
 *  Selection follows focus, exactly like native radios. */
export function radioGroupNav(e, values, current, select) {
  let delta = 0;
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') delta = 1;
  else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') delta = -1;
  else return;
  e.preventDefault();
  const idx = Math.max(0, values.indexOf(current));
  const next = values[(idx + delta + values.length) % values.length];
  select(next);
}

/** Radio option card — used for install mode, compute and update channel.
 *  Roving tabindex: only the selected option is in the tab order; arrows move
 *  within the group. The leading dot lights when selected. */
function OptionCard({ active, disabled, onSelect, name, desc, badge }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      tabIndex={active ? 0 : -1}
      disabled={disabled}
      title={desc}
      onClick={() => !disabled && onSelect()}
      className={cn(
        'flex flex-col gap-1 rounded-md border px-3 py-2.5 text-left transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        active
          ? 'border-transparent bg-primary/10'
          : 'border-border bg-bg-elev-2 hover:bg-bg-elev-1',
        disabled && 'cursor-not-allowed opacity-40 hover:bg-bg-elev-2',
      )}
    >
      <span className="flex flex-wrap items-center gap-2">
        <span
          className={cn(
            'h-1.5 w-1.5 shrink-0 rounded-full',
            active ? 'bg-primary shadow-[0_0_6px_1px_var(--color-brand-glow)]' : 'bg-fg-subtle/40',
          )}
          aria-hidden="true"
        />
        <span className="text-sm font-semibold">{name}</span>
        {badge && (
          <Badge tone="success" size="xs">
            {badge}
          </Badge>
        )}
      </span>
    </button>
  );
}

/** Fixed caption slot under a radio group: two reserved lines, the active
 *  option's description swaps in — the layout never shifts on selection. */
function GroupCaption({ text }) {
  return (
    <p className="m-0 min-h-[2.4em] text-xs leading-snug text-fg-subtle" aria-live="polite">
      {text}
    </p>
  );
}

/** Mono error block shown for server / submit failures. */
function ErrorBox({ children }) {
  return (
    <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-danger/10 px-3 py-2 font-mono text-[0.66rem] leading-relaxed text-danger shadow-[inset_2px_0_0_var(--color-danger)]">
      {children}
    </pre>
  );
}

export default function FirstRunSetup() {
  const { t } = useTranslation();
  const locale = useAppStore((s) => s.locale);
  const setLocale = useAppStore((s) => s.setLocale);

  const [setup, setSetup] = useState(null); // get_setup_state payload
  const [plan, setPlan] = useState(null); // user's editable choices
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState(null);
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );

  useEffect(() => {
    (async () => {
      try {
        const s = await invoke('get_setup_state');
        if (!mounted.current) return;
        setSetup(s);
        setPlan({
          installMode:
            s.portable.available && s.defaults.installMode === 'portable'
              ? 'portable'
              : 'installed',
          envDir: s.defaults.envDir,
          dataDir: s.defaults.dataDir,
          modelsDir: s.defaults.modelsDir,
          // Where a portable install would go. Seeded from whatever the app
          // currently resolves (default beside the app, or an earlier
          // relocation) so the row shows a real path, never "(default)".
          portableDir: s.portable.baseDir || '',
          region: s.defaults.region,
          updateChannel: s.defaults.updateChannel,
          // Pre-select ROCm only when Rust verified the ROCm userspace is
          // installed (kind === 'rocm'). A bare AMD GPU without the runtime
          // reports kind === 'amd': ROCm stays offered but unselected, since
          // its wheels would silently fall back to CPU on such machines.
          torchVariant: s.hardware?.kind === 'rocm' ? 'rocm' : s.defaults.torchVariant,
          mirrors: { pypiIndex: '', hfEndpoint: '', pythonDownloads: '' },
        });
      } catch (e) {
        if (mounted.current) setServerError(String(e));
      }
    })();
  }, []);

  const portable = plan?.installMode === 'portable';
  const req = setup?.requirements;
  const hw = setup?.hardware;
  const combinedNeed = req ? req.envBytes + req.modelsBytes + req.dataBytes : 0;

  // Live target probes — in portable mode only the anchor folder matters.
  const portableBase = plan?.portableDir || setup?.portable?.baseDir || '';
  const portableDefault = setup?.portable?.defaultDir || '';
  const portableRelocated = Boolean(
    portableBase && portableDefault && portableBase !== portableDefault,
  );
  const portableAnchor = setup?.portable?.anchorDir || '';
  // A folder INSIDE the app's own directory is recorded as a RELATIVE path, so
  // app + folder move as a unit and the mount path may change. Anywhere else
  // only an absolute path can be stored, and the cross-machine promise does not
  // hold — say so rather than imply it (CodeRabbit, #1404).
  const portableTravelsWithApp =
    Boolean(portableAnchor) &&
    portableBase.startsWith(portableAnchor.endsWith('/') ? portableAnchor : `${portableAnchor}/`);
  const envCheck = useTargetCheck(portable ? null : plan?.envDir);
  const dataCheck = useTargetCheck(portable ? null : plan?.dataDir);
  const modelsCheck = useTargetCheck(portable ? null : plan?.modelsDir);
  const portableCheck = useTargetCheck(portable ? portableBase : null);

  // Mirror of the Rust gate: group targets by filesystem, sum requirements,
  // block when any volume falls short or isn't writable.
  const blockers = useMemo(() => {
    if (!plan || !req) return [{ key: 'loading' }];
    const targets = portable
      ? [{ check: portableCheck, need: combinedNeed, label: portableBase }]
      : [
          { check: envCheck, need: req.envBytes, label: plan.envDir },
          { check: dataCheck, need: req.dataBytes, label: plan.dataDir },
          { check: modelsCheck, need: req.modelsBytes, label: plan.modelsDir },
        ];
    if (targets.some((x) => x.check == null)) return [{ key: 'loading' }];
    const out = [];
    for (const { check, label } of targets) {
      if (!check.writable) out.push({ key: 'not_writable', label });
    }
    const byFs = new Map();
    for (const { check, need } of targets) {
      const k = check.fsKey || check.path;
      const cur = byFs.get(k) || { need: 0, free: check.freeBytes };
      cur.need += need;
      cur.free = Math.min(cur.free ?? Infinity, check.freeBytes ?? Infinity);
      byFs.set(k, cur);
    }
    for (const { need, free } of byFs.values()) {
      if (free != null && free < need) out.push({ key: 'space', need, free });
    }
    return out;
  }, [
    plan,
    req,
    portable,
    portableBase,
    combinedNeed,
    envCheck,
    dataCheck,
    modelsCheck,
    portableCheck,
  ]);

  const pickDir = useCallback(
    async (field) => {
      try {
        const { open } = await import('@tauri-apps/plugin-dialog');
        const dir = await open({ directory: true, defaultPath: plan?.[field] || undefined });
        if (typeof dir === 'string' && dir) setPlan((p) => ({ ...p, [field]: dir }));
      } catch (e) {
        console.error('folder pick failed', e);
      }
    },
    [plan],
  );

  const set = useCallback((patch) => setPlan((p) => ({ ...p, ...patch })), []);

  const start = useCallback(async () => {
    if (!plan || submitting) return;
    setSubmitting(true);
    setServerError(null);
    try {
      const clean = (s) => (s && s.trim() ? s.trim() : null);
      await invoke('complete_setup', {
        plan: {
          installMode: plan.installMode,
          portableDir: clean(plan.portableDir),
          envDir: clean(plan.envDir),
          dataDir: clean(plan.dataDir),
          modelsDir: clean(plan.modelsDir),
          region: plan.region,
          locale,
          updateChannel: plan.updateChannel,
          torchVariant: plan.torchVariant,
          mirrors: {
            pypiIndex: clean(plan.mirrors.pypiIndex),
            hfEndpoint: clean(plan.mirrors.hfEndpoint),
            pythonDownloads: clean(plan.mirrors.pythonDownloads),
          },
        },
      });
      // Success: the stage poll in App.jsx leaves `awaiting_setup` and the
      // normal bootstrap progress UI takes over. Nothing to do here.
    } catch (e) {
      if (mounted.current) {
        const message = e instanceof Error ? e.message : String(e);
        setServerError(
          message === 'backend_stop_failed'
            ? t('firstrun.backend_stop_failed')
            : message === 'setup_task_failed'
              ? t('bootstrap.unknown_error')
              : message,
        );
        setSubmitting(false);
      }
    }
  }, [plan, submitting, locale, t]);

  if (!setup || !plan) {
    return (
      <div className="absolute inset-0 z-[9999] flex flex-col items-center justify-center bg-bg px-6 font-sans text-fg">
        <UiScaleControl />
        {serverError ? (
          <ErrorBox>{serverError}</ErrorBox>
        ) : (
          <div className="text-sm text-fg-muted">{t('firstrun.loading', 'Preparing setup…')}</div>
        )}
      </div>
    );
  }

  const blocked = blockers.length > 0;
  const spaceBlocker = blockers.find((b) => b.key === 'space');
  // The full machine identity — OS/distro · arch · GPU · CPU · RAM — the
  // exact matrix cell this install is for (and what bug reports cite).
  const hwLine = hw
    ? [
        [hw.osName, hw.arch].filter(Boolean).join(' '),
        hw.gpu,
        hw.cpuCores ? `${hw.cpuCores}×CPU` : null,
        hw.ramGb ? `${hw.ramGb} GB RAM` : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : null;
  // ROCm wheels are Linux-only — never offer a choice that can't work on
  // this platform (Rust clamps it server-side too).
  const rocmAvailable = setup.os === 'linux';

  return (
    <div className="absolute inset-0 z-[9999] flex flex-col items-center overflow-hidden bg-bg px-6 pt-12 font-sans text-fg">
      <div className="flex w-full max-w-[1100px] flex-1 flex-col">
        <div className="flex flex-1 flex-col gap-5 overflow-y-auto pb-4">
          {/* ── Masthead: waveform + headline + language/region ──────────── */}
          <header
            className="fr-rise flex flex-col gap-3 pb-1"
            style={{ '--rise': 0 }}
            data-tauri-drag-region
          >
            <Waveform />
            {/* Journey rail: this page is stage 1 of the install flow. */}
            <JourneyRail active="setup" t={t} />
            <div className="mt-2 flex flex-wrap items-end justify-between gap-6">
              <div className="min-w-0">
                {/* Version rides beside the app name — same masthead across all
                    three first-run acts (setup → install → models & engines), so
                    a screenshot from any of them identifies the build. */}
                <div className="flex flex-wrap items-baseline gap-2.5">
                  <h1 className="m-0 font-serif text-[clamp(1.6rem,3vw,2.2rem)] font-semibold leading-tight tracking-tight">
                    {t('firstrun.title', 'Set up VoiceStudio')}
                  </h1>
                  <span className="font-mono text-[0.62rem] tracking-[0.14em] text-fg-subtle">
                    v{APP_VERSION}
                  </span>
                </div>
                <p className="mt-1.5 max-w-[58ch] text-sm leading-snug text-fg-muted">
                  {t(
                    'firstrun.subtitle',
                    "Nothing's installed yet — review where everything goes, then start. Change it later in Settings.",
                  )}
                </p>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-2">
                {/* Language + download region: the two "where am I" choices. */}
                <div className="flex items-center gap-2">
                  <UiScaleControl />
                  <Select
                    size="sm"
                    value={locale}
                    onChange={(e) => {
                      setLocale(e.target.value);
                      i18n.changeLanguage(e.target.value);
                    }}
                    aria-label={t('firstrun.language', 'Language')}
                  >
                    {LANGUAGES.map((l) => (
                      <option key={l.code} value={l.code}>
                        {l.label}
                      </option>
                    ))}
                  </Select>
                  <Select
                    size="sm"
                    value={plan.region}
                    onChange={(e) => set({ region: e.target.value })}
                    aria-label={t('firstrun.region_label', 'Download region')}
                  >
                    <option value="auto">{t('bootstrap.auto_detect', 'Auto-detect')}</option>
                    <option value="global">
                      {t('bootstrap.region_global', 'Global (direct)')}
                    </option>
                    <option value="china">{t('bootstrap.region_china', 'China (mirror)')}</option>
                    <option value="russia">
                      {t('bootstrap.region_russia', 'Russia (mirror)')}
                    </option>
                    <option value="restricted">
                      {t('bootstrap.region_restricted', 'Restricted (mirror)')}
                    </option>
                  </Select>
                </div>
                <details className="text-right">
                  <summary className="cursor-pointer select-none font-mono text-[0.62rem] uppercase tracking-wide text-fg-muted hover:text-fg">
                    {t('firstrun.mirrors_title', 'Custom mirrors (advanced)')}
                  </summary>
                  <div className="mt-2 grid min-w-[320px] gap-2 text-left">
                    {[
                      [
                        'pypiIndex',
                        t('firstrun.mirror_pypi', 'PyPI index URL'),
                        'https://mirrors.aliyun.com/pypi/simple/',
                      ],
                      [
                        'hfEndpoint',
                        t('firstrun.mirror_hf', 'Hugging Face endpoint'),
                        'https://hf-mirror.com',
                      ],
                      [
                        'pythonDownloads',
                        t('firstrun.mirror_python', 'Python downloads mirror'),
                        'https://gh-proxy.com/…',
                      ],
                    ].map(([field, label, ph]) => (
                      <label key={field} className="flex flex-col gap-1">
                        <span className="font-mono text-[0.6rem] uppercase tracking-wide text-fg-muted">
                          {label}
                        </span>
                        <Input
                          size="sm"
                          type="url"
                          placeholder={ph}
                          value={plan.mirrors[field]}
                          onChange={(e) =>
                            set({ mirrors: { ...plan.mirrors, [field]: e.target.value } })
                          }
                        />
                      </label>
                    ))}
                  </div>
                </details>
              </div>
            </div>
          </header>

          {/* ── Wide deck: storage rail (left) + decision rail (right) ────── */}
          <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[7fr_5fr]">
            <div className="flex min-w-0 flex-col gap-5">
              <Section title={t('firstrun.mode_title', 'Install mode')} delay={1}>
                <div
                  className="grid grid-cols-1 gap-2.5 sm:grid-cols-2"
                  role="radiogroup"
                  aria-label={t('firstrun.mode_title', 'Install mode')}
                  onKeyDown={(e) =>
                    radioGroupNav(
                      e,
                      setup.portable.available ? ['installed', 'portable'] : ['installed'],
                      plan.installMode,
                      (v) => set({ installMode: v }),
                    )
                  }
                >
                  <OptionCard
                    active={!portable}
                    onSelect={() => set({ installMode: 'installed' })}
                    name={t('firstrun.mode_installed', 'Installed')}
                    desc={t(
                      'firstrun.mode_installed_desc',
                      'Standard system folders. Recommended.',
                    )}
                  />
                  <OptionCard
                    active={portable}
                    disabled={!setup.portable.available}
                    onSelect={() => set({ installMode: 'portable' })}
                    name={t('firstrun.mode_portable', 'Portable')}
                    desc={
                      setup.portable.available
                        ? t(
                            'firstrun.mode_portable_desc',
                            'Everything lives in one folder next to the app — move it to another disk or machine as a unit.',
                          )
                        : t(
                            'firstrun.mode_portable_unavailable',
                            'Unavailable: the folder next to the app is not writable.',
                          )
                    }
                  />
                </div>
                <GroupCaption
                  text={
                    portable
                      ? t(
                          'firstrun.mode_portable_desc',
                          'Everything lives in one folder next to the app — move it to another disk or machine as a unit.',
                        )
                      : t('firstrun.mode_installed_desc', 'Standard system folders. Recommended.')
                  }
                />
              </Section>

              <Section title={t('firstrun.storage_title', 'Storage')} delay={2}>
                {portable ? (
                  <>
                    <StorageRow
                      label={t('firstrun.portable_folder', 'Portable folder')}
                      desc={t(
                        'firstrun.portable_folder_desc',
                        'App environment, models, and your voice data — one folder, fully movable.',
                      )}
                      path={portableBase}
                      need={combinedNeed}
                      check={portableCheck}
                      onPick={() => pickDir('portableDir')}
                    />
                    {portableRelocated && (
                      <GroupCaption
                        text={
                          setup.portable.anchorWritable && portableTravelsWithApp
                            ? t(
                                'firstrun.portable_moved',
                                'Recorded beside the app as a relative path, so moving the app and this folder together — even to another machine or drive letter — keeps the install working.',
                              )
                            : t(
                                'firstrun.portable_moved_machine_bound',
                                'This exact location is remembered on this machine. If the app or this folder moves, or you use another machine, you will have to point the app at it again.',
                              )
                        }
                      />
                    )}
                  </>
                ) : (
                  <>
                    <StorageRow
                      label={t('firstrun.env_dir', 'App environment')}
                      desc={t('firstrun.env_dir_desc', 'Python runtime and AI libraries.')}
                      path={plan.envDir}
                      need={req.envBytes}
                      check={envCheck}
                      onPick={() => pickDir('envDir')}
                    />
                    <StorageRow
                      label={t('firstrun.data_dir', 'Voice data & projects')}
                      desc={t(
                        'firstrun.data_dir_desc',
                        'Your voices, dubs, outputs and project database.',
                      )}
                      path={plan.dataDir}
                      need={req.dataBytes}
                      check={dataCheck}
                      onPick={() => pickDir('dataDir')}
                    />
                    <StorageRow
                      label={t('firstrun.models_dir', 'Model cache')}
                      desc={t(
                        'firstrun.models_dir_desc',
                        'Downloaded AI models — the largest and most relocatable part.',
                      )}
                      path={plan.modelsDir}
                      need={req.modelsBytes}
                      check={modelsCheck}
                      onPick={() => pickDir('modelsDir')}
                    />
                  </>
                )}
              </Section>
            </div>

            <div className="flex min-w-0 flex-col gap-5">
              <Section title={t('firstrun.compute_title', 'Compute')} delay={2}>
                {hwLine && (
                  <div className="flex min-w-0 items-center gap-2 pb-1" title={hwLine}>
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full bg-success shadow-[0_0_6px_1px_color-mix(in_srgb,var(--color-success)_60%,transparent)] fr-pulse"
                      aria-hidden="true"
                    />
                    <span className="shrink-0 font-mono text-[0.58rem] uppercase tracking-wide text-fg-muted">
                      {t('firstrun.compute_detected', { defaultValue: 'Detected' })}
                    </span>
                    <span className="truncate font-mono text-[0.66rem] tabular-nums text-fg">
                      {hwLine}
                    </span>
                  </div>
                )}
                <div
                  className="grid gap-2.5"
                  role="radiogroup"
                  aria-label={t('firstrun.compute_title', 'Compute')}
                  onKeyDown={(e) =>
                    radioGroupNav(
                      e,
                      rocmAvailable ? ['auto', 'rocm'] : ['auto'],
                      plan.torchVariant,
                      (v) => set({ torchVariant: v }),
                    )
                  }
                >
                  <OptionCard
                    active={plan.torchVariant === 'auto'}
                    onSelect={() => set({ torchVariant: 'auto' })}
                    name={t('firstrun.compute_auto', 'Auto (NVIDIA CUDA / Apple MPS / CPU)')}
                    desc={t(
                      'firstrun.compute_auto_desc',
                      'Best backend picked at runtime — CUDA on NVIDIA, MPS on Apple Silicon, else CPU.',
                    )}
                    badge={
                      hw?.kind === 'cuda' || hw?.kind === 'mps'
                        ? t('firstrun.compute_match', { defaultValue: 'matches this machine' })
                        : null
                    }
                  />
                  {rocmAvailable && (
                    <OptionCard
                      active={plan.torchVariant === 'rocm'}
                      onSelect={() => set({ torchVariant: 'rocm' })}
                      name={t('firstrun.compute_rocm', 'AMD GPU (ROCm, Linux)')}
                      desc={t(
                        'firstrun.compute_rocm_desc',
                        'Installs PyTorch ROCm wheels for AMD graphics cards on Linux. Leave on Auto if unsure.',
                      )}
                      badge={
                        hw?.kind === 'rocm'
                          ? t('firstrun.compute_match', { defaultValue: 'matches this machine' })
                          : null
                      }
                    />
                  )}
                </div>
                <GroupCaption
                  text={
                    plan.torchVariant === 'rocm'
                      ? t(
                          'firstrun.compute_rocm_desc',
                          'Installs PyTorch ROCm wheels for AMD graphics cards on Linux. Leave on Auto if unsure.',
                        )
                      : t(
                          'firstrun.compute_auto_desc',
                          'Best backend picked at runtime — CUDA on NVIDIA, MPS on Apple Silicon, else CPU.',
                        )
                  }
                />
              </Section>

              <Section title={t('firstrun.channel_label', 'Update channel')} delay={3}>
                <div
                  className="grid gap-2.5"
                  role="radiogroup"
                  aria-label={t('firstrun.channel_label', 'Update channel')}
                  onKeyDown={(e) =>
                    radioGroupNav(e, ['stable', 'preview'], plan.updateChannel, (v) =>
                      set({ updateChannel: v }),
                    )
                  }
                >
                  <OptionCard
                    active={plan.updateChannel === 'stable'}
                    onSelect={() => set({ updateChannel: 'stable' })}
                    name={t('firstrun.channel_stable', 'Stable')}
                    desc={t(
                      'firstrun.channel_stable_desc',
                      'Tested releases only, after community validation.',
                    )}
                  />
                  <OptionCard
                    active={plan.updateChannel === 'preview'}
                    onSelect={() => set({ updateChannel: 'preview' })}
                    name={t('firstrun.channel_preview', 'Preview (latest main)')}
                    desc={t(
                      'firstrun.channel_preview_desc',
                      'Latest main — new engines and fixes first, occasional rough edges.',
                    )}
                  />
                </div>
                <GroupCaption
                  text={
                    plan.updateChannel === 'preview'
                      ? t(
                          'firstrun.channel_preview_desc',
                          'Latest main — new engines and fixes first, occasional rough edges.',
                        )
                      : t(
                          'firstrun.channel_stable_desc',
                          'Tested releases only, after community validation.',
                        )
                  }
                />
              </Section>
            </div>
          </div>
        </div>

        {/* ── Footer: gate + arm ──────────────────────────────────────────── */}
        <footer
          className="fr-rise flex shrink-0 flex-col gap-2 border-t border-border bg-bg pt-3 pb-6"
          style={{ '--rise': 5 }}
        >
          {serverError && <ErrorBox>{serverError}</ErrorBox>}
          {spaceBlocker && (
            <p className="m-0 text-sm text-danger">
              {t('firstrun.insufficient_space', {
                need: fmtGB(spaceBlocker.need),
                free: fmtGB(spaceBlocker.free),
                defaultValue:
                  'Not enough free space: this layout needs ~{{need}} on one disk, only {{free}} available. Pick a different location.',
              })}
            </p>
          )}
          {blockers.some((b) => b.key === 'not_writable') && (
            <p className="m-0 text-sm text-danger">
              {t(
                'firstrun.blocked_not_writable',
                'A chosen folder is not writable — pick a different location.',
              )}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-4">
            {/* Version moved up beside the app name (masthead) — the footer now
                carries only the download total. */}
            <span className="inline-flex flex-wrap items-baseline gap-2 text-xs tabular-nums text-fg-muted">
              {t('firstrun.total_required', {
                size: fmtGB(combinedNeed),
                defaultValue: 'Total disk needed: ~{{size}} (one-time download on first use)',
              })}
            </span>
            <Button variant="primary" disabled={blocked || submitting} onClick={start}>
              {submitting
                ? t('firstrun.starting', 'Starting…')
                : t('firstrun.start', 'Start installation')}
            </Button>
          </div>
          {/* The product's whole thesis, said where the user decides. */}
          <p className="m-0 text-xs text-fg-subtle">
            {t(
              'firstrun.trust_line',
              'Privacy first: your voices, recordings and projects never leave this machine — no account, no cloud processing.',
            )}
          </p>
        </footer>
      </div>
    </div>
  );
}

/** Three-stage breadcrumb shared by the setup + install acts. */
export function JourneyRail({ active, t }) {
  const stages = [
    ['setup', t('firstrun.stage_setup', 'Setup')],
    ['installing', t('firstrun.installing_title', 'Installing')],
    ['models', t('firstrun.stage_models', 'Models & engines')],
  ];
  const activeIdx = stages.findIndex(([id]) => id === active);
  return (
    <nav
      className="flex flex-wrap items-center gap-x-5 gap-y-2"
      aria-label={t('firstrun.title', 'Set up VoiceStudio')}
    >
      {stages.map(([id, label], i) => {
        const isActive = i === activeIdx;
        const isDone = i < activeIdx;
        return (
          <span
            key={id}
            className={cn(
              'inline-flex items-center gap-1.5 font-mono text-[0.62rem] font-semibold uppercase tracking-[0.14em]',
              isActive ? 'text-fg' : isDone ? 'text-fg-muted' : 'text-fg-subtle/60',
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
          </span>
        );
      })}
    </nav>
  );
}
