import { useState } from 'react';
import i18n from '../../i18n';
import DubLeftColumn from '../../components/dub/DubLeftColumn';
import DubRightColumn from '../../components/dub/DubRightColumn';
import DubResizableColumns from '../../components/dub/DubResizableColumns';
import DubTrackSummary from '../../components/dub/DubTrackSummary';

const noop = () => {};
const segments = Array.from({ length: 353 }, (_, index) => ({
  id: `segment-${index}`,
  start: index * 4,
  end: index * 4 + 3,
  text: [
    'Every voice has a story. Let us bring yours to life.',
    'How did we get here? It is a long story.',
    'A small moment can change the way you see everything.',
  ][index % 3],
  text_original: 'Every voice has a story. Let us bring yours to life.',
  speaker_id: `Speaker ${(index % 2) + 1}`,
  profile_id: `auto:speaker_${(index % 2) + 1}`,
  fit_status: { status: index % 3 === 1 ? 'overflows' : 'fits', overflow_s: 0.69 },
  rate_ratio: 0.73,
}));

export default function DubWorkspaceFixture() {
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [showTranscript, setShowTranscript] = useState(false);
  const [glossaryOpen, setGlossaryOpen] = useState(false);
  const [selectedSegIds, setSelectedSegIds] = useState(new Set(segments.map((s) => s.id)));
  const [timingStrategy, setTimingStrategy] = useState('concise');
  const [voiceMatch, setVoiceMatch] = useState('per_line');
  const [preserveBg, setPreserveBg] = useState(true);
  const [dualSubs, setDualSubs] = useState(false);
  const [burnSubs, setBurnSubs] = useState(false);
  const [defaultTrack, setDefaultTrack] = useState('bn');
  const [previewMode, setPreviewMode] = useState('bn');
  const [exportTracks, setExportTracks] = useState(['original', 'bn']);
  const common = {
    t: i18n.t.bind(i18n),
    dubJobId: 'workspace-fixture',
    dubSegments: segments,
    dubTracks: ['bn'],
    dubLang: 'Bengali',
    dubLangCode: 'bn',
    dubStep: 'done',
    profiles: [{ id: 'demo', name: 'VoiceStudio Demo Voice' }],
    speakerClones: { 'Speaker 1': { duration: 5.6 }, 'Speaker 2': { duration: 8.2 } },
    isTranslating: false,
    setDubLang: noop,
    setDubLangCode: noop,
    segmentMoveResize: noop,
    segmentDelete: noop,
  };
  return (
    <div
      className="dub-workspace"
      style={{
        height: 'calc(100vh - 40px)',
        containerType: 'inline-size',
        containerName: 'dub-shell',
      }}
    >
      <div className="dub-editor flex flex-col" style={{ height: '100%' }}>
        <DubResizableColumns resizeLabel="Resize video and transcript columns">
          <DubLeftColumn
            {...common}
            trackControls={
              <DubTrackSummary
                {...common}
                exportTracks={exportTracks}
                setExportTracks={setExportTracks}
              />
            }
            hasDubbedTrack
            previewMode={previewMode}
            setPreviewMode={setPreviewMode}
            videoSrc="/workspace-fixture.wav"
            setDubSegments={noop}
            settingsOpen={settingsOpen}
            setSettingsOpen={setSettingsOpen}
            translateQuality="fast"
            translateProvider="argos"
            activeEngineEntry={{
              id: 'argos',
              display_name: 'Argos (Local, Fast)',
              installed: true,
            }}
            engines={[{ id: 'argos', display_name: 'Argos (Local, Fast)', installed: true }]}
            dubInstruct=""
            setDubInstruct={noop}
            handleTranslateAll={noop}
            handleCleanupSegments={noop}
            hasAnyTranslation
            dubDialect=""
            setDubDialect={noop}
            i18n={i18n}
            setTranslateProvider={noop}
            setTranslateQuality={noop}
            multiLangMode={false}
            setMultiLangMode={noop}
            multiLangs={[]}
            setMultiLangs={noop}
            fmtDur={(seconds) => `${seconds}s`}
          />
          <DubRightColumn
            {...common}
            preserveBg={preserveBg}
            setPreserveBg={setPreserveBg}
            dualSubs={dualSubs}
            setDualSubs={setDualSubs}
            burnSubs={burnSubs}
            setBurnSubs={setBurnSubs}
            defaultTrack={defaultTrack}
            setDefaultTrack={setDefaultTrack}
            timingStrategy={timingStrategy}
            setTimingStrategy={setTimingStrategy}
            voiceMatch={voiceMatch}
            setVoiceMatch={setVoiceMatch}
            dubTranscript="Every voice has a story. Let us bring yours to life."
            showTranscript={showTranscript}
            setShowTranscript={setShowTranscript}
            glossaryVisible={glossaryOpen}
            setGlossaryOpen={setGlossaryOpen}
            setGlossaryHidden={noop}
            glossaryTermCount={0}
            selectedSegIds={selectedSegIds}
            clearSegSelection={() => setSelectedSegIds(new Set())}
            bulkApplyToSelected={noop}
            bulkDeleteSelected={noop}
            pasteTranslations={noop}
          />
        </DubResizableColumns>
      </div>
    </div>
  );
}
