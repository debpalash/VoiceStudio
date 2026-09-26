import { Suspense, lazy, useState, useId } from 'react';
import {
  ChevronUp,
  ChevronDown,
  FileText,
  ClipboardPaste,
  AudioLines,
  Languages,
  Captions,
  ListMusic,
  Timer,
  Fingerprint,
  BookOpen,
  Settings2,
} from 'lucide-react';
import DubToggle from './DubToggle';
import SearchableSelect from '../SearchableSelect';
import './DubRightColumn.css';
import { Button, Segmented } from '../../ui';
import GlossaryPanel from '../GlossaryPanel';
import CheckpointBanner from '../CheckpointBanner';
import DubSelectionToolbar from './DubSelectionToolbar';
import { resolveDubDefaultTrack } from '../../utils/dubDefaultTrack';

const DubSegmentTable = lazy(() => import('../DubSegmentTable'));
const DubPasteTranslationDialog = lazy(() => import('./DubPasteTranslationDialog'));

const LazyFallback = () => <div className="p-[12px] text-[#6b6657] text-[0.7rem]">Loading…</div>;

// ── Output-options + bulk-select utility clusters ────────────────────────
const OUT_TITLE =
  'font-[family-name:var(--chrome-font-mono)] text-[length:var(--chrome-label-size)] tracking-[var(--chrome-label-track)] uppercase text-[var(--chrome-fg-muted)] font-semibold';

export default function DubRightColumn({
  t,
  preserveBg,
  setPreserveBg,
  dualSubs,
  setDualSubs,
  burnSubs,
  setBurnSubs,
  defaultTrack,
  setDefaultTrack,
  dubLangCode,
  multiLangMode,
  batchTargets,
  multiBatchBusy,
  setDubLang,
  setDubLangCode,
  dubTracks,
  timingStrategy,
  setTimingStrategy,
  voiceMatch,
  setVoiceMatch,
  dubTranscript,
  showTranscript,
  setShowTranscript,
  dubJobId,
  glossaryVisible,
  setGlossaryOpen,
  setGlossaryHidden,
  glossaryTermCount,
  dubLang,
  dubSegments,
  onGlossaryChange,
  selectedSegIds,
  bulkApplyToSelected,
  speakerClones,
  profiles,
  clearSegSelection,
  bulkDeleteSelected,
  showCheckpoint,
  checkpointStage,
  onCheckpointContinue,
  onCheckpointDismiss,
  isTranslating,
  segmentPreviewLoading,
  toggleSegSelect,
  selectAllSegs,
  segmentEditField,
  segmentDelete,
  segmentRestoreOriginal,
  handleSegmentPreview,
  onDirectSegment,
  segmentSplit,
  segmentMerge,
  segmentInsert,
  segmentMoveResize,
  seekWaveform,
  timelineSelSegId,
  dubStep,
  dubProgress,
  pasteTranslations,
}) {
  const [pasteOpen, setPasteOpen] = useState(false);
  const [outputOpen, setOutputOpen] = useState(false);
  const outputId = useId();
  const transcriptId = useId();
  const resolvedTrack = resolveDubDefaultTrack(defaultTrack, dubLangCode, dubTracks);
  const timingLabel = t(
    {
      concise: 'dub.timing_concise',
      smart_fit: 'dub.timing_smart_fit',
      stretch_video: 'dub.timing_stretch_video',
      strict_slot: 'dub.timing_lip_sync',
    }[timingStrategy] || 'dub.timing_concise',
  );
  const outputSummary = [
    { label: timingLabel, title: t('dub.timing_label'), Icon: Timer },
    {
      label: t(
        voiceMatch === 'consistent' ? 'dub.voice_match_consistent' : 'dub.voice_match_per_line',
      ),
      title: t('dub.voice_match'),
      Icon: Fingerprint,
    },
    {
      label: resolvedTrack === 'original' ? t('dub.original_audio') : resolvedTrack.toUpperCase(),
      title: t('dub.default_track'),
      Icon: ListMusic,
    },
    ...(preserveBg ? [{ label: t('dub.mix_bg_audio'), Icon: AudioLines }] : []),
    ...(dualSubs ? [{ label: t('dub.dual_subs'), Icon: Languages }] : []),
    ...(burnSubs ? [{ label: t('dub.burn_subs'), Icon: Captions }] : []),
  ];
  return (
    <div className="studio-panel dub-panel-col dub-panel-right">
      <div className="dub-output-settings">
        <button
          type="button"
          className="dub-output-toggle"
          aria-label={t('dub.output_options')}
          aria-describedby={`${outputId}-summary`}
          aria-expanded={outputOpen}
          aria-controls={outputId}
          onClick={() => setOutputOpen((open) => !open)}
        >
          <span className="dub-output-heading">
            <Settings2 size={16} aria-hidden="true" />
            {t('dub.output_options')}
          </span>
          <span id={`${outputId}-summary`} className="dub-output-summary">
            {outputSummary.map(({ label, title, Icon }) => (
              <span key={title || label} title={title}>
                <Icon size={13} aria-hidden="true" /> {label}
              </span>
            ))}
          </span>
          {outputOpen ? (
            <ChevronUp size={15} aria-hidden="true" />
          ) : (
            <ChevronDown size={15} aria-hidden="true" />
          )}
        </button>
        {outputOpen && (
          <div id={outputId} className="dub-output-content">
            <div className="dub-output-grid">
              <DubToggle
                label={t('dub.mix_bg_audio')}
                Icon={AudioLines}
                checked={preserveBg}
                onChange={setPreserveBg}
              />
              <DubToggle
                label={t('dub.dual_subs')}
                title={t('dub.dual_subs_title')}
                Icon={Languages}
                checked={dualSubs}
                onChange={setDualSubs}
              />
              <DubToggle
                label={t('dub.burn_subs')}
                title={t('dub.burn_subs_title')}
                Icon={Captions}
                checked={burnSubs}
                onChange={setBurnSubs}
              />
            </div>
            <div className="dub-output-fields">
              <div className="dub-output-field dub-output-track">
                <span className="inline-flex items-center gap-2 text-sm">
                  <ListMusic size={15} aria-hidden="true" />
                  {t('dub.default_track')}
                </span>
                <SearchableSelect
                  ariaLabel={t('dub.default_track')}
                  menuPortal
                  value={resolvedTrack}
                  onChange={setDefaultTrack}
                  buttonClassName="min-h-10 rounded-lg border-0 bg-[var(--chrome-hover-bg)] px-3 text-sm text-[var(--chrome-fg)]"
                  options={[
                    { value: 'original', label: t('dub.original_track') },
                    ...(dubLangCode
                      ? [
                          {
                            value: dubLangCode,
                            label: t('dub.selected_dub', { code: dubLangCode }),
                          },
                        ]
                      : []),
                    ...dubTracks
                      .filter((tr) => tr !== dubLangCode)
                      .map((tr) => ({ value: tr, label: t('dub.dub_track', { code: tr }) })),
                  ]}
                />
              </div>
              <div className="dub-output-field">
                <span className={OUT_TITLE}>
                  <Timer size={16} aria-hidden="true" />
                  {t('dub.timing_label')}
                </span>
                <Segmented
                  aria-label={t('dub.timing_label')}
                  value={timingStrategy}
                  onChange={setTimingStrategy}
                  items={[
                    {
                      value: 'concise',
                      label: t('dub.timing_concise'),
                    },
                    {
                      value: 'smart_fit',
                      label: t('dub.timing_smart_fit'),
                      title: t('dub.timing_smart_fit_title'),
                    },
                    {
                      value: 'stretch_video',
                      label: t('dub.timing_stretch_video'),
                    },
                    {
                      value: 'strict_slot',
                      label: t('dub.timing_lip_sync'),
                      title: t('dub.timing_lip_sync'),
                    },
                  ]}
                />
              </div>
              {/* Voice match — whether each line clones from its own source clip
            (best prosody, identity may drift) or every line of a speaker
            shares ONE reference (steady identity). */}
              <div className="dub-output-field" title={t('dub.voice_match_title')}>
                <span className={OUT_TITLE}>
                  <Fingerprint size={16} aria-hidden="true" />
                  {t('dub.voice_match')}
                </span>
                <Segmented
                  aria-label={t('dub.voice_match')}
                  value={voiceMatch}
                  onChange={setVoiceMatch}
                  items={[
                    {
                      value: 'per_line',
                      label: t('dub.voice_match_per_line'),
                      title: t('dub.voice_match_per_line_title'),
                    },
                    {
                      value: 'consistent',
                      label: t('dub.voice_match_consistent'),
                      title: t('dub.voice_match_consistent_title'),
                    },
                  ]}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      {showCheckpoint && (
        <CheckpointBanner
          stage={checkpointStage}
          count={dubSegments.length}
          timingWarnings={
            dubSegments.filter((segment) => segment.fit_status?.status === 'overflows').length
          }
          onContinue={checkpointStage === 'done' ? null : onCheckpointContinue}
          onDismiss={onCheckpointDismiss}
          continueLoading={isTranslating}
        />
      )}

      {(dubTranscript || dubJobId || pasteTranslations) && (
        <div className="dub-reference-tools">
          {dubTranscript && (
            <button
              type="button"
              aria-expanded={showTranscript}
              aria-controls={transcriptId}
              className="dub-reference-toggle"
              onClick={() => setShowTranscript(!showTranscript)}
            >
              <FileText size={15} aria-hidden="true" />
              {t('dub.transcript')}
              {showTranscript ? (
                <ChevronUp size={14} aria-hidden="true" />
              ) : (
                <ChevronDown size={14} aria-hidden="true" />
              )}
            </button>
          )}
          {dubJobId && (
            <button
              type="button"
              className="dub-reference-toggle"
              aria-expanded={glossaryVisible}
              onClick={() => {
                setGlossaryOpen(!glossaryVisible);
                setGlossaryHidden(glossaryVisible);
              }}
              title={t('dub.glossary_title')}
            >
              <BookOpen size={15} aria-hidden="true" />
              {t('glossary.title')}
              <span className="dub-reference-count">
                {t('glossary.count', { count: glossaryTermCount })}
              </span>
              {glossaryVisible ? (
                <ChevronUp size={14} aria-hidden="true" />
              ) : (
                <ChevronDown size={14} aria-hidden="true" />
              )}
            </button>
          )}
          {pasteTranslations && (
            <Button
              className="dub-paste-translation"
              variant="subtle"
              size="sm"
              onClick={() => setPasteOpen(true)}
              disabled={!dubSegments.length}
              title={t('dub.paste_translation_title')}
              leading={<ClipboardPaste size={14} aria-hidden="true" />}
            >
              {t('dub.paste_translation_btn')}
            </Button>
          )}
        </div>
      )}
      {dubTranscript && showTranscript && (
        <div id={transcriptId} className="dub-reference-section dub-transcript-body">
          {dubTranscript}
        </div>
      )}
      {dubJobId && glossaryVisible && (
        <div className="mb-[4px]">
          <GlossaryPanel
            projectId={dubJobId}
            sourceLang={dubLangCode && dubLang ? dubLang.slice(0, 2).toLowerCase() || 'en' : 'en'}
            targetLang={dubLangCode}
            segments={dubSegments}
            onChange={onGlossaryChange}
            onClose={() => {
              setGlossaryHidden(true);
              setGlossaryOpen(false);
            }}
          />
        </div>
      )}

      {/* "Apply Voice to All" row removed 2026-04-21 — redundant
                  with the CAST strip in the left column, which does the same
                  thing per-speaker (and handles the multi-speaker case cleanly). */}

      {selectedSegIds.size > 0 && (
        <DubSelectionToolbar
          t={t}
          count={selectedSegIds.size}
          profiles={profiles}
          speakerClones={speakerClones}
          disabled={multiBatchBusy || dubStep === 'generating' || dubStep === 'stopping'}
          onApply={bulkApplyToSelected}
          onDelete={bulkDeleteSelected}
          onClear={clearSegSelection}
        />
      )}

      {multiLangMode && batchTargets?.length > 1 && (
        <label className="mb-[4px] flex max-w-[320px] items-center gap-[7px] px-[2px]">
          <span className={OUT_TITLE}>{t('dub.language')}:</span>
          <SearchableSelect
            ariaLabel={t('dub.language')}
            value={dubLangCode}
            disabled={multiBatchBusy}
            menuPortal
            options={batchTargets.map((target) => ({
              value: target.code,
              label: `${target.lang} · ${target.code.toUpperCase()}`,
            }))}
            onChange={(code) => {
              const target = batchTargets.find((item) => item.code === code);
              if (!target) return;
              setDubLang(target.lang);
              setDubLangCode(target.code);
            }}
          />
        </label>
      )}

      {pasteOpen && (
        <Suspense fallback={null}>
          <DubPasteTranslationDialog
            open
            segments={dubSegments}
            onApply={pasteTranslations}
            onClose={() => setPasteOpen(false)}
          />
        </Suspense>
      )}

      <Suspense fallback={<LazyFallback />}>
        <DubSegmentTable
          segments={dubSegments}
          profiles={profiles}
          speakerClones={speakerClones}
          dubStep={dubStep}
          dubProgress={dubProgress}
          previewLoadingId={segmentPreviewLoading}
          selectedIds={selectedSegIds}
          onSelect={toggleSegSelect}
          onSelectAll={selectAllSegs}
          onClearSelection={clearSegSelection}
          onEditField={segmentEditField}
          onDelete={segmentDelete}
          onRestore={segmentRestoreOriginal}
          onPreview={handleSegmentPreview}
          onDirect={onDirectSegment}
          onSplit={segmentSplit}
          onMerge={segmentMerge}
          onInsert={segmentInsert}
          onMoveResize={segmentMoveResize}
          onSeek={seekWaveform}
          timelineSelectedId={timelineSelSegId}
        />
      </Suspense>
    </div>
  );
}
