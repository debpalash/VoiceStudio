import React, { useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import {
  Scale,
  Fingerprint,
  Wand2,
  Film,
  Lock,
  BookOpen,
  BookMarked,
  LibraryBig,
  FileText,
  HardDrive,
  Download,
  ArrowRight,
  ArrowRightLeft,
  AudioLines,
} from 'lucide-react';
import { API } from '../api/client';
import { useAppStore } from '../store';
import ReadinessChecklist from '../components/ReadinessChecklist';
import LaunchpadDeck from '../components/LaunchpadDeck';
import useShellNarrow from '../hooks/useShellNarrow';
import signalField from '../assets/signal-field.webp';

// Shared utility-class strings for the Launchpad project/section rows. Migrated
// from the former `.lp-project-card`/`.lp-section-title`/`.proj-*` global rules
// (P4 shadcn/Tailwind pass). Defined once here so the four card instances stay
// in lockstep; Tailwind's scanner picks the literals up from this file.
//
// Rows sit one step quieter than the feature tiles above them: invisible at
// rest, a faint surface on hover. Borders are deliberately absent — every
// `--chrome-border` token is zeroed app-wide, so structure comes from spacing.
const projCard =
  'group min-w-0 border-0 bg-[var(--chrome-hover-bg)] rounded-lg min-h-[64px] py-[10px] px-[12px] [transition:background_var(--dur-base)_var(--ease-out)] flex items-center gap-[10px] hover:bg-[color-mix(in_srgb,var(--chrome-fg)_5%,transparent)]';
const collectionGrid =
  'grid [grid-template-columns:repeat(auto-fill,minmax(min(100%,240px),1fr))] gap-2';
const projIcon = 'w-[22px] h-[22px] flex items-center justify-center shrink-0';
// The dubbing rows are the one place that holds a real image, so they keep a
// 30px plate (tinted, clipped) instead of the bare glyph slot above.
const projThumb =
  'w-[30px] h-[30px] rounded-[var(--chrome-radius-pill)] flex items-center justify-center shrink-0 overflow-hidden bg-[color-mix(in_srgb,#fe8019_9%,transparent)]';
const projInfo = 'flex-1 min-w-0';
const projName =
  '[font-family:var(--font-sans)] text-[0.775rem] font-medium text-[color:var(--chrome-fg)] [letter-spacing:-0.005em] whitespace-nowrap overflow-hidden text-ellipsis';
const projMeta =
  '[font-family:var(--chrome-font-mono)] text-[0.62rem] text-[color:var(--chrome-fg-dim)] mt-[3px] font-normal whitespace-nowrap overflow-hidden text-ellipsis';
// Keep Open visible for touch, keyboard, and pointer users alike.
const projAction =
  '[font-family:var(--font-sans)] text-[0.7rem] font-medium border-0 py-[3px] px-[10px] rounded-[var(--chrome-radius-pill)] bg-transparent text-[color:var(--chrome-fg-muted)] cursor-pointer min-h-9 opacity-100 [transition:opacity_var(--dur-base)_var(--ease-out),background_var(--dur-fast),color_var(--dur-fast)] shrink-0 whitespace-nowrap [letter-spacing:0.01em] group-hover:opacity-100 focus-visible:opacity-100 hover:bg-[color-mix(in_srgb,var(--chrome-fg)_8%,transparent)] hover:text-[color:var(--chrome-fg)]';
// Section divider label ("Cloned Voices" etc.) — the trailing hairline lives on
// an ::after pseudo, expressed via the after: variant. A single rule that fades
// out to the right; the old dotted stipple read as texture, not structure.
const sectionTitle =
  "[font-family:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] font-medium uppercase [letter-spacing:var(--chrome-label-track)] text-[color:var(--chrome-fg-dim)] m-0 mb-[10px] flex items-center gap-[9px] after:content-[''] after:flex-1 after:h-px after:[background-image:linear-gradient(90deg,color-mix(in_srgb,var(--chrome-fg)_14%,transparent),transparent)]";

function DubThumb({ jobId, fallback }) {
  const [failed, setFailed] = useState(false);
  if (!jobId || failed) return fallback;
  return (
    <img
      src={`${API}/dub/thumb/${jobId}`}
      alt=""
      onError={() => setFailed(true)}
      loading="lazy"
      className="w-full h-full object-cover [border-radius:inherit] block"
    />
  );
}

// The feature-card tile lives in LaunchpadDeck so every shell width gets the
// same clear entry points.

export default function Launchpad({
  profiles,
  studioProjects,
  exportHistory = [],
  setMode,
  setIsCompareModalOpen,
  handleSelectProfile,
  loadProject,
}) {
  const { t } = useTranslation();
  // Clone/Design are no longer separate navigation modes — both cards open
  // the unified Voice ('studio') workspace preset to the matching method.
  const setDefineMethod = useAppStore((s) => s.setDefineMethod);
  const openStudio = (method) => {
    setMode('studio');
    setDefineMethod(method);
  };
  const cloneProfiles = profiles.filter((p) => !p.instruct);
  const designProfiles = profiles.filter((p) => !!p.instruct);
  const demoProfile = profiles.find((p) => p.id === 'demo0001');
  // Most-recent exported files from OmniDrive — a quick "pick up where you left
  // off" strip. exportHistory arrives newest-first from /export/history.
  const recentFiles = exportHistory.slice(0, 4);

  // The eight feature cards — single source of truth for the full-width
  // responsive grid (LaunchpadDeck) so hues, i18n keys, counts and navigation
  // targets stay in one place.
  const features = [
    {
      key: 'clone',
      hue: '#d3869b',
      Icon: Fingerprint,
      title: t('launchpad.clone_title'),
      desc: t('launchpad.clone_desc'),
      count: cloneProfiles.length,
      go: () => openStudio('audio'),
    },
    {
      key: 'design',
      hue: '#8ec07c',
      Icon: Wand2,
      title: t('launchpad.design_title'),
      desc: t('launchpad.design_desc'),
      count: designProfiles.length,
      go: () => openStudio('design'),
    },
    {
      key: 'dub',
      hue: '#fe8019',
      Icon: Film,
      title: t('launchpad.dub_title'),
      desc: t('launchpad.dub_desc'),
      count: studioProjects.length,
      go: () => setMode('dub'),
    },
    {
      key: 'stories',
      hue: '#83a598',
      Icon: BookOpen,
      title: t('launchpad.stories_title'),
      desc: t('launchpad.stories_desc'),
      go: () => setMode('stories'),
    },
    {
      key: 'audiobook',
      hue: '#458588',
      Icon: BookMarked,
      title: t('launchpad.audiobook_title'),
      desc: t('launchpad.audiobook_desc'),
      go: () => setMode('audiobook'),
    },
    {
      key: 'gallery',
      hue: '#fabd2f',
      Icon: LibraryBig,
      title: t('launchpad.gallery_title'),
      desc: t('launchpad.gallery_desc'),
      go: () => setMode('gallery'),
    },
    {
      key: 'transcripts',
      hue: '#b8bb26',
      Icon: FileText,
      title: t('launchpad.transcripts_title'),
      desc: t('launchpad.transcripts_desc'),
      go: () => setMode('transcriptions'),
    },
    {
      key: 'convert',
      hue: '#689d6a',
      Icon: ArrowRightLeft,
      title: t('launchpad.convert_title'),
      desc: t('launchpad.convert_desc'),
      go: () => openStudio('convert'),
    },
  ];

  const rootRef = useRef(null);
  const shellNarrow = useShellNarrow(rootRef);

  return (
    <div
      className="launchpad [container-type:inline-size] [container-name:launchpad]"
      ref={rootRef}
    >
      {/* Hero — an eyebrow, one serif line, one sentence. Everything that used
          to compete with it (boxed number pill, filled CTA) is now quiet type;
          a hairline underneath does the separating that a card would have. */}
      {/* shrink-0 on all three blocks below (#1859). `.launchpad` is a
          scrollable flex column, and a flex item shrinks before its container
          scrolls — so opening the log panel, which shrinks grid row 2, was
          absorbed by these children instead. The hero is `overflow-hidden`, so
          it clipped its own heading mid-line, and the deck rode up over it.
          Measured in Chromium at a 720px window with the panel at 560px: the
          hero box went 145px → 64px against a 145px natural height, and the
          deck's top landed 22px ABOVE the h1's bottom. With shrink-0 the hero
          stays 145px and `.launchpad` scrolls, which is what its
          `overflow-y: auto` was for. */}
      <div className="relative z-[1] mx-auto w-full max-w-[1180px] shrink-0 overflow-hidden px-[44px] pb-[26px] pt-[38px] @max-[900px]/launchpad:px-[20px] @max-[900px]/launchpad:pb-[20px] @max-[900px]/launchpad:pt-[26px]">
        {/* The signal-field waveform (the same cover the project wears on the
            web) bleeds in from the right, where the hero has only air — the
            mask ends it well before the text column, and a bottom fade keeps
            the hairline underneath crisp. Decorative: hidden from readers,
            inert to the pointer. */}
        <img
          src={signalField}
          alt=""
          aria-hidden="true"
          data-testid="launchpad-signal-field"
          className="pointer-events-none absolute inset-y-0 right-0 h-full w-[62%] select-none object-cover object-right opacity-60 mix-blend-screen [mask-image:radial-gradient(80%_72%_at_66%_54%,black_22%,transparent_68%),linear-gradient(270deg,transparent,black_16%)] [mask-composite:intersect] @max-[900px]/launchpad:w-[78%] @max-[900px]/launchpad:opacity-40"
        />
        <div className="relative flex justify-between items-start gap-[24px] flex-wrap">
          <div className="max-w-[640px]">
            <div className="mb-[14px] flex items-center gap-[8px]">
              <span
                className="h-[5px] w-[5px] rounded-full bg-[var(--chrome-accent)]"
                aria-hidden="true"
              />
              <span className="[font-family:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] font-medium [letter-spacing:var(--chrome-label-track)] uppercase text-[color:var(--chrome-fg-dim)]">
                {t('launchpad.greeting')}
              </span>
            </div>
            <h1 className="text-[2.6rem] @max-[900px]/launchpad:text-[1.8rem] @max-[640px]/launchpad:text-[1.4rem] font-normal m-0 text-[color:var(--chrome-fg)] [font-family:var(--font-serif)]! [letter-spacing:-0.025em]! [line-height:1.06] inline-block relative [font-optical-sizing:auto]">
              <Trans
                i18nKey="launchpad.hero_title"
                components={{
                  1: (
                    <em className="italic font-normal text-[color:var(--chrome-accent)] relative" />
                  ),
                }}
              />
            </h1>
            <p className="m-0 mt-[14px] text-[color:var(--chrome-fg-muted)] text-[0.8rem] @max-[900px]/launchpad:text-[0.82rem] @max-[640px]/launchpad:text-[0.76rem] [font-family:var(--font-sans)] font-normal max-w-[540px] @max-[640px]/launchpad:max-w-full [line-height:1.65]">
              <Trans
                i18nKey="launchpad.hero_desc"
                values={{ count: 646 }}
                components={{
                  1: (
                    <span className="text-[color:var(--chrome-accent)] [font-family:var(--chrome-font-mono)] [font-variant-numeric:tabular-nums] text-[0.76rem]" />
                  ),
                }}
              />
            </p>
          </div>
          {/* A/B Compare is a side-by-side voice diff — only useful when the
              user has at least two profiles to actually compare. On a fresh
              install (or for first-time users) the button is just chrome
              noise that opens an empty modal, so we gate it. */}
          {profiles.length >= 2 && (
            <button
              type="button"
              onClick={() => setIsCompareModalOpen(true)}
              className="inline-flex items-center gap-[6px] border-0 py-[6px] px-[12px] [font-family:var(--font-sans)] text-[0.72rem] font-medium [letter-spacing:0.01em] text-[color:var(--chrome-fg-muted)] bg-transparent rounded-[var(--chrome-radius-pill)] cursor-pointer shrink-0 [transition:background_var(--dur-fast),color_var(--dur-fast)] hover:bg-[var(--chrome-accent-bg)] hover:text-[color:var(--chrome-accent)]"
              title={t('launchpad.ab_compare_title')}
            >
              <Scale size={12} strokeWidth={1.5} /> {t('launchpad.ab_compare')}
            </button>
          )}
        </div>
        <div
          className="mt-[26px] h-px [background-image:linear-gradient(90deg,color-mix(in_srgb,var(--chrome-fg)_12%,transparent),transparent_70%)]"
          aria-hidden="true"
        />
      </div>

      {/* Feature cards — one full-width, responsive grid at every shell width.
          It reflows its column count from the shell's OWN width (see
          LaunchpadDeck), filling a maximized display edge-to-edge and packing
          down to the 900×600 minimum without a viewport @media. `shellNarrow`
          (the app-container's own width class) only tunes how comfortably the
          columns pack. */}
      <div className="relative z-[1] mx-auto w-full max-w-[1180px] shrink-0 px-[44px] py-[4px] @max-[900px]/launchpad:px-[20px] @max-[640px]/launchpad:px-[12px]">
        <LaunchpadDeck features={features} narrow={shellNarrow} />
      </div>

      <div
        className="relative z-[1] mx-auto grid w-full max-w-[1180px] shrink-0 [grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr))] gap-6 px-[44px] pt-6 pb-8 @max-[900px]/launchpad:px-[20px] @max-[640px]/launchpad:px-[12px]"
        data-testid="launchpad-library-grid"
      >
        {/* Demo profile callout */}
        {demoProfile && profiles.length === 1 && studioProjects.length === 0 && (
          <div className="col-span-full min-w-0">
            <div className="flex flex-wrap items-center gap-[10px] py-[11px] px-[16px] bg-[color-mix(in_srgb,var(--chrome-accent)_7%,transparent)] rounded-[var(--chrome-radius-pill)] [font-family:var(--font-sans)] text-[0.76rem] text-[color:var(--chrome-fg-muted)] animate-[lpFadeUp_0.5s_cubic-bezier(0.4,0,0.2,1)_both]">
              <AudioLines
                size={20}
                className="shrink-0 text-[var(--chrome-accent)]"
                aria-hidden="true"
              />
              <span className="min-w-0 flex-1 leading-relaxed">{t('launchpad.demo_callout')}</span>
              <button
                type="button"
                className="ml-auto inline-flex min-h-9 items-center gap-2 shrink-0 border-0 py-[4px] px-[12px] [font-family:var(--font-sans)] text-[0.7rem] font-medium rounded-[var(--chrome-radius-pill)] bg-transparent text-[color:var(--chrome-accent)] cursor-pointer [transition:background_var(--dur-fast)] hover:bg-[color-mix(in_srgb,var(--chrome-accent)_16%,transparent)]"
                onClick={() => {
                  openStudio('audio');
                  handleSelectProfile(demoProfile);
                }}
              >
                {t('launchpad.try_it')} <ArrowRight size={14} aria-hidden="true" />
              </button>
            </div>
          </div>
        )}

        {/* Recent files from OmniDrive — last few exports, with a jump to the
          full file browser (the Projects/OmniDrive page). */}
        {recentFiles.length > 0 && (
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 mb-[10px]">
              <div className="[font-family:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] font-medium uppercase [letter-spacing:var(--chrome-label-track)] text-[color:var(--chrome-fg-dim)] m-0 flex items-center gap-[9px] shrink-0">
                <HardDrive size={12} strokeWidth={1.5} color="#fabd2f" />{' '}
                {t('launchpad.recent_files')}
              </div>
              <span
                className="flex-1 h-px [background-image:linear-gradient(90deg,color-mix(in_srgb,var(--chrome-fg)_14%,transparent),transparent)]"
                aria-hidden="true"
              />
              <button
                type="button"
                className="inline-flex items-center gap-[5px] shrink-0 border-0 bg-transparent cursor-pointer py-[2px] px-[4px] [font-family:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] font-medium uppercase [letter-spacing:var(--chrome-label-track)] text-[color:var(--chrome-fg-dim)] [transition:color_var(--dur-fast)] hover:text-[color:var(--chrome-fg)]"
                onClick={() => setMode('projects')}
              >
                {t('launchpad.view_all_files')} <ArrowRight size={12} strokeWidth={1.5} />
              </button>
            </div>
            <div className={collectionGrid} data-testid="launchpad-recent-grid">
              {recentFiles.map((f, i) => {
                const name =
                  (f.destination_path || f.path || f.filename || '').split(/[\\/]/).pop() ||
                  t('launchpad.file');
                return (
                  <button
                    key={f.id || f.destination_path || i}
                    type="button"
                    className={`${projCard} w-full [font:inherit] text-inherit text-left cursor-pointer`}
                    onClick={() => setMode('projects')}
                    title={name}
                  >
                    <div className={projIcon}>
                      <Download size={15} strokeWidth={1.5} color="#fabd2f" />
                    </div>
                    <div className={projInfo}>
                      <div className={projName}>{name}</div>
                      {f.mode && <div className={projMeta}>{f.mode}</div>}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Recent Projects */}
        {(profiles.length > 0 || studioProjects.length > 0) && (
          <div className="min-w-0">
            <div className="grid min-w-0 grid-cols-1 gap-6">
              {/* Cloned voices */}
              {cloneProfiles.length > 0 && (
                <div>
                  <div className={sectionTitle}>
                    <Fingerprint size={12} strokeWidth={1.5} color="#d3869b" />{' '}
                    {t('launchpad.cloned_voices')}
                  </div>
                  <div className={collectionGrid}>
                    {cloneProfiles.map((p) => (
                      <div key={p.id} className={projCard}>
                        <div className={projIcon}>
                          <Fingerprint size={15} strokeWidth={1.5} color="#d3869b" />
                        </div>
                        <div className={projInfo}>
                          <div className={projName}>{p.name}</div>
                          <div className={projMeta} title={p.ref_audio_path}>
                            {p.ref_audio_path?.split(/[\\/]/).pop()}
                          </div>
                        </div>
                        <button
                          type="button"
                          className={projAction}
                          onClick={() => {
                            openStudio('audio');
                            handleSelectProfile(p);
                          }}
                        >
                          {t('launchpad.open')}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Designed voices */}
              {designProfiles.length > 0 && (
                <div>
                  <div className={sectionTitle}>
                    <Wand2 size={12} strokeWidth={1.5} color="#8ec07c" />{' '}
                    {t('launchpad.designed_voices')}
                  </div>
                  <div className={collectionGrid}>
                    {designProfiles.map((p) => (
                      <div key={p.id} className={projCard}>
                        <div className={projIcon}>
                          {p.is_locked ? (
                            <Lock size={15} strokeWidth={1.5} color="#b8bb26" />
                          ) : (
                            <Wand2 size={15} strokeWidth={1.5} color="#8ec07c" />
                          )}
                        </div>
                        <div className={projInfo}>
                          <div className={projName}>{p.name}</div>
                          <div className={`${projMeta} italic`}>{p.instruct}</div>
                        </div>
                        {p.is_locked && (
                          <span className="[font-family:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] [letter-spacing:var(--chrome-label-track)] uppercase text-[#b8bb26] font-medium shrink-0">
                            {t('launchpad.locked')}
                          </span>
                        )}
                        <button
                          type="button"
                          className={projAction}
                          onClick={() => {
                            openStudio('design');
                            handleSelectProfile(p);
                          }}
                        >
                          {t('launchpad.open')}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Dubbing projects */}
              {studioProjects.length > 0 && (
                <div>
                  <div className={sectionTitle}>
                    <Film size={12} strokeWidth={1.5} color="#fe8019" />{' '}
                    {t('launchpad.dubbing_projects')}
                  </div>
                  <div className={collectionGrid}>
                    {studioProjects.map((proj) => (
                      <div key={proj.id} className={projCard}>
                        <div className={projThumb}>
                          <DubThumb
                            jobId={proj.state?.dubJobId || proj.id}
                            fallback={<Film size={15} strokeWidth={1.5} color="#fe8019" />}
                          />
                        </div>
                        <div className={projInfo}>
                          <div className={projName}>{proj.name}</div>
                          <div className={projMeta}>
                            {proj.video_path || t('launchpad.audio_only')}
                          </div>
                        </div>
                        <button
                          type="button"
                          className={projAction}
                          onClick={() => {
                            setMode('dub');
                            loadProject(proj.id);
                          }}
                        >
                          {t('launchpad.open')}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Empty state */}
      {profiles.length === 0 && studioProjects.length === 0 && (
        <div className="flex-1 flex items-center justify-center relative z-[1]">
          <div className="text-center max-w-[360px]">
            {/* Nine bars breathing on stagger — the only motion on an otherwise
                empty screen. Height/phase come from the bar's own custom props
                so `.lp-wave-bar` keeps its themed colour cycle. */}
            <div className="flex items-center justify-center gap-[3px] h-[28px] mb-[18px] opacity-25">
              {[8, 14, 22, 18, 26, 14, 20, 10, 16].map((h, i) => (
                <span
                  key={i}
                  className="lp-wave-bar"
                  style={{ '--bar-h': `${h}px`, '--bar-delay': `${i * 0.12}s` }}
                />
              ))}
            </div>
            <p className="[font-family:var(--font-sans)] text-[0.78rem] [line-height:1.6] text-[color:var(--chrome-fg-muted)] m-0">
              {t('launchpad.empty_hint')}
            </p>
          </div>
          {/* No `showWhenAllPass` — let the component self-hide when every
              check is pass-or-warn. Surfacing "everything is fine" on the
              welcome screen is noise; only show when there's an actual
              issue to address. */}
          <ReadinessChecklist />
        </div>
      )}

      {/* Show checklist alongside existing projects too, but only when issues exist */}
      {/* Wrapped rather than given the class directly: ReadinessChecklist takes
          no className, is mounted twice (nested inside the empty state as well
          as here), and shrink-0 is a fact about THIS parent's flex column, not
          about the component. */}
      {(profiles.length > 0 || studioProjects.length > 0) && (
        <div className="shrink-0">
          <ReadinessChecklist compact />
        </div>
      )}
    </div>
  );
}
