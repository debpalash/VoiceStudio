import { useEffect, useRef, useState } from 'react';
import {
  Sparkles,
  Loader,
  ChevronDown,
  ChevronUp,
  Globe,
  UserSquare2,
  Languages,
  Wand2,
  Download,
  Copy,
  ExternalLink,
  ArrowRightLeft,
  Cpu,
  Gauge,
  Hash,
  MapPin,
  RotateCcw,
} from 'lucide-react';
import { Button, Segmented, Progress } from '../../ui';
import { useAppStore } from '../../store';
import WaveformTimeline from '../WaveformTimeline';
import MultiLangPicker from '../MultiLangPicker';
import { API } from '../../api/client';
import { dubListTracks } from '../../api/dub';
import { LANG_CODES } from '../../utils/languages';
import ALL_LANGUAGES from '../../languages.json';
import SearchableSelect from '../SearchableSelect';
import { dialectOptionsFor, dialectLabel, dialectMatchesLang } from '../../api/dialects';
import { dubSegmentsText } from '../../api/dub';
import { copyText } from '../../utils/copyText';
import { openExternal } from '../../api/external';
import { TRANSLATION_ENGINES_DOCS } from '../../utils/errorDocsMap';
import CastingBoard from './CastingBoard';
import toast from 'react-hot-toast';
import './DubLeftColumn.css';

// ── Translation-settings bar utility class clusters ──────────────────────
const SETTINGS_SUMMARY = 'dub-translation-summary';
const SUMMARY_TRIGGER =
  'inline-flex items-center gap-[5px] flex-1 min-w-0 bg-transparent border-none text-fg-muted cursor-pointer py-[2px] px-0 [font:inherit] text-left';
const SETTINGS_BAR =
  'dub-translation-settings flex flex-col gap-2 mb-2 p-3 bg-[var(--chrome-hover-bg)] border-0 rounded-lg';
const FIELD = 'flex flex-col gap-1 min-w-0';
const FIELD_RESP = '';
const FIELD_LABEL = 'flex items-center gap-2 text-sm font-medium text-[var(--chrome-fg)]';
const FIELD_INPUT = 'input-base !w-full !text-xs !px-3 !py-2 min-h-9';
const ENGINE_CHIP =
  'ml-[6px] px-[6px] py-[1px] text-[0.55rem] leading-[1.4] bg-[color-mix(in_srgb,var(--color-brand)_14%,transparent)] border border-transparent text-[var(--color-brand)] rounded-[var(--radius-pill)] whitespace-nowrap transition-colors';
// Highlighted accent Install affordance — brand-filled pill, deliberately louder
// than ENGINE_CHIP so an uninstalled selected engine is an obvious call to action
// rather than a muted footnote.
const ENGINE_INSTALL_BTN =
  'inline-flex items-center gap-[3px] ml-[6px] px-[7px] py-[1px] text-[0.55rem] font-semibold leading-[1.5] bg-[var(--color-brand)] hover:bg-[var(--color-brand-hover)] text-[var(--color-fg-inverse)] border border-transparent rounded-[var(--radius-pill)] whitespace-nowrap cursor-pointer transition-colors shadow-[0_0_0_2px_color-mix(in_srgb,var(--color-brand)_25%,transparent)] disabled:opacity-60 disabled:cursor-default';

export default function DubLeftColumn({
  trackControls,
  hasDubbedTrack,
  t,
  previewMode,
  setPreviewMode,
  dubTracks,
  videoSrc,
  playbackAudioSrc,
  waveformRef,
  dubJobId,
  dubSegments,
  timelineOnsets,
  timelineSelSegId,
  setTimelineSelSegId,
  incrementalPlan,
  segmentMoveResize,
  segmentDelete,
  onTimelinePreviewSegment,
  dubStep,
  dubProgress,
  fmtDur,
  genElapsed,
  genRemaining,
  speakerClones,
  setDubSegments,
  profiles,
  settingsOpen,
  setSettingsOpen,
  dubLang,
  dubLangCode,
  translateQuality,
  activeEngineUnavailable,
  translateProvider,
  dubInstruct,
  setDubInstruct,
  handleTranslateAll,
  isTranslating,
  hasAnyTranslation,
  handleCleanupSegments,
  setDubLang,
  setDubLangCode,
  dubDialect,
  setDubDialect,
  i18n,
  enginesSandboxed,
  handleInstallEngine,
  engineInstalling,
  activeEngineEntry,
  engines,
  setTranslateProvider,
  setTranslateQuality,
  multiLangMode,
  setMultiLangMode,
  multiLangs,
  setMultiLangs,
  multiLangProgress,
  editSegments,
}) {
  // Two-stage LLM translation quality — only meaningful (and only rendered)
  // when the LLM engine is the active translator. Persisted prefs.
  const autoGlossary = useAppStore((s) => s.autoGlossary);
  const setAutoGlossary = useAppStore((s) => s.setAutoGlossary);
  const reflectPass = useAppStore((s) => s.reflectPass);
  const setReflectPass = useAppStore((s) => s.setReflectPass);
  // Opt-in LLM condensation suggestions for segments the duration planner
  // classifies as impossible to fit (default OFF — needs an LLM).
  const condenseSuggest = useAppStore((s) => s.condenseSuggest);
  const setCondenseSuggest = useAppStore((s) => s.setCondenseSuggest);
  const failedTranslationCount = dubSegments.filter((segment) => segment.translate_error).length;
  // Frozen-build (packaged/signed, read-only site-packages) escape-hatch
  // popover: pip install is impossible, so we surface the copyable command +
  // a one-click switch to the always-bundled Argos engine + a docs deeplink.
  const [installPopoverOpen, setInstallPopoverOpen] = useState(false);
  const installPopoverRef = useRef(null);
  useEffect(() => {
    if (!installPopoverOpen) return undefined;
    const onDown = (e) => {
      if (installPopoverRef.current && !installPopoverRef.current.contains(e.target)) {
        setInstallPopoverOpen(false);
      }
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setInstallPopoverOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [installPopoverOpen]);
  // Command shown/copied in the frozen popover — single-sourced from the
  // backend registry (activeEngineEntry.install_command), with a defensive
  // fallback so the popover is never empty for a known-uninstalled engine.
  const installCmd =
    activeEngineEntry?.install_command ||
    (activeEngineEntry?.pip_package ? `uv pip install ${activeEngineEntry.pip_package}` : '');
  const selectLanguage = (
    code,
    label = LANG_CODES.find((item) => item.code === code)?.label || code,
  ) => {
    setDubLang(label);
    setDubLangCode(code);
    if (!dialectMatchesLang(dubDialect, code)) setDubDialect('');
  };

  const copyInstallCmd = async () => {
    if (!installCmd) return;
    const ok = await copyText(installCmd);
    if (ok) toast.success(t('dub.install_cmd_copied'));
    else toast.error(t('dub.copy_failed'));
  };

  // Per-track metadata (duration + timing strategy) for the pill tooltips.
  // The store only carries the track codes, so hydrate lazily from the
  // existing GET /dub/tracks/{job_id} once the editor shows tracks (re-runs
  // when a new language finishes and dubTracks changes). Failure-silent:
  // the pills render fine without tooltips.
  const [trackInfo, setTrackInfo] = useState({});
  useEffect(() => {
    if (!hasDubbedTrack || !dubJobId) return undefined;
    let cancelled = false;
    dubListTracks(dubJobId)
      .then((tracks) => {
        if (!cancelled) setTrackInfo(tracks || {});
      })
      .catch(() => {
        /* tooltip enrichment only — never block or toast */
      });
    return () => {
      cancelled = true;
    };
  }, [hasDubbedTrack, dubJobId, dubTracks]);
  const trackTooltip = (code) => {
    const info = trackInfo[code];
    if (!info) return undefined;
    const parts = [];
    if (Number.isFinite(info.duration) && info.duration > 0) {
      parts.push(
        t('dub.track_tip_duration', {
          duration: fmtDur(Math.round(info.duration)),
          defaultValue: 'Duration {{duration}}',
        }),
      );
    }
    if (info.timing_strategy) {
      // Reuse the timing-strategy display names where they exist
      // (dub.timing_<id>); unknown/future strategies fall back to the raw id.
      const strategy = t(`dub.timing_${info.timing_strategy}`, {
        defaultValue: info.timing_strategy,
      });
      parts.push(t('dub.track_tip_timing', { strategy, defaultValue: 'Timing {{strategy}}' }));
    }
    return parts.length ? parts.join(' · ') : undefined;
  };

  async function hydrateMissingTranslations(code) {
    const st = useAppStore.getState();
    const jobId = st.dubJobId;
    if (!jobId) return;
    const missing = st.dubSegments.some(
      (seg) =>
        !(
          seg.translations &&
          typeof seg.translations[code] === 'string' &&
          seg.translations[code].trim()
        ),
    );
    if (!missing) return;
    try {
      const texts = await dubSegmentsText(jobId, code);
      if (!texts || !Object.keys(texts).length) return;
      const cur = useAppStore.getState();
      if (cur.dubLangCode !== code) return; // user already switched again
      cur.setDubSegments(
        cur.dubSegments.map((seg, i) => {
          const key = seg.id != null ? String(seg.id) : String(i);
          const incoming = texts[key];
          const has =
            seg.translations &&
            typeof seg.translations[code] === 'string' &&
            seg.translations[code].trim();
          if (has || typeof incoming !== 'string' || !incoming.trim()) return seg;
          return {
            ...seg,
            text: incoming,
            translations: { ...seg.translations, [code]: incoming },
            merge_parts: undefined,
          };
        }),
      );
    } catch {
      /* advisory — rows keep their previous-language text, as before */
    }
  }

  return (
    <div className="studio-panel dub-panel-col dub-panel-left">
      {hasDubbedTrack && (
        <div
          className="dub-lang-switch"
          role="radiogroup"
          aria-label={t('dub.preview_language', { defaultValue: 'Preview language' })}
        >
          <button
            type="button"
            role="radio"
            aria-checked={previewMode === 'original'}
            className={`dub-lang-pill ${previewMode === 'original' ? 'is-active' : ''}`}
            onClick={() => setPreviewMode('original')}
          >
            {t('dub.original_audio')}
          </button>
          {dubTracks.map((code) => {
            const label = LANG_CODES.find((lc) => lc.code === code)?.label || code.toUpperCase();
            return (
              <button
                key={code}
                type="button"
                role="radio"
                aria-checked={previewMode === code}
                className={`dub-lang-pill ${previewMode === code ? 'is-active' : ''}`}
                onClick={() => {
                  setPreviewMode(code);
                  // The transcript/segment list follows the previewed track:
                  // swap segment texts to this language's saved translations
                  // (the P1.2 per-language store — non-destructive, exactly
                  // what the language dropdown and multi-language generate
                  // already do). Without this, previewing German played
                  // German audio over, say, Bengali segment text.
                  const st = useAppStore.getState();
                  st.setDubLang(label);
                  st.switchDubLangCode(code);
                  // Review finding (#1148): the in-browser translations map
                  // can be PARTIAL (tracks generated before per-language
                  // persistence, partial regens) — the non-destructive switch
                  // then leaves those rows in the previous language, a
                  // mixed-language transcript under a single-language track.
                  // Hydrate the gaps from the backend's authoritative
                  // segments_i18n store. Failure-silent: no data → the rows
                  // keep what they had, exactly the pre-hydration behavior.
                  hydrateMissingTranslations(code);
                }}
                title={trackTooltip(code)}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}
      <WaveformTimeline
        key={videoSrc}
        ref={waveformRef}
        audioSrc={`${API}/dub/audio/${dubJobId}`}
        videoSrc={videoSrc}
        playbackFallbackSrc={playbackAudioSrc}
        segments={dubSegments}
        onsets={timelineOnsets}
        selectedSegId={timelineSelSegId}
        onSelectSeg={setTimelineSelSegId}
        incrementalPlan={incrementalPlan}
        onSegmentCommit={segmentMoveResize}
        onSegmentDelete={segmentDelete}
        onPreviewSegment={onTimelinePreviewSegment}
        disabled={dubStep === 'generating' || dubStep === 'stopping'}
        overlayContent={
          dubStep === 'generating' || dubStep === 'stopping' ? (
            <div className="flex flex-col items-center gap-[6px] w-full p-[10px] backdrop-blur-[2px]">
              <div className="flex items-center gap-[6px]">
                {dubStep === 'stopping' ? (
                  <Loader className="spinner" size={14} color="#a89984" />
                ) : (
                  <Sparkles className="spinner" size={14} color="#d3869b" />
                )}
                <span
                  className={`font-semibold text-[0.75rem] [font-variant-numeric:tabular-nums] tracking-[0.01em] ${dubStep === 'stopping' ? 'text-fg-muted' : 'text-fg'}`}
                >
                  {dubStep === 'stopping'
                    ? t('dub.stopping')
                    : t('dub.generate_dub') + ` ${dubProgress.current}/${dubProgress.total}…`}
                </span>
              </div>
              {dubStep === 'generating' && (
                <>
                  <div className="flex gap-[var(--space-4)] text-[0.65rem] text-fg-muted [font-variant-numeric:tabular-nums]">
                    <span>
                      ⏱ {fmtDur(genElapsed)} {t('dub.elapsed')}
                    </span>
                    {genRemaining !== null && (
                      <span>
                        ~{fmtDur(genRemaining)} {t('dub.remaining')}
                      </span>
                    )}
                  </div>
                  <div className="w-[80%] max-w-[240px] my-[1px]">
                    <Progress
                      value={
                        dubProgress.total ? (dubProgress.current / dubProgress.total) * 100 : 0
                      }
                      tone="brand"
                      size="sm"
                    />
                  </div>
                  {dubProgress.text && (
                    <span className="text-[0.62rem] text-fg-muted">{dubProgress.text}</span>
                  )}
                </>
              )}
            </div>
          ) : null
        }
      />

      {/* Cast — per-speaker voice assignment: the compact dropdown strip plus
          the drag-and-drop casting board. Renders nothing when no segment
          carries a speaker. When the auto-clone extractor found a usable
          passage per speaker (≥5s from the isolated vocals), that option is
          first-class in both views and pre-selected on the segments so "new
          language = same speaker's voice" works by default. */}
      <CastingBoard
        t={t}
        dubSegments={dubSegments}
        setDubSegments={setDubSegments}
        speakerClones={speakerClones}
        profiles={profiles}
      />

      {/* Translation settings — collapsed or expanded */}
      {!settingsOpen && (
        <div className={SETTINGS_SUMMARY}>
          <button
            type="button"
            className={SUMMARY_TRIGGER}
            onClick={() => setSettingsOpen(true)}
            title={t('dub.edit_settings')}
            aria-label={t('dub.edit_settings')}
            aria-expanded={false}
          >
            <Languages size={16} aria-hidden="true" />
            <span className="dub-translation-summary-copy">
              <strong className="text-[var(--chrome-fg)] font-semibold">{dubLang}</strong> ·{' '}
              {t(
                {
                  fast: 'dub.fast_quality',
                  autofit: 'dub.autofit_quality',
                  cinematic: 'dub.cinematic_quality',
                }[translateQuality] || 'dub.fast_quality',
              )}{' '}
              · {activeEngineEntry?.display_name || translateProvider}
              {activeEngineUnavailable && (
                <span className="text-[var(--color-danger)]"> {t('dub.needs_install_short')}</span>
              )}
            </span>
            {dubInstruct && (
              <span className="text-[var(--chrome-fg-dim)] italic ml-[var(--space-2)]">
                {t('dub.style_label_prefix')}
                {dubInstruct}
              </span>
            )}
            <ChevronDown size={14} className="ml-auto shrink-0" aria-hidden="true" />
          </button>
          {trackControls}
          <Button
            variant="subtle"
            size="sm"
            onClick={handleTranslateAll}
            disabled={isTranslating || !dubSegments.length}
            loading={isTranslating}
            leading={!isTranslating && <Languages size={10} />}
          >
            {isTranslating
              ? t('dub.translating')
              : hasAnyTranslation
                ? t('dub.retranslate')
                : t('dub.translate_all')}
          </Button>
          <Button
            variant="subtle"
            size="sm"
            onClick={handleCleanupSegments}
            disabled={!dubSegments.length || !dubJobId}
            title={t('dub.clean_up_title')}
            leading={<Wand2 size={10} />}
          >
            {t('dub.clean_up')}
          </Button>
        </div>
      )}
      {settingsOpen && (
        <div className={SETTINGS_BAR}>
          <div className="dub-translation-fields">
            <button
              type="button"
              className={`${SUMMARY_TRIGGER} col-span-full !py-2`}
              onClick={() => setSettingsOpen(false)}
              title={t('dub.collapse_settings')}
              aria-expanded={true}
            >
              <Languages size={16} aria-hidden="true" />
              {t('dub.edit_settings')}
              <ChevronUp size={14} className="ml-auto" aria-hidden="true" />
            </button>
            <div className={`${FIELD} flex-[1_1_100px] min-w-[70px] ${FIELD_RESP}`}>
              <div className={FIELD_LABEL}>
                <Globe size={15} aria-hidden="true" /> {t('dub.language')}
              </div>
              <MultiLangPicker
                single
                ariaLabel={t('dub.language')}
                compact
                selected={[{ lang: dubLang, code: dubLang === 'Auto' ? 'Auto' : dubLangCode }]}
                options={ALL_LANGUAGES.map((label) => ({
                  label,
                  code: LANG_CODES.find((item) => item.label === label)?.code || label,
                }))}
                onChange={([item]) => {
                  selectLanguage(item.code, item.lang);
                }}
              />
            </div>
            <div className={`${FIELD} flex-[0_1_72px] min-w-[52px] ${FIELD_RESP}`}>
              <div className={FIELD_LABEL}>
                <Hash size={15} aria-hidden="true" />
                {t('dub.iso_code')}
              </div>
              <SearchableSelect
                ariaLabel={t('dub.iso_code')}
                menuPortal
                buttonClassName={FIELD_INPUT}
                value={dubLangCode}
                options={LANG_CODES.map((lc) => ({
                  value: lc.code,
                  label: lc.code + ' — ' + lc.label,
                }))}
                onChange={(code) => {
                  selectLanguage(code);
                }}
              />
            </div>
            {/* #280: regional dialect / vocabulary. Only rendered for
                      languages with curated variants; region names come from
                      Intl.DisplayNames so they localize with the UI for free. */}
            {dialectOptionsFor(dubLangCode).length > 0 && (
              <div className={`${FIELD} flex-[0_1_110px] min-w-[80px] ${FIELD_RESP}`}>
                <div className={FIELD_LABEL} title={t('dub.dialect_title')}>
                  <MapPin size={15} aria-hidden="true" />
                  {t('dub.dialect_label')}
                </div>
                <SearchableSelect
                  ariaLabel={t('dub.dialect_label')}
                  menuPortal
                  buttonClassName={FIELD_INPUT}
                  value={dialectMatchesLang(dubDialect, dubLangCode) ? dubDialect : ''}
                  onChange={setDubDialect}
                  options={[
                    { value: '', label: t('dub.dialect_default') },
                    ...dialectOptionsFor(dubLangCode).map((d) => ({
                      value: d,
                      label: dialectLabel(d, i18n.language),
                    })),
                  ]}
                />
              </div>
            )}
            <div className={`${FIELD} flex-[1.4_1_130px] min-w-[90px] ${FIELD_RESP}`}>
              <div className={`${FIELD_LABEL} !overflow-visible flex items-center`}>
                <Cpu size={15} aria-hidden="true" />
                {t('dub.engine_label')}
                {/* FROM-SOURCE lane: pip install works (uv pip install runs
                    in-process). Promote the muted chip to a highlighted accent
                    Install button so an uninstalled selected engine is an
                    obvious call to action. Keys off translateProvider, so
                    picking any uninstalled engine surfaces it immediately. */}
                {activeEngineUnavailable && !enginesSandboxed && (
                  <button
                    type="button"
                    className={ENGINE_INSTALL_BTN}
                    onClick={() => handleInstallEngine(translateProvider)}
                    disabled={engineInstalling === translateProvider}
                    title={t('dub.install_engine')}
                  >
                    {engineInstalling === translateProvider ? (
                      <>
                        <Loader className="spinner" size={9} /> {t('dub.installing_engine')}
                      </>
                    ) : (
                      <>
                        <Download size={9} />{' '}
                        {t('dub.install_engine_pkg', {
                          pkg: activeEngineEntry?.pip_package || '',
                        })}
                      </>
                    )}
                  </button>
                )}
                {/* FROZEN lane: packaged build, site-packages is read-only +
                    signed, so pip install is impossible. Offer a highlighted
                    button that opens a popover with the copyable command, a
                    one-click switch to bundled Argos, and a docs deeplink. */}
                {activeEngineUnavailable && enginesSandboxed && (
                  <span className="relative inline-flex" ref={installPopoverRef}>
                    <button
                      type="button"
                      className={ENGINE_INSTALL_BTN}
                      onClick={() => setInstallPopoverOpen((o) => !o)}
                      aria-haspopup="dialog"
                      aria-expanded={installPopoverOpen}
                      title={t('dub.install_disabled_title')}
                    >
                      <Download size={9} /> {t('dub.needs_install_short')}
                    </button>
                    {installPopoverOpen && (
                      <div
                        role="dialog"
                        aria-label={t('dub.install_popover_title')}
                        className="absolute z-20 top-[calc(100%+6px)] left-0 w-[290px] max-w-[80vw] p-[10px] flex flex-col gap-[8px] bg-[var(--chrome-bg,#282828)] border border-transparent rounded-[8px] shadow-[0_8px_24px_rgba(0,0,0,0.45)] normal-case text-left"
                      >
                        <div className="text-[0.68rem] font-semibold text-[var(--chrome-fg,#ebdbb2)] normal-case tracking-normal">
                          {t('dub.install_popover_title')}
                        </div>
                        <p className="text-[0.62rem] leading-[1.4] text-[var(--chrome-fg-muted,#a89984)] m-0">
                          {t('dub.install_popover_frozen_body')}
                        </p>
                        {installCmd && (
                          <div className="flex items-stretch gap-[4px]">
                            <code className="flex-1 min-w-0 px-[6px] py-[4px] text-[0.6rem] leading-[1.4] font-[family-name:var(--chrome-font-mono,monospace)] text-[var(--chrome-fg,#ebdbb2)] bg-[rgba(0,0,0,0.35)] border border-transparent rounded-[5px] overflow-x-auto whitespace-nowrap">
                              {installCmd}
                            </code>
                            <button
                              type="button"
                              className="shrink-0 inline-flex items-center justify-center px-[6px] rounded-[5px] border border-transparent text-[var(--chrome-fg-muted,#a89984)] hover:text-[var(--chrome-fg,#ebdbb2)] hover:border-transparent cursor-pointer bg-transparent"
                              onClick={copyInstallCmd}
                              title={t('dub.copy_command')}
                              aria-label={t('dub.copy_command')}
                            >
                              <Copy size={11} />
                            </button>
                          </div>
                        )}
                        <button
                          type="button"
                          className="inline-flex items-center justify-center gap-[5px] px-[8px] py-[5px] text-[0.64rem] font-semibold bg-[var(--color-brand)] hover:bg-[var(--color-brand-hover)] text-[var(--color-fg-inverse)] border-none rounded-[var(--radius-lg)] cursor-pointer transition-colors"
                          onClick={() => {
                            setTranslateProvider('argos');
                            setInstallPopoverOpen(false);
                          }}
                        >
                          <ArrowRightLeft size={11} /> {t('dub.switch_to_argos')}
                        </button>
                        <button
                          type="button"
                          className="inline-flex items-center gap-[5px] self-start text-[0.6rem] text-[var(--chrome-fg-muted,#a89984)] hover:text-[var(--chrome-fg,#ebdbb2)] bg-transparent border-none cursor-pointer p-0"
                          onClick={() => openExternal(TRANSLATION_ENGINES_DOCS)}
                        >
                          <ExternalLink size={10} /> {t('dub.open_docs')}
                        </button>
                      </div>
                    )}
                  </span>
                )}
              </div>
              <SearchableSelect
                ariaLabel={t('dub.engine_label')}
                menuPortal
                buttonClassName={FIELD_INPUT}
                value={translateProvider}
                onChange={setTranslateProvider}
                options={engines.map((p) => ({
                  value: p.id,
                  label: p.installed
                    ? p.display_name
                    : p.display_name + t('dub.needs_install_suffix'),
                }))}
              />
            </div>
            <div className={`${FIELD} flex-[0_1_auto] min-w-[80px] ${FIELD_RESP}`}>
              <div className={FIELD_LABEL} title={t('dub.quality_title')}>
                <Gauge size={15} aria-hidden="true" />
                {t('dub.quality_label')}
              </div>
              <Segmented
                className="w-full [&_button]:min-h-8 [&_button]:text-xs"
                size="sm"
                value={translateQuality}
                onChange={setTranslateQuality}
                items={[
                  { value: 'fast', label: t('dub.fast_quality') },
                  {
                    value: 'autofit',
                    label: t('dub.autofit_quality', { defaultValue: 'Autofit' }),
                  },
                  { value: 'cinematic', label: t('dub.cinematic_quality') },
                ]}
              />
              {/* Opt-in (default OFF): when the duration planner marks a
                  translated line "impossible" for its slot, ask the LLM for a
                  shorter rewrite the user can apply per segment. */}
              <label
                className="flex items-center gap-[4px] mt-[3px] text-[0.55rem] text-fg-muted cursor-pointer select-none"
                title={t('dub.condense_title')}
              >
                <input
                  type="checkbox"
                  checked={condenseSuggest}
                  onChange={(e) => setCondenseSuggest(e.target.checked)}
                  className="cursor-pointer"
                />
                {t('dub.condense_label')}
              </label>
            </div>
            {/* LLM engine only: auto-glossary + reflect pass. Both default ON;
                the reflect tooltip is explicit that it multiplies LLM calls. */}
            {translateProvider === 'openai' && (
              <div
                className={`${FIELD} flex-[0_0_auto] ${FIELD_RESP} justify-end gap-[2px] pb-[2px]`}
              >
                <label
                  className="flex items-center gap-[4px] text-[0.6rem] text-[var(--chrome-fg-muted)] cursor-pointer whitespace-nowrap"
                  title={t('dub.auto_glossary_title')}
                >
                  <input
                    type="checkbox"
                    className="accent-[var(--color-brand)] cursor-pointer"
                    checked={autoGlossary}
                    onChange={(e) => setAutoGlossary(e.target.checked)}
                  />
                  <span>{t('dub.auto_glossary_label')}</span>
                </label>
                <label
                  className="flex items-center gap-[4px] text-[0.6rem] text-[var(--chrome-fg-muted)] cursor-pointer whitespace-nowrap"
                  title={t('dub.reflect_title')}
                >
                  <input
                    type="checkbox"
                    className="accent-[var(--color-brand)] cursor-pointer"
                    checked={reflectPass}
                    onChange={(e) => setReflectPass(e.target.checked)}
                  />
                  <span>{t('dub.reflect_label')}</span>
                </label>
              </div>
            )}
            <div className={`${FIELD} flex-[1_1_90px] min-w-[64px] ${FIELD_RESP}`}>
              <div className={FIELD_LABEL}>
                <UserSquare2 size={15} aria-hidden="true" /> {t('dub.style')}{' '}
                <span className="text-[0.52rem] text-fg-subtle italic ml-[2px]">
                  {t('dub.optional')}
                </span>
              </div>
              <input
                className={FIELD_INPUT}
                aria-label={t('dub.style')}
                placeholder={t('dub.style_placeholder')}
                value={dubInstruct}
                onChange={(e) => setDubInstruct(e.target.value)}
              />
            </div>
            <div className={`${FIELD} col-span-full pt-2`}>
              <label className="flex items-center gap-[6px] text-[0.65rem] text-[var(--chrome-fg-muted)] cursor-pointer mb-[2px]">
                <input
                  type="checkbox"
                  className="accent-[var(--color-brand)] cursor-pointer"
                  checked={multiLangMode}
                  onChange={(e) => setMultiLangMode(e.target.checked)}
                />
                <span>{t('dub.multi_lang')}</span>
              </label>
              {multiLangMode && (
                <MultiLangPicker
                  selected={multiLangs}
                  onChange={setMultiLangs}
                  disabled={dubStep === 'generating'}
                  progressByCode={multiLangProgress}
                />
              )}
            </div>
          </div>
          <div className="dub-translation-actions flex justify-end gap-2 flex-wrap">
            <div className="mr-auto min-w-0">{trackControls}</div>
            {failedTranslationCount > 0 && (
              <>
                <Button
                  variant="subtle"
                  size="sm"
                  onClick={() => handleTranslateAll({ retryFailed: true })}
                  disabled={isTranslating}
                >
                  {t('dub.retry_failed', { count: failedTranslationCount })}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    editSegments(
                      dubSegments.map((segment) =>
                        segment.translate_error
                          ? { ...segment, translate_error: undefined, translation_skipped: true }
                          : segment,
                      ),
                    )
                  }
                  disabled={isTranslating}
                >
                  {t('dub.skip_failed')}
                </Button>
              </>
            )}
            <Button
              variant="subtle"
              size="sm"
              onClick={() =>
                editSegments(
                  dubSegments.map((s) => ({
                    ...s,
                    text: s.text_original || s.text,
                    translate_error: undefined,
                    translate_degraded: undefined,
                  })),
                )
              }
              disabled={!dubSegments.some((s) => s.text_original && s.text_original !== s.text)}
              title={t('dub.restore_title')}
              leading={<RotateCcw size={15} aria-hidden="true" />}
            >
              {t('dub.restore')}
            </Button>
            <Button
              variant="subtle"
              size="sm"
              onClick={handleCleanupSegments}
              disabled={!dubSegments.length || !dubJobId}
              title={t('dub.clean_up_title')}
              leading={<Wand2 size={15} aria-hidden="true" />}
            >
              {t('dub.clean_up')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleTranslateAll}
              disabled={isTranslating || !dubSegments.length}
              loading={isTranslating}
              leading={!isTranslating && <Languages size={10} />}
            >
              {isTranslating ? t('dub.translating') : t('dub.translate_all')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
