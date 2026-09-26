// ─────────────────────────────────────────────────────────────────
//  Visual-regression component registry.
//
//  Each entry renders a small, representative spread of a presentational
//  leaf component's variants/states. Keep these PURE — no backend hooks,
//  no i18n, no app context — so they render synchronously and snapshot
//  deterministically.
//
//  To add a component: add an entry here AND its name to ./manifest.ts
//  (the Playwright test reads the manifest). See ./README.md.
// ─────────────────────────────────────────────────────────────────

import React from 'react';
import DubSegmentTable from '../../components/DubSegmentTable.jsx';
import DubWorkspaceSidebar from '../../components/DubWorkspaceSidebar.jsx';
import DubSelectionToolbar from '../../components/dub/DubSelectionToolbar.jsx';
import IdleSkeleton from '../../components/dub/IdleSkeleton.jsx';
import i18n from '../../i18n';
import WaveformTimeline from '../../components/WaveformTimeline.jsx';
import DubWorkspaceFixture from './DubWorkspaceFixture.jsx';
import '../../components/dub/DubRightColumn.css';
import Header from '../../components/Header';
import StoriesEditor from '../../components/StoriesEditor.jsx';
import AudiobookTab from '../../pages/AudiobookTab.jsx';
import { Download, Mic, Search, Sparkles, Trash2 } from 'lucide-react';

import Badge from '../../ui/Badge.jsx';
import Button from '../../ui/Button.jsx';
import { Field, Input, Select, Textarea } from '../../ui/Input.jsx';
import Panel from '../../ui/Panel.jsx';
import Progress from '../../ui/Progress.jsx';
import Segmented from '../../ui/Segmented.jsx';
import Slider from '../../ui/Slider.jsx';
import Table from '../../ui/Table.jsx';
import Tabs from '../../ui/Tabs.jsx';
import SettingRow from '../../components/settings/primitives/SettingRow.jsx';
import SettingsToggle from '../../components/settings/primitives/SettingsToggle.jsx';
// shadcn/ui proof components — themed via the VoiceStudio token bridge (index.css).
// Rendered here across all 3 themes to prove the bridge keeps them on-palette.
import { Button as ShadcnButton } from '../../components/ui/button.tsx';
import { Input as ShadcnInput } from '../../components/ui/input.tsx';
// SettingRow / SettingsToggle styling is now Tailwind utilities on the token
// bridge (FAST-mode shadcn migration) — no primitives stylesheet to import.
import './harness.css';

// ── PAGE / PANEL specs (opt-in providers) ─────────────────────────────────
// Unlike the pure leaf specs above, these render real Settings panels that
// depend on the Zustand store, react-i18next, react-query, and direct api/*
// fetches. Each declares a `providers` block (see ./providers.jsx) that seeds
// that infrastructure with representative data so the panel renders with NO
// backend. The `providers` key is what flips the harness into wrapped mode —
// leaf specs without it are byte-for-byte unaffected.
import AppearancePanel from '../../components/settings/AppearancePanel.jsx';
import TitleTabs from '../../components/TitleTabs.jsx';
import GeneralTab from '../../components/settings/GeneralTab.jsx';
import StoragePanel from '../../components/settings/StoragePanel.jsx';
import ResetPanel from '../../components/settings/ResetPanel.jsx';
import UninstallPanel from '../../components/settings/UninstallPanel.jsx';
import { queryKeys } from '../../api/hooks.ts';

// Representative scan payloads for the two desktop-shell Storage panels, so the
// harness renders their loaded state (sizes, bars, the shared-cache row) with no
// backend. Sizes span B → GB on purpose: it's the spread the redesign is FOR.
const RESET_SCAN = [
  { key: 'ui_prefs', paths: [], size_bytes: 0, exists: true, shared: false, needs_restart: false },
  { key: 'history', paths: [], size_bytes: 0, exists: true, shared: false, needs_restart: false },
  {
    key: 'settings',
    paths: ['~/…/VoiceStudio/prefs.json'],
    size_bytes: 4096,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'content',
    paths: ['~/…/VoiceStudio/voices'],
    size_bytes: 5.4 * 1024 ** 3,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'engines',
    paths: ['~/…/VoiceStudio/engines'],
    size_bytes: 2.3 * 1024 ** 3,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'tools',
    paths: ['~/…/VoiceStudio/media_tools'],
    size_bytes: 96 * 1024 ** 2,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'models',
    paths: ['~/.cache/huggingface'],
    size_bytes: 14.2 * 1024 ** 3,
    exists: true,
    shared: true,
    needs_restart: true,
  },
  {
    key: 'caches',
    paths: ['~/…/VoiceStudio/gallery_cache'],
    size_bytes: 11 * 1024 ** 2,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'logs',
    paths: ['~/…/VoiceStudio/omnivoice.log'],
    size_bytes: 820,
    exists: true,
    shared: false,
    needs_restart: true,
  },
];
const UNINSTALL_SCAN = [
  {
    key: 'data',
    path: '~/Library/Application Support/OmniVoice',
    size_bytes: 720 * 1024,
    exists: true,
    shared: false,
  },
  {
    key: 'env',
    path: '~/Library/Application Support/com.debpalash.omnivoice-studio',
    size_bytes: 391,
    exists: true,
    shared: false,
  },
  { key: 'logs', path: '~/Library/Logs/OmniVoice', size_bytes: 4096, exists: true, shared: false },
  {
    key: 'models',
    path: '~/.cache/huggingface',
    size_bytes: 7.5 * 1024 ** 3,
    exists: true,
    shared: true,
  },
];

function Spec({ label, children }) {
  return (
    <div className="visual-spec">
      <span className="visual-spec__label">{label}</span>
      <div className="visual-spec__row">{children}</div>
    </div>
  );
}

const BADGE_TONES = ['neutral', 'brand', 'success', 'warn', 'danger', 'info', 'violet'];

const TAB_ITEMS = [
  { id: 'clone', label: 'Clone', icon: Mic },
  { id: 'design', label: 'Design', icon: Sparkles },
  { id: 'dub', label: 'Dub' },
];

const TABLE_COLS = [
  { key: 'name', label: 'Voice', flex: 2 },
  { key: 'lang', label: 'Lang', width: 80 },
  { key: 'dur', label: 'Length', width: 70, align: 'right' },
];

// Generated locally so waveform interaction checks need neither a backend nor media fixtures.
function WaveformPanFixture() {
  const [audioSrc] = React.useState(() => {
    const samples = 8000 * 30;
    const buffer = new ArrayBuffer(44 + samples * 2);
    const wav = new DataView(buffer);
    const text = (at, value) =>
      [...value].forEach((char, i) => wav.setUint8(at + i, char.charCodeAt(0)));
    text(0, 'RIFF');
    wav.setUint32(4, buffer.byteLength - 8, true);
    text(8, 'WAVE');
    text(12, 'fmt ');
    wav.setUint32(16, 16, true);
    wav.setUint16(20, 1, true);
    wav.setUint16(22, 1, true);
    wav.setUint32(24, 8000, true);
    wav.setUint32(28, 16000, true);
    wav.setUint16(32, 2, true);
    wav.setUint16(34, 16, true);
    text(36, 'data');
    wav.setUint32(40, samples * 2, true);
    for (let i = 0; i < samples; i++)
      wav.setInt16(44 + i * 2, Math.round(Math.sin(i * 0.17) * 8000), true);
    return URL.createObjectURL(new Blob([buffer], { type: 'audio/wav' }));
  });
  React.useEffect(() => () => URL.revokeObjectURL(audioSrc), [audioSrc]);
  return (
    <WaveformTimeline
      audioSrc={audioSrc}
      segments={[{ id: 'one', start: 5, end: 10, text: 'Drag the waveform to pan' }]}
    />
  );
}

function DubIdleStartFixture() {
  const [ingestUrl, setIngestUrl] = React.useState('');
  const [sourceLanguage, setSourceLanguage] = React.useState('auto');
  const [targetLanguage, setTargetLanguage] = React.useState('es');
  const [optionalOpen, setOptionalOpen] = React.useState(false);
  const noop = () => {};
  return (
    <div style={{ height: 760, minWidth: 0 }}>
      <IdleSkeleton
        t={i18n.t.bind(i18n)}
        uiLocale="en"
        dubVideoFile={null}
        activeProjectName=""
        dubFilename=""
        dubError=""
        dubJobId={null}
        dubStep="idle"
        dubFailure={null}
        asrInstall={null}
        handleInstallMissingAsr={noop}
        handleDubRetryTranscribe={noop}
        handleDubImportSrt={noop}
        dubLocalBlobUrl={null}
        dubPrepStage={null}
        dubPrepProgress={{ percent: null, speedBps: null, etaS: null, stageStartedAt: null }}
        handleDubAbort={noop}
        transcribeElapsed={0}
        transcribeProgress={null}
        dubDuration={0}
        dubNumSpeakers={null}
        setDubNumSpeakers={noop}
        handleDubUpload={noop}
        demoDismissed
        dismissDubDemo={noop}
        setDubVideoFile={noop}
        setDubInputType={noop}
        setDubStep={noop}
        fileToMediaUrl={async () => ({ audioUrl: null, videoUrl: null })}
        setDubLocalBlobUrl={noop}
        ingestUrl={ingestUrl}
        setIngestUrl={setIngestUrl}
        onIngestUrl={noop}
        fetchYtSubs={false}
        setFetchYtSubs={noop}
        youtubeCookieFile={null}
        setYoutubeCookieFile={noop}
        dubLangCode={targetLanguage}
        dubSourceLangCode={sourceLanguage}
        setDubSourceLangCode={setSourceLanguage}
        setDubLangCode={setTargetLanguage}
        setDubLang={noop}
        landingAdvOpen={optionalOpen}
        setLandingAdvOpen={setOptionalOpen}
        dubInstruct=""
        setDubInstruct={noop}
        onOpenQueue={noop}
      />
    </div>
  );
}

const DUB_SIDEBAR_PROJECTS = [
  {
    id: 'dub-project-1',
    name: 'Documentary voice-over',
    updated_at: '2026-09-08T12:00:00Z',
    duration: 128,
    video_path: 'documentary.mp4',
  },
  {
    id: 'dub-project-2',
    name: 'Product launch — Spanish',
    updated_at: '2026-09-07T12:00:00Z',
    duration: 64,
    video_path: 'product-launch.mov',
  },
];

const DUB_SIDEBAR_HISTORY = [
  {
    id: 'dub-history-1',
    filename: 'Documentary final.mp4',
    duration: 128,
    segments_count: 18,
    language: 'Spanish',
    language_code: 'es',
    job_data: { input_type: 'video' },
  },
  {
    id: 'dub-history-2',
    filename: 'Interview voice track.wav',
    duration: 76,
    segments_count: 9,
    language: 'French',
    language_code: 'fr',
    job_data: { input_type: 'audio' },
  },
];

function DubIdleWorkspaceFixture() {
  const noop = () => {};
  return (
    <div className="studio-with-history" style={{ height: 760, minWidth: 0 }}>
      <div className="studio-with-history__main">
        <DubIdleStartFixture />
      </div>
      <div className="studio-right">
        <DubWorkspaceSidebar
          projects={DUB_SIDEBAR_PROJECTS}
          activeProjectId={null}
          loadProject={noop}
          deleteProject={noop}
          renameProject={noop}
          dubHistory={DUB_SIDEBAR_HISTORY}
          restoreDubHistory={noop}
          deleteHistory={noop}
          clearHistory={noop}
        />
      </div>
    </div>
  );
}

const STORIES_CAST = [
  { id: 'narrator', name: 'Narrator', color: '#b8bb26', profileId: 'voice-aria' },
  { id: 'mara', name: 'Mara', color: '#d3869b', profileId: 'voice-mara' },
  { id: 'cole', name: 'Cole', color: '#83a598', profileId: 'voice-cole' },
];

const STORIES_TRACKS = [
  {
    id: 101,
    character: 'narrator',
    text: '# The Signal at Sundown',
    profileId: null,
    emotion: null,
    speed: null,
    generating: false,
    audioUrl: null,
  },
  {
    id: 102,
    character: 'narrator',
    text: 'The lighthouse had been silent for eleven winters.',
    profileId: null,
    emotion: null,
    speed: null,
    generating: false,
    audioUrl: null,
  },
  {
    id: 103,
    character: 'mara',
    text: 'Cole, did you hear that? [pause 0.4s] The old radio is calling us.',
    profileId: null,
    emotion: null,
    speed: 0.95,
    generating: false,
    audioUrl: null,
  },
  {
    id: 104,
    character: 'cole',
    text: 'I heard it. Stay close, and keep the lantern low.',
    profileId: null,
    emotion: null,
    speed: null,
    generating: false,
    audioUrl: null,
  },
];

const STORIES_PROJECT = {
  id: 'visual-story',
  name: 'The Signal at Sundown',
  cast: STORIES_CAST,
  tracks: STORIES_TRACKS,
  updatedAt: 1,
};

export const SPECS = {
  AudiobookWorkspace: {
    width: '100%',
    providers: {
      store: {
        script:
          '# Chapter One\n\nA light shone across the water.\n\n# Chapter Two\n\nThe boat returned safely.',
        lastOutput: '',
      },
      fetch: () => ({}),
    },
    render: () => (
      <div style={{ height: 800 }}>
        <AudiobookTab profiles={[]} />
      </div>
    ),
  },
  DubIdleStart: {
    width: '100%',
    providers: {},
    render: () => <DubIdleStartFixture />,
  },
  DubIdleWorkspace: {
    width: '100%',
    providers: {},
    render: () => <DubIdleWorkspaceFixture />,
  },
  StoriesWorkspaceLayout: {
    width: '100%',
    providers: {
      store: {
        cast: STORIES_CAST,
        storyTracks: STORIES_TRACKS,
        storyProjects: [STORIES_PROJECT],
        currentProjectId: STORIES_PROJECT.id,
      },
    },
    render: () => (
      <div style={{ height: 780, minWidth: 0 }}>
        <StoriesEditor
          profiles={[
            { id: 'voice-aria', name: 'Aria — warm narrator' },
            { id: 'voice-mara', name: 'Mara — intimate alto' },
            { id: 'voice-cole', name: 'Cole — steady baritone' },
          ]}
        />
      </div>
    ),
  },
  DubWorkspaceLayout: {
    width: '100%',
    providers: {},
    render: () => <DubWorkspaceFixture />,
  },
  DubSelectionLayout: {
    width: '100%',
    providers: {},
    render: () => (
      <div style={{ overflow: 'hidden' }}>
        <DubSelectionToolbar
          t={i18n.t.bind(i18n)}
          count={353}
          profiles={[
            { id: 'demo', name: 'VoiceStudio Demo Voice' },
            { id: 'narrator', name: 'Studio Narrator', instruct: 'calm' },
          ]}
          speakerClones={{ 'Speaker 1': {}, 'Speaker 2': {} }}
          onApply={() => {}}
          onDelete={() => {}}
          onClear={() => {}}
        />
      </div>
    ),
  },
  DubWaveformPan: {
    width: '100%',
    providers: {},
    render: () => <WaveformPanFixture />,
  },
  DubSegmentLayout: {
    width: '100%',
    providers: {},
    render: () => (
      <div
        className="dub-panel-right"
        style={{
          height: 760,
          width: '100%',
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <DubSegmentTable
          segments={Array.from({ length: 353 }, (_, i) => ({
            id: `segment-${i}`,
            start: 16.7 + i * 4,
            end: 19.5 + i * 4,
            text:
              i % 2
                ? "What happened? It's complicated. Oh, it's perfect. Complicated is the best."
                : 'Am I with you?',
            text_original:
              i % 2 ? 'An original line that should stay below the editable translation.' : '',
            speaker_id: `Speaker ${(i % 2) + 1}`,
            profile_id: `auto:speaker_${(i % 2) + 1}`,
            fit_status: { status: i % 2 ? 'overflows' : 'fits', overflow_s: 0.69 },
            rate_ratio: 0.73,
            ...(i === 1
              ? {
                  qc_flagged: true,
                  plan: {
                    status: 'impossible',
                    est_overrun_s: 1.5,
                    suggested_text: 'A shorter line.',
                  },
                }
              : {}),
          }))}
          profiles={[]}
          speakerClones={{ 'Speaker 1': {}, 'Speaker 2': {} }}
          selectedIds={new Set()}
          dubStep="done"
          dubProgress={{}}
          {...Object.fromEntries(
            [
              'Select',
              'SelectAll',
              'ClearSelection',
              'EditField',
              'Delete',
              'Restore',
              'Preview',
              'Split',
              'Merge',
              'Insert',
              'MoveResize',
            ].map((name) => [`on${name}`, () => {}]),
          )}
        />
      </div>
    ),
  },
  HeaderStatus: {
    providers: {
      query: (qc) => qc.setQueryData(queryKeys.sysinfo, { cpu_percent: 0, ram_percent: 0 }),
      fetch: (url) => (url.endsWith('/sysinfo') ? { cpu_percent: 0, ram_percent: 0 } : undefined),
    },
    render: () => <Header mode="dub" setMode={() => {}} modelStatus="idle" />,
  },
  Badge: {
    render: () => (
      <>
        <Spec label="tones">
          {BADGE_TONES.map((tone) => (
            <Badge key={tone} tone={tone}>
              {tone}
            </Badge>
          ))}
        </Spec>
        <Spec label="dot / size">
          <Badge tone="success" dot>
            online
          </Badge>
          <Badge tone="brand" size="xs">
            xs
          </Badge>
          <Badge tone="warn" size="sm">
            sm
          </Badge>
        </Spec>
      </>
    ),
  },

  Segmented: {
    render: () => (
      <>
        <Spec label="sm — middle active">
          <Segmented
            size="sm"
            value="b"
            onChange={() => {}}
            items={[
              { value: 'a', label: 'One' },
              { value: 'b', label: 'Two' },
              { value: 'c', label: 'Three' },
            ]}
          />
        </Spec>
        <Spec label="xs — first active">
          <Segmented
            size="xs"
            value="a"
            onChange={() => {}}
            items={[
              { value: 'a', label: 'Alpha' },
              { value: 'b', label: 'Beta' },
            ]}
          />
        </Spec>
      </>
    ),
  },

  Progress: {
    render: () => (
      <>
        <Spec label="tones @ 65%">
          {['brand', 'success', 'warn', 'danger'].map((tone) => (
            <div key={tone} style={{ width: '200px' }}>
              <Progress tone={tone} value={65} />
            </div>
          ))}
        </Spec>
        <Spec label="sizes @ 40%">
          {['xs', 'sm', 'md'].map((size) => (
            <div key={size} style={{ width: '200px' }}>
              <Progress size={size} value={40} />
            </div>
          ))}
        </Spec>
        <Spec label="indeterminate / no-shimmer">
          <div style={{ width: '200px' }}>
            <Progress />
          </div>
          <div style={{ width: '200px' }}>
            <Progress value={50} shimmer={false} />
          </div>
        </Spec>
      </>
    ),
  },

  Button: {
    render: () => (
      <>
        <Spec label="variants">
          <Button variant="primary">Primary</Button>
          <Button variant="subtle">Subtle</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
        </Spec>
        <Spec label="chip / preset / icon">
          <Button variant="chip">Chip</Button>
          <Button variant="chip" active>
            Active chip
          </Button>
          <Button variant="preset">Preset</Button>
          <Button variant="icon" aria-label="Delete">
            <Trash2 size={16} />
          </Button>
        </Spec>
        <Spec label="states">
          <Button variant="primary" leading={<Download size={14} />}>
            Leading
          </Button>
          <Button variant="primary" loading>
            Loading
          </Button>
          <Button variant="primary" disabled>
            Disabled
          </Button>
        </Spec>
      </>
    ),
  },

  ShadcnButton: {
    render: () => (
      <>
        <Spec label="variants">
          <ShadcnButton>Default</ShadcnButton>
          <ShadcnButton variant="secondary">Secondary</ShadcnButton>
          <ShadcnButton variant="outline">Outline</ShadcnButton>
          <ShadcnButton variant="ghost">Ghost</ShadcnButton>
          <ShadcnButton variant="destructive">Destructive</ShadcnButton>
          <ShadcnButton variant="link">Link</ShadcnButton>
        </Spec>
        <Spec label="sizes / icon">
          <ShadcnButton size="sm">Small</ShadcnButton>
          <ShadcnButton size="default">Default</ShadcnButton>
          <ShadcnButton size="lg">Large</ShadcnButton>
          <ShadcnButton size="icon" aria-label="Sparkles">
            <Sparkles />
          </ShadcnButton>
        </Spec>
        <Spec label="leading icon / disabled">
          <ShadcnButton>
            <Download /> Download
          </ShadcnButton>
          <ShadcnButton variant="outline">
            <Mic /> Record
          </ShadcnButton>
          <ShadcnButton disabled>Disabled</ShadcnButton>
        </Spec>
      </>
    ),
  },

  ShadcnInput: {
    render: () => (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, width: 300 }}>
        <Spec label="states">
          <ShadcnInput placeholder="Default" />
          <ShadcnInput defaultValue="With value" />
          <ShadcnInput defaultValue="Disabled" disabled />
          <ShadcnInput placeholder="Invalid" aria-invalid="true" />
        </Spec>
        <Spec label="types">
          <ShadcnInput type="email" placeholder="you@example.com" />
          <ShadcnInput type="password" defaultValue="secret" />
          <ShadcnInput type="file" />
        </Spec>
      </div>
    ),
  },

  Panel: {
    render: () => (
      <>
        <Spec label="glass + title + actions">
          <Panel
            variant="glass"
            title="Voice settings"
            actions={
              <Button variant="ghost" size="sm" leading={<Sparkles size={14} />}>
                Tune
              </Button>
            }
          >
            Body content sits on the panel surface.
          </Panel>
        </Spec>
        <Spec label="solid / flat">
          <Panel variant="solid" title="Solid">
            Solid surface body.
          </Panel>
          <Panel variant="flat" title="Flat">
            Flat surface body.
          </Panel>
        </Spec>
      </>
    ),
  },

  Input: {
    render: () => (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, width: 300 }}>
        <Spec label="sizes">
          <Input size="sm" placeholder="Small" />
          <Input size="md" placeholder="Medium" />
          <Input size="lg" placeholder="Large" />
        </Spec>
        <Spec label="states">
          <Input placeholder="Default" />
          <Input defaultValue="Disabled" disabled />
          <Input defaultValue="Invalid" aria-invalid="true" />
        </Spec>
        <Spec label="textarea / select">
          <Textarea placeholder="Textarea" rows={2} />
          <Select defaultValue="b">
            <option value="a">Option A</option>
            <option value="b">Option B</option>
          </Select>
        </Spec>
        <Spec label="field">
          <Field label="Name" hint="Your full name">
            <Input placeholder="Jane Doe" />
          </Field>
          <Field label="Email" error="Required field">
            <Input placeholder="you@example.com" />
          </Field>
          <Field label="Search" icon={<Search size={13} />}>
            <Input placeholder="Search…" />
          </Field>
        </Spec>
      </div>
    ),
  },

  SettingRow: {
    render: () => (
      <Panel variant="flat" padding="md">
        <SettingRow
          title="Auto-update models"
          subtitle="Download new engine weights in the background."
          control={<SettingsToggle checked onChange={() => {}} aria-label="Auto-update" />}
        />
        <SettingRow
          icon={Sparkles}
          title="Cinematic dubbing"
          subtitle="Use the LLM rewrite pass for natural phrasing."
          control={<SettingsToggle checked={false} onChange={() => {}} aria-label="Cinematic" />}
        />
        <SettingRow title="App version" control="0.3.8" mono />
      </Panel>
    ),
  },

  SettingsToggle: {
    render: () => (
      <>
        <Spec label="on / off">
          <SettingsToggle checked onChange={() => {}} aria-label="On" />
          <SettingsToggle checked={false} onChange={() => {}} aria-label="Off" />
        </Spec>
        <Spec label="disabled">
          <SettingsToggle checked disabled onChange={() => {}} aria-label="Disabled on" />
          <SettingsToggle checked={false} disabled onChange={() => {}} aria-label="Disabled off" />
        </Spec>
      </>
    ),
  },

  Slider: {
    render: () => (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, width: '100%' }}>
        <Spec label="md + label">
          <Slider value={42} onChange={() => {}} label="Stability" />
        </Spec>
        <Spec label="sm">
          <Slider value={70} onChange={() => {}} size="sm" />
        </Spec>
        <Spec label="no value bubble">
          <Slider value={30} onChange={() => {}} showValue={false} />
        </Spec>
      </div>
    ),
  },

  Table: {
    render: () => (
      <div style={{ display: 'flex', width: '100%', height: 160 }}>
        <Table>
          <Table.Toolbar search="voice" onSearch={() => {}} meta="42/42 · 3 sel" />
          <Table.Header columns={TABLE_COLS} />
          <div style={{ flex: 1 }} />
        </Table>
      </div>
    ),
  },

  Tabs: {
    render: () => (
      <>
        <Spec label="pill (md)">
          <Tabs items={TAB_ITEMS} value="clone" onChange={() => {}} />
        </Spec>
        <Spec label="pill (sm)">
          <Tabs items={TAB_ITEMS} value="design" onChange={() => {}} size="sm" />
        </Spec>
        <Spec label="underline">
          <Tabs items={TAB_ITEMS} value="dub" onChange={() => {}} variant="underline" />
        </Spec>
      </>
    ),
  },

  // ── Panels (provider-wrapped) ────────────────────────────────────────────

  // The titlebar tab strip (Settings → Appearance → Navigation style). Framed
  // the way it actually ships — recessed shelf above, content plane below —
  // because the whole point of the skin is the seam between the two: the
  // active tab has to read as one surface with the page under it.
  TitleTabs: {
    width: 1100,
    providers: {
      store: ({ theme }) => ({ theme: theme === 'default' ? 'gruvbox' : theme, locale: 'en' }),
    },
    render: () => (
      <div style={{ width: 1100 }}>
        <div className="header-area header-area--tabs">
          <div>
            <TitleTabs mode="dub" setMode={() => {}} />
          </div>
          <div
            style={{
              fontFamily: 'var(--chrome-font-mono)',
              fontSize: 10.5,
              color: 'var(--chrome-fg-dim)',
              paddingBottom: 8,
            }}
          >
            READY
          </div>
        </div>
        <div style={{ height: 72, background: 'var(--chrome-bg)' }} />
      </div>
    ),
  },

  // Store + i18n only — the simplest page-level target. Aligns the store's
  // active `theme` with the rendered data-theme variant so the highlighted
  // theme dot matches the snapshot's palette.
  AppearancePanel: {
    width: 640,
    providers: {
      store: ({ theme }) => ({
        theme: theme === 'default' ? 'gruvbox' : theme,
        uiScale: 1,
        font: 'inter',
        autoPlayPreview: true,
      }),
    },
    render: () => <AppearancePanel />,
  },

  // Store + i18n + a seeded react-query cache. `useSystemInfo()` would
  // otherwise spin forever with no backend; we pre-fill its cache entry with
  // a representative payload so the ffmpeg badge + advanced rows render real.
  GeneralTab: {
    width: 640,
    providers: {
      store: ({ theme }) => ({
        locale: 'en',
        theme: theme === 'default' ? 'gruvbox' : theme,
      }),
      query: (qc) => {
        qc.setQueryData(queryKeys.systemInfo, {
          app_version: '0.3.6',
          python: '3.12.4',
          platform: 'macOS-15.0-arm64',
          device: 'mps',
          ffmpeg_ok: true,
          ffmpeg_path: '/opt/homebrew/bin/ffmpeg',
          proxy_url: '',
          has_hf_token: true,
        });
      },
    },
    render: () => <GeneralTab />,
  },

  // Direct api/* fetch on mount (no react-query) — exercises the fetch stub.
  // StoragePanel GETs /api/settings/storage/models-dir as it mounts; the stub
  // returns a representative payload so it renders its loaded state, not the
  // error fallback a missing backend would otherwise produce.
  StoragePanel: {
    width: 640,
    providers: {
      fetch: (url) => {
        if (url.includes('/api/settings/storage/models-dir')) {
          return {
            configured: '',
            effective: '/Users/~/Library/Caches/huggingface',
            default: '/Users/~/Library/Caches/huggingface',
            restart_required: false,
          };
        }
        return undefined;
      },
    },
    render: () => <StoragePanel />,
  },

  // The scoped-reset panel, advanced list expanded so the full row treatment —
  // icon, size, proportional bar, dimmed path, the shared-cache caution — is on
  // screen at once.
  ResetPanel: {
    width: 640,
    providers: {
      invoke: (cmd) => (cmd === 'reset_scan' ? RESET_SCAN : null),
    },
    render: () => <ResetPanel _forceAdvanced />,
  },

  // The uninstaller list, with the shared HF cache in its own "Optional" group.
  UninstallPanel: {
    width: 640,
    providers: {
      invoke: (cmd) => (cmd === 'uninstall_scan' ? UNINSTALL_SCAN : null),
    },
    render: () => <UninstallPanel />,
  },
};
