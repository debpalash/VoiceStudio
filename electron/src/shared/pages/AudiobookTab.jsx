import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileText, Users, BookText } from 'lucide-react';
import {
  audiobookPlan,
  audiobookGenerate,
  audiobookResume,
  audiobookUploadCover,
  audiobookPreviewChapter,
  audiobookImport,
} from '../api/audiobook';
import { audioUrl } from '../api/generate';
import { useEngines } from '../api/hooks';
import { consumeLongformStream } from '../utils/longformStream';
import { useAppStore } from '../store';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs';
import { overridesToRequest } from '../components/audiobook/AudiobookOverrides';
import GenerationProgress from '../components/audiobook/GenerationProgress';
import PlanList from '../components/audiobook/PlanList';
import AudiobookResult from '../components/audiobook/AudiobookResult';
import StatsBar from '../components/audiobook/StatsBar';
import ValidationWarnings from '../components/audiobook/ValidationWarnings';
import AudiobookHero from '../components/audiobook/AudiobookHero';
import AudiobookScriptPanel from '../components/audiobook/AudiobookScriptPanel';
import AudiobookVoicesPanel from '../components/audiobook/AudiobookVoicesPanel';
import AudiobookBookPanel from '../components/audiobook/AudiobookBookPanel';
import AudiobookRecovery from '../components/audiobook/AudiobookRecovery';
import { useAudiobookLexicon } from '../hooks/useAudiobookLexicon';
import { parseCastNames, validateScript } from '../utils/audiobookScript';
import { SAMPLE_AUDIOBOOK_SCRIPT } from '../data/sampleAudiobook';

// Stable empty-cast fallback: a literal `?? {}` mints a new object every render,
// which defeats the useMemos keyed on voiceCast (they'd recompute every render).
const EMPTY_CAST = Object.freeze({});

// Write → Cast → Produce: the audiobook pipeline as workspace tabs, mirroring
// the clone workspace (Script / Voice + pinned actions). Persisted per visit;
// validated on read so a stale value can never strand the workspace.
const BOOK_TABS = ['script', 'voices', 'book'];
const BOOK_TAB_KEY = 'omnivoice.audiobook.tab';

function readStoredBookTab() {
  try {
    const stored = localStorage.getItem(BOOK_TAB_KEY);
    return BOOK_TABS.includes(stored) ? stored : 'script';
  } catch {
    return 'script';
  }
}

/**
 * AudiobookTab — turn a chapter-delimited script into a chapterized m4b.
 *
 * Markdown `# H1` headings delimit chapters; inline `[voice:NAME]` and
 * `[pause …]` are honoured by the backend parser. "Preview plan" shows the
 * parsed chapters; "Create" streams synthesis progress and offers the m4b.
 */
export default function AudiobookTab({ profiles = [] }) {
  const { t } = useTranslation();
  // Workspace tab (Script / Voices / Book) — local + persisted, like the
  // catalogue pane. Manual activation so arrowing across the strip never
  // yanks the panel out from under keyboard users.
  const [bookTab, setBookTabRaw] = useState(readStoredBookTab);
  const setBookTab = useCallback((next) => {
    setBookTabRaw(next);
    try {
      localStorage.setItem(BOOK_TAB_KEY, next);
    } catch {
      /* private mode / quota — the tab still switches, it just won't persist */
    }
  }, []);
  // Persisted via the unified LongformProject store (#31b) — book identity,
  // script, voice, and output prefs now survive a tab switch / reload (they
  // used to live in component useState and evaporate).
  const text = useAppStore((s) => s.script);
  const setText = useAppStore((s) => s.setScript);
  const defaultVoice = useAppStore((s) => s.defaultVoice) ?? ''; // select coerces null→''
  const setOutputPrefs = useAppStore((s) => s.setOutputPrefs);
  const setProjectMeta = useAppStore((s) => s.setProjectMeta);
  const setDefaultVoice = (v) => setOutputPrefs({ defaultVoice: v || null });
  // Multi-voice cast map (#1217): [voice:NAME] → profile id, store-backed so a
  // book's voice assignments survive a tab switch / reload.
  const voiceCast = useAppStore((s) => s.voiceCast) ?? EMPTY_CAST;
  const setVoiceCast = useAppStore((s) => s.setVoiceCast);
  // Language pick + expressive overrides (#1208) — store-backed so a book's
  // tuning survives a tab switch / reload (same persistence as the lexicon).
  const language = useAppStore((s) => s.language) ?? 'Auto';
  const setLanguage = (v) => setOutputPrefs({ language: v || 'Auto' });
  const overrides = useAppStore((s) => s.overrides);
  const setLongformOverrides = useAppStore((s) => s.setLongformOverrides);
  // Derived from the shared engine cache so a switch elsewhere updates these
  // controls immediately instead of leaving a stale mount-time snapshot.
  const { data: engines } = useEngines();
  const activeTts = engines?.tts?.active;
  const emotionSupported = !!engines?.tts?.backends?.find((engine) => engine.id === activeTts)
    ?.supports_emotion;
  const [plan, setPlan] = useState(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  // Per-chapter live progress (#1216): [{ title, status }] where status is
  // pending | rendering | done | cached | failed. Drives GenerationProgress.
  const [chapters, setChapters] = useState([]);
  const [assembling, setAssembling] = useState(false);
  const [stopped, setStopped] = useState(false);
  // Store-backed (#1139): the finished render's filename used to be component
  // useState, so the player + Download link vanished on the first tab switch —
  // users reported "no way to export". It now survives tab switches/reloads.
  const output = useAppStore((s) => s.lastOutput);
  const setOutput = useAppStore((s) => s.setLastOutput);
  const outputScript = useAppStore((s) => s.lastOutputScript);
  const outputChapters = useAppStore((s) => s.lastOutputChapters);
  const setOutputSnapshot = useAppStore((s) => s.setLastOutputSnapshot);
  const [error, setError] = useState('');
  const [done, setDone] = useState(null); // {cached_chapters, failed_chapters}
  const [chapterPrev, setChapterPrev] = useState({}); // index → {url, loading}
  const abortRef = useRef(false);
  const abortControllerRef = useRef(null); // per-generation fetch AbortController
  const chaptersRef = useRef([]);

  // Abort an in-flight generation when the tab unmounts. Without this, leaving
  // mid-render keeps the stream (and the backend job) running, and a late
  // done/error event could clobber the store's output from a generation the
  // user started after coming back. Mirrors the manual Stop.
  useEffect(
    () => () => {
      abortRef.current = true;
      abortControllerRef.current?.abort();
    },
    [],
  );

  // Output prefs + metadata (embedded in the file; players show these) — now
  // store-backed. `meta` is default-filled so every controlled input gets a
  // defined string (an empty store record never flips a controlled→uncontrolled).
  const format = useAppStore((s) => s.outputFormat); // 'm4b' | 'mp3'
  const setFormat = (v) => setOutputPrefs({ outputFormat: v });
  const loudness = useAppStore((s) => s.loudness); // 'off' | 'acx' | 'podcast'
  const setLoudness = (v) => setOutputPrefs({ loudness: v });
  const metaStore = useAppStore((s) => s.meta);
  const meta = {
    title: '',
    author: '',
    narrator: '',
    year: '',
    genre: '',
    description: '',
    ...metaStore,
  };
  const setMetaField = (k) => (e) => setProjectMeta({ [k]: e.target.value });

  // Cover stays component-local (a File/blob can't persist to localStorage;
  // coverRef persistence is a noted follow-up).
  const [coverFile, setCoverFile] = useState(null);
  const [coverPreview, setCoverPreview] = useState('');

  // Pronunciation lexicon: editable {word → respelling} rows (extracted to a
  // hook so this page stays under the max-lines lint, #1217).
  const { lex, lexDict, setLexRow, addLexRow, removeLexRow } = useAudiobookLexicon();

  // Cast + validation derive purely from the script (#1217). castNames drives
  // the Cast panel; voiceMap is the minimal name→profile map actually present in
  // the script (stray store mappings are excluded so the cache key stays stable
  // and an absent map keeps today's render). Warnings are non-blocking hints.
  const textareaRef = useRef(null);
  const [warningsDismissed, setWarningsDismissed] = useState(false);
  const castNames = useMemo(() => parseCastNames(text), [text]);
  const voiceMap = useMemo(() => {
    const m = {};
    for (const name of castNames) if (voiceCast[name]) m[name] = voiceCast[name];
    return m;
  }, [castNames, voiceCast]);
  const voiceMapArg = Object.keys(voiceMap).length ? voiceMap : null;
  const warnings = useMemo(() => {
    const mappedNames = Object.keys(voiceCast).filter((n) => voiceCast[n]);
    return validateScript(text, { mappedNames, profileIds: profiles.map((p) => p.id) });
  }, [text, voiceCast, profiles]);

  // Tab badges (the inspector's counts, moved onto the strip): cast size on
  // Voices, filled details + lexicon rows on Book.
  const detailCount =
    Object.values(meta).filter((value) => value?.trim()).length + (coverPreview ? 1 : 0);
  const lexiconCount = lex.filter((row) => row.word.trim() || row.say.trim()).length;

  const onCoverPick = useCallback(
    (e) => {
      const f = e.target.files?.[0];
      if (!f) return;
      setCoverFile(f);
      setCoverPreview(URL.createObjectURL(f));
    },
    [setCoverFile, setCoverPreview],
  );
  const clearCover = useCallback(() => {
    setCoverFile(null);
    if (coverPreview) URL.revokeObjectURL(coverPreview);
    setCoverPreview('');
  }, [coverPreview, setCoverFile, setCoverPreview]);
  // Revoke the cover blob URL when it's replaced or the tab unmounts (React
  // doesn't reclaim object URLs on its own).
  useEffect(
    () => () => {
      if (coverPreview) URL.revokeObjectURL(coverPreview);
    },
    [coverPreview],
  );

  const [importing, setImporting] = useState(false);

  const onPreview = useCallback(async () => {
    setError('');
    setPlanLoading(true);
    try {
      setPlan(await audiobookPlan({ text, default_voice: defaultVoice || null }));
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setPlanLoading(false);
    }
  }, [text, defaultVoice]);

  const onImport = useCallback(
    async (e) => {
      const f = e.target.files?.[0];
      e.target.value = ''; // allow re-importing the same file
      if (!f) return;
      setError('');
      setImporting(true);
      try {
        const r = await audiobookImport(f);
        setText(r.text);
        setPlan(null);
      } catch (err) {
        setError(t('audiobook.import_failed', { message: err?.message || String(err) }));
      } finally {
        setImporting(false);
      }
    },
    [t],
  );

  // Drop the demo story straight into the editor so a first-timer can hit
  // Preview/Create immediately and hear every markup capability. Guard against
  // clobbering real work — only prompt when there's existing script content.
  const loadSample = useCallback(() => {
    if (text.trim() && !window.confirm(t('audiobook.load_sample_confirm'))) return;
    setText(SAMPLE_AUDIOBOOK_SCRIPT);
    setPlan(null);
    setError('');
  }, [text, t, setText]);

  const onPreviewChapter = useCallback(
    async (i) => {
      setError('');
      setChapterPrev((p) => ({ ...p, [i]: { ...p[i], loading: true } }));
      try {
        const lexicon = lexDict();
        const r = await audiobookPreviewChapter({
          text,
          chapter_index: i,
          default_voice: defaultVoice || null,
          lexicon: Object.keys(lexicon).length ? lexicon : null,
          // Cast map MUST match the full render's so a preview warms the exact
          // cache slot the render reuses (preview/render parity, #1217).
          voice_map: voiceMapArg,
          // Same expressive fields as the full render so a preview warms the
          // exact cache slot the render reuses (preview/render parity, #1208).
          ...overridesToRequest(overrides, language),
        });
        setChapterPrev((p) => ({ ...p, [i]: { url: audioUrl(r.output), loading: false } }));
      } catch (e) {
        setChapterPrev((p) => ({ ...p, [i]: { ...p[i], loading: false } }));
        setError(e?.message || String(e));
      }
    },
    [text, defaultVoice, lex, overrides, language, voiceMapArg],
  );

  const consumeGeneration = useCallback(
    async (res, snapshotScript = null) => {
      await consumeLongformStream(
        res,
        (evt) => {
          if (evt.type === 'started') {
            // Seed the per-chapter list; chapter 0 starts rendering immediately.
            chaptersRef.current = Array.from({ length: evt.chapters }, (_, i) => ({
              title: '',
              status: i === 0 ? 'rendering' : 'pending',
            }));
            setChapters(chaptersRef.current);
          } else if (evt.type === 'chapter') {
            // A chapter finished (cached vs freshly rendered per evt.cached); the
            // next pending chapter becomes the one rendering. duration_s feeds
            // the synced-lyrics player's chapter timeline.
            chaptersRef.current = chaptersRef.current.map((c, j) =>
              j === evt.index
                ? {
                    ...c,
                    title: evt.title,
                    status: evt.cached ? 'cached' : 'done',
                    duration_s: evt.duration_s,
                  }
                : j === evt.index + 1 && c.status === 'pending'
                  ? { ...c, status: 'rendering' }
                  : c,
            );
            setChapters(chaptersRef.current);
          } else if (evt.type === 'chapter_error') {
            chaptersRef.current = chaptersRef.current.map((c, j) =>
              j === evt.index
                ? {
                    ...c,
                    title: evt.title,
                    status: 'failed',
                    error: evt.reason || evt.error || '',
                  }
                : j === evt.index + 1 && c.status === 'pending'
                  ? { ...c, status: 'rendering' }
                  : c,
            );
            setChapters(chaptersRef.current);
          } else if (evt.type === 'assembling') {
            setAssembling(true);
          } else if (evt.type === 'stopped') {
            setStopped(true);
          } else if (evt.type === 'done') {
            if (snapshotScript === null) setOutput(evt.output);
            else setOutputSnapshot(evt.output, snapshotScript, chaptersRef.current);
            setDone({
              cached_chapters: evt.cached_chapters || 0,
              failed_chapters: evt.failed_chapters || [],
            });
          } else if (evt.type === 'error') {
            setError(evt.error || 'synthesis failed');
          }
        },
        { isAborted: () => abortRef.current, signal: abortControllerRef.current?.signal },
      );
      // consumeLongformStream returns (never throws) on a caller-initiated stop.
      if (abortRef.current) setStopped(true);
    },
    [setOutput, setOutputSnapshot],
  );

  const onCreate = useCallback(async () => {
    setError('');
    setOutput('');
    setDone(null);
    setStopped(false);
    setChapters([]);
    chaptersRef.current = [];
    setAssembling(false);
    setGenerating(true);
    abortRef.current = false;
    // A per-generation AbortController: Stop aborts it, which cancels the fetch
    // end-to-end so the backend sees the disconnect and stops rendering (#1216).
    const controller = new AbortController();
    abortControllerRef.current = controller;
    try {
      let cover_path = null;
      if (coverFile) {
        cover_path = (await audiobookUploadCover(coverFile)).path;
      }
      // Only send metadata fields the user actually filled in.
      const metadata = Object.fromEntries(Object.entries(meta).filter(([, v]) => v && v.trim()));
      const lexicon = lexDict();
      const res = await audiobookGenerate(
        {
          text,
          default_voice: defaultVoice || null,
          format,
          loudness: loudness === 'off' ? null : loudness,
          cover_path,
          metadata: Object.keys(metadata).length ? metadata : null,
          lexicon: Object.keys(lexicon).length ? lexicon : null,
          // Multi-voice cast map (#1217): [voice:NAME] → profile id. Absent when
          // empty, so a single-voice book stays byte-identical to before.
          voice_map: voiceMapArg,
          // language pick + expressive/quality overrides + cache opt-out (#1208).
          // Only non-default values are emitted, so an untouched panel keeps the
          // request byte-identical to before.
          ...overridesToRequest(overrides, language),
        },
        { signal: controller.signal },
      );
      await consumeGeneration(res, text);
    } catch (e) {
      // A Stop that lands before/around the first byte aborts the fetch →
      // AbortError. Treat every self-initiated abort as "Stopped", not an error.
      if (abortRef.current || e?.name === 'AbortError') setStopped(true);
      else setError(e?.message || String(e));
    } finally {
      setGenerating(false);
      setAssembling(false);
      abortControllerRef.current = null;
    }
  }, [
    text,
    defaultVoice,
    format,
    loudness,
    coverFile,
    meta,
    lex,
    overrides,
    language,
    voiceMapArg,
    consumeGeneration,
  ]);

  const onResume = useCallback(
    async (jobId) => {
      let accepted = false;
      setError('');
      setOutput('');
      setDone(null);
      setStopped(false);
      setChapters([]);
      chaptersRef.current = [];
      setAssembling(false);
      setGenerating(true);
      abortRef.current = false;
      const controller = new AbortController();
      abortControllerRef.current = controller;
      try {
        const res = await audiobookResume(jobId, { signal: controller.signal });
        accepted = true;
        // The manifest contains the original chapter plan, but the endpoint does
        // not expose its source script. Keep playback, but do not attach whatever
        // script happens to be open now as misleading synced lyrics.
        await consumeGeneration(res);
      } catch (error) {
        if (abortRef.current || error?.name === 'AbortError') setStopped(true);
        else {
          setError(t('audiobook.resume_failed', { message: error?.message || String(error) }));
        }
      } finally {
        setGenerating(false);
        setAssembling(false);
        abortControllerRef.current = null;
      }
      return accepted;
    },
    [consumeGeneration, setOutput, t],
  );

  // Stop = abort the fetch (cancels the request → backend disconnect) AND flip
  // the isAborted flag the stream consumer polls, so the read loop releases too.
  const onStop = useCallback(() => {
    abortRef.current = true;
    abortControllerRef.current?.abort();
  }, []);

  const busy = planLoading || generating || importing;
  const canRun = text.trim().length > 0 && !busy;
  // Cmd/Ctrl+Enter in the editor triggers Create when runnable; a no-op while
  // generating (canRun is false when busy).
  const onScriptKeyDown = useCallback(
    (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        if (canRun) onCreate();
      }
    },
    [canRun, onCreate],
  );

  // The pinned status rail (warnings / progress / result / plan) only takes
  // space once there is something to show — a fresh book is just the tabs.
  const showWarnings = !warningsDismissed && !generating && warnings.length > 0;
  const hasStatus =
    showWarnings || error || generating || (stopped && !generating) || output || plan;

  const tabDefs = useMemo(
    () => [
      { id: 'script', label: t('audiobook.tab_script'), Icon: FileText, badge: 0 },
      { id: 'voices', label: t('audiobook.tab_voices'), Icon: Users, badge: castNames.length },
      {
        id: 'book',
        label: t('audiobook.tab_book'),
        Icon: BookText,
        badge: detailCount + lexiconCount,
      },
    ],
    [t, castNames.length, detailCount, lexiconCount],
  );

  return (
    <div className="audiobook-tab flex h-full flex-col box-border px-[1.25rem] py-[1rem] gap-[10px] max-[1120px]:overflow-y-auto">
      <AudiobookHero
        t={t}
        busy={busy}
        importing={importing}
        planLoading={planLoading}
        generating={generating}
        canRun={canRun}
        onImport={onImport}
        onLoadSample={loadSample}
        onPreview={onPreview}
        onCreate={onCreate}
        onStop={onStop}
      />

      <AudiobookRecovery t={t} generating={generating} onResume={onResume} />

      <Tabs
        value={bookTab}
        onValueChange={setBookTab}
        activationMode="manual"
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <div className="relative z-10 flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 px-1 pb-3 pt-1 max-[600px]:px-0">
          <TabsList
            aria-label={t('audiobook.title')}
            className="grid h-auto w-auto min-w-0 flex-[1_1_320px] grid-cols-3 gap-[3px] rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-bg)] p-[3px]"
          >
            {tabDefs.map(({ id, label, Icon, badge }) => (
              <TabsTrigger
                key={id}
                value={id}
                data-book-tab={id}
                disabled={busy}
                className="min-h-11 h-auto min-w-0 cursor-pointer whitespace-normal rounded-[var(--chrome-radius-pill)] border border-transparent bg-transparent px-3 py-2 text-sm font-medium text-[color:var(--chrome-fg-muted)] transition-colors data-[state=active]:border-[var(--chrome-accent-border)] data-[state=active]:bg-[var(--chrome-accent-bg)] data-[state=active]:font-semibold data-[state=active]:text-[color:var(--chrome-accent)] data-[state=active]:shadow-none dark:data-[state=active]:border-[var(--chrome-accent-border)] dark:data-[state=active]:bg-[var(--chrome-accent-bg)] dark:data-[state=active]:text-[color:var(--chrome-accent)] hover:data-[state=inactive]:bg-[var(--chrome-hover-bg)]"
              >
                <Icon size={16} aria-hidden="true" />
                {label}
                {badge > 0 && (
                  <span
                    className="rounded-full bg-[var(--chrome-hover-bg)] px-[8px] text-[0.7rem] [font-variant-numeric:tabular-nums]"
                    aria-hidden="true"
                  >
                    {badge}
                  </span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        <TabsContent value={bookTab} className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col">
            {bookTab === 'script' && (
              <AudiobookScriptPanel
                t={t}
                text={text}
                setText={setText}
                textareaRef={textareaRef}
                onScriptKeyDown={onScriptKeyDown}
                warningsDismissed={warningsDismissed}
                setWarningsDismissed={setWarningsDismissed}
              />
            )}
            {bookTab === 'voices' && (
              <AudiobookVoicesPanel
                t={t}
                profiles={profiles}
                defaultVoice={defaultVoice}
                setDefaultVoice={setDefaultVoice}
                language={language}
                setLanguage={setLanguage}
                castNames={castNames}
                voiceCast={voiceCast}
                setVoiceCast={setVoiceCast}
                overrides={overrides}
                setOverrides={setLongformOverrides}
                emotionSupported={emotionSupported}
              />
            )}
            {bookTab === 'book' && (
              <AudiobookBookPanel
                t={t}
                format={format}
                setFormat={setFormat}
                loudness={loudness}
                setLoudness={setLoudness}
                coverPreview={coverPreview}
                onCoverPick={onCoverPick}
                clearCover={clearCover}
                meta={meta}
                setMetaField={setMetaField}
                lex={lex}
                setLexRow={setLexRow}
                addLexRow={addLexRow}
                removeLexRow={removeLexRow}
                detailCount={detailCount}
                lexiconCount={lexiconCount}
              />
            )}
          </div>
        </TabsContent>
      </Tabs>

      {hasStatus && (
        <div
          className="flex shrink-0 flex-col gap-[9px] overflow-y-auto rounded-[12px] bg-[var(--color-bg-elev-2)] p-[10px]"
          data-testid="audiobook-status-rail"
        >
          {showWarnings && (
            <ValidationWarnings
              t={t}
              warnings={warnings}
              onDismiss={() => setWarningsDismissed(true)}
            />
          )}

          {error && (
            <div className="error-banner" role="alert">
              {error}
            </div>
          )}

          {generating && <GenerationProgress t={t} chapters={chapters} assembling={assembling} />}

          {stopped && !generating && (
            <div className="audiobook-progress" role="status">
              {t('audiobook.stopped_note')}
            </div>
          )}

          {output && (
            <AudiobookResult
              t={t}
              output={output}
              done={done}
              script={outputScript}
              chapters={outputChapters}
            />
          )}

          {plan && (
            <PlanList
              t={t}
              plan={plan}
              chapterPrev={chapterPrev}
              onPreviewChapter={onPreviewChapter}
              busy={busy}
            />
          )}
        </div>
      )}
    </div>
  );
}
