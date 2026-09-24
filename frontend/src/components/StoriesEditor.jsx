/**
 * StoriesEditor — multi-track audiobook / story studio (Phase 1).
 *
 * Line-card model: each line has a character (from the Cast), an optional
 * per-line voice override, and editable text with [pause]/[voice:] markers.
 * A Cast panel maps each character → a voice once; lines inherit it. "Generate"
 * stitches every line (+ pauses) into a single downloadable audiobook WAV.
 * State persists via the zustand longformSlice (IndexedDB, with bounded prefs
 * and ids in the root localStorage envelope).
 *
 * Spec: docs/superpowers/specs/2026-05-30-stories-editor-studio-design.md
 */
import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import {
  Plus,
  Play,
  Trash2,
  GripVertical,
  BookOpen,
  Mic,
  Download,
  Scissors,
  Pause as PauseIcon,
  Users,
  X,
  Upload,
  Sparkles,
  SlidersHorizontal,
  Folder,
  Layers,
  Bookmark,
  FileText,
  Drama,
  Timer,
  ChartColumn,
  Hourglass,
  Laugh,
  Wind,
  CircleQuestionMark,
  Zap,
  CircleCheck,
  Annoyed,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { Button, Menu } from '../ui';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import VoiceSelector from './VoiceSelector';
import SearchableSelect from './SearchableSelect';
import { useAppStore } from '../store';
import { recordValueMoment } from '../utils/donationMoments';
import {
  parseStoryText,
  hasStoryMarkers,
  applyInlineVoice,
  insertToken,
} from '../utils/storyTokens';
import { parseScript } from '../utils/parseScript';
import { importToText } from '../utils/importStory';
import {
  DEFAULT_SPLIT_MODE,
  DEFAULT_SPLIT_MAX,
  SPLIT_MODES,
  splitStoryText,
} from '../utils/splitStoryText';
import { readTextFile } from '../utils/readTextFile';
import { generateSpeech, audioUrl } from '../api/generate';
import { playBlobAudio } from '../utils/media';
import { stopActivePlayback } from '../utils/playback';
import { downloadMedia } from '../utils/mediaDownload';
import { encodeAudio } from '../api/stories';
import { longformRender } from '../api/audiobook';
import { exportStems } from '../utils/storyExport';
import { storyToSpans } from '../utils/storyToSpans';
import { consumeLongformStream } from '../utils/longformStream';
import { reorder } from '../utils/storyReorder';
import { effectiveProfile, effectiveSpeed, castMember, nextCastColor } from '../utils/storyCast';
import { askConfirm } from '../utils/dialog';
import { SAMPLE_STORY_CAST, SAMPLE_STORY_LINES, SAMPLE_STORY_NAME } from '../data/sampleStory';

// ── Shared class strings (replacing the old stories-* BEM chrome) ─────────
const NAME_INPUT =
  'bg-bg-elev-2 border border-border rounded-sm text-fg [font-size:var(--text-xs)] px-[8px] py-[4px]';
const SELECT_CHROME =
  'bg-bg-elev-2 border border-border rounded-md text-fg [font-size:var(--text-xs)] px-[6px] py-[4px] [font-family:var(--font-sans)] [color-scheme:dark]';
const DEL_BTN =
  'bg-transparent text-fg-subtle cursor-pointer w-[26px] h-[26px] flex items-center justify-center rounded-md hover:enabled:text-danger hover:enabled:bg-white/[0.06] focus-visible:[box-shadow:var(--focus-ring)] disabled:opacity-35 disabled:cursor-not-allowed';
const RESET_BTN =
  'bg-transparent border border-border text-fg-subtle [font-size:var(--text-xs)] px-[8px] py-[2px] rounded-sm cursor-pointer hover:text-fg';
const SPEED_RANGE = 'w-[120px]';
const TRACK_BTN =
  'w-[26px] h-[26px] flex items-center justify-center bg-transparent text-fg-subtle cursor-pointer rounded-md [transition:color_0.15s,background_0.15s,opacity_0.15s] p-0 hover:bg-white/[0.06] focus-visible:[box-shadow:var(--focus-ring)]';

function releasePreview(track) {
  if (track.audioUrl) URL.revokeObjectURL(track.audioUrl);
}

// Trigger a browser download for a Blob.
function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// A chapter line is any track whose text is a markdown heading (`# …`). It
// renders as a section bar (no voice/tune/preview), and storyExport keys its
// chapter cues off the same prefix — keep the two in sync.
// Lenient on purpose: a heading with an empty title is still `# ` (or `#`), and
// it must stay a chapter while the user edits the title — otherwise clearing the
// text would flip the bar back into a voiced line card mid-edit.
const isChapterText = (s) => /^\s*#{1,6}(\s|$)/.test(s || '');

let _trackId = 0;
function makeTrack(character = 'narrator', text = '') {
  return {
    id: ++_trackId,
    character,
    text,
    profileId: null,
    emotion: null,
    speed: null,
    generating: false,
    audioUrl: null,
  };
}

function genCastId() {
  const rnd = Math.random().toString(36).slice(2, 8);
  return `c_${rnd}`;
}

// Curated inline emotion/sound tags (a subset of utils/constants TAGS) for the
// per-line tone drawer. Inserting a tag is the model-native way to direct tone.
const STORY_TONES = [
  { tag: '[laughter]', icon: Laugh, key: 'laugh' },
  { tag: '[sigh]', icon: Wind, key: 'sigh' },
  { tag: '[question-en]', icon: CircleQuestionMark, key: 'question' },
  { tag: '[surprise-wa]', icon: Zap, key: 'surprise' },
  { tag: '[confirmation-en]', icon: CircleCheck, key: 'confirm' },
  { tag: '[dissatisfaction-hnn]', icon: Annoyed, key: 'dissatisfaction' },
];

const DEFAULT_SAMPLE_KEY = 'ov_stories_default_sample_v2';

export default function StoriesEditor({ profiles = [] }) {
  const { t } = useTranslation();

  // ── Persisted project state (zustand) ──────────────────────────────────
  const tracks = useAppStore((s) => s.storyTracks);
  const setStoryTracks = useAppStore((s) => s.setStoryTracks);
  const cast = useAppStore((s) => s.cast);
  const setCast = useAppStore((s) => s.setCast);
  const upsertCastMember = useAppStore((s) => s.upsertCastMember);
  const removeCastMember = useAppStore((s) => s.removeCastMember);
  const setCharacterVoice = useAppStore((s) => s.setCharacterVoice);
  const storyProjects = useAppStore((s) => s.storyProjects);
  const currentProjectId = useAppStore((s) => s.currentProjectId);
  const saveProject = useAppStore((s) => s.saveProject);
  const loadProject = useAppStore((s) => s.loadProject);
  const newProject = useAppStore((s) => s.newProject);
  const deleteProject = useAppStore((s) => s.deleteProject);

  // Proxy so existing `setTracks(prev => …)` call shapes keep working.
  const setTracks = useCallback(
    (updater) => {
      const cur = useAppStore.getState().storyTracks;
      setStoryTracks(typeof updater === 'function' ? updater(cur) : updater);
    },
    [setStoryTracks],
  );

  // Reseed the id counter from persisted tracks so new lines never collide.
  useEffect(() => {
    const maxId = tracks.reduce((m, tk) => Math.max(m, tk.id || 0), 0);
    if (maxId > _trackId) _trackId = maxId;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [activeTrack, setActiveTrack] = useState(null);
  // Bumped by clearScript so a preview that was still generating when the
  // script went away never plays or writes audio back for a deleted line.
  const previewGenRef = useRef(0);
  const playingPreviewRef = useRef(null);
  const [activeTab, setActiveTab] = useState('script');
  const [splitOpen, setSplitOpen] = useState(false);
  const [splitText, setSplitText] = useState('');
  const [splitMode, setSplitMode] = useState(DEFAULT_SPLIT_MODE);
  const [splitMax, setSplitMax] = useState(DEFAULT_SPLIT_MAX.sentences);
  const [exporting, setExporting] = useState(false);
  const [exportPct, setExportPct] = useState(0);
  const [expandedLine, setExpandedLine] = useState(null);
  const [projectName, setProjectName] = useState('');
  const [exportFormat, setExportFormat] = useState('m4b');
  const toggleSplit = useCallback(() => setSplitOpen((open) => !open), []);
  // Global reading speed (#415): one speed for every line without its own
  // per-track override. UI preference → persisted in localStorage (survives
  // restarts; not part of the project state, so no slice migration).
  const [globalSpeed, setGlobalSpeedState] = useState(() => {
    try {
      const v = parseFloat(localStorage.getItem('ov_stories_global_speed'));
      return Number.isFinite(v) && v >= 0.5 && v <= 2 ? v : 1;
    } catch {
      return 1;
    }
  });
  const setGlobalSpeed = useCallback((v) => {
    setGlobalSpeedState(v);
    try {
      localStorage.setItem('ov_stories_global_speed', String(v));
    } catch {
      /* noop */
    }
  }, []);
  const trackTextRefs = useRef(new Map());
  const fileInputRef = useRef(null);
  const dragId = useRef(null);
  const sampleBootstrapRef = useRef(false);
  const sampleVoicesBackfilledRef = useRef(false);
  const [dragOver, setDragOver] = useState(null);

  // ── Cast ────────────────────────────────────────────────────────────────
  const addCharacter = useCallback(() => {
    const n = cast.length;
    upsertCastMember({
      id: genCastId(),
      name: `${t('stories.character')} ${n}`,
      color: nextCastColor(cast),
      profileId: null,
    });
    setActiveTab('cast');
  }, [cast, upsertCastMember, t]);

  const deleteCharacter = useCallback(
    (id) => {
      if (id === 'narrator') return; // keep at least the narrator
      // Reassign any lines using this character back to the narrator.
      setTracks((prev) =>
        prev.map((tk) => (tk.character === id ? { ...tk, character: 'narrator' } : tk)),
      );
      removeCastMember(id);
    },
    [removeCastMember, setTracks],
  );

  // ── Auto-cast: detect speakers in pasted/imported text, build cast + lines ─
  const slug = (name) =>
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'char';

  const autoCast = useCallback(() => {
    const parsed = parseScript(splitText);
    if (!parsed.length) {
      toast.error(t('stories.autocastEmpty'));
      return;
    }
    const speakers = [...new Set(parsed.map((p) => p.speaker))];
    const newCast = cast.map((c) => ({ ...c }));
    const idFor = {};
    let voiceIdx = 0;
    const assignVoice = () => (profiles.length ? profiles[voiceIdx++ % profiles.length].id : null);
    for (const sp of speakers) {
      const id = sp.toLowerCase() === 'narrator' ? 'narrator' : slug(sp);
      idFor[sp] = id;
      const existing = newCast.find((c) => c.id === id);
      if (!existing) {
        newCast.push({ id, name: sp, color: nextCastColor(newCast), profileId: assignVoice() });
      } else if (!existing.profileId && profiles.length) {
        existing.profileId = assignVoice();
      }
    }
    setCast(newCast);
    const newTracks = parsed.map((p) => makeTrack(idFor[p.speaker], p.text));
    setTracks((prev) => [...prev, ...newTracks]);
    setSplitText('');
    setSplitOpen(false);
    setActiveTab('cast');
    toast.success(t('stories.autocastDone', { lines: newTracks.length, voices: speakers.length }));
  }, [splitText, cast, profiles, setCast, setTracks, t]);

  const onImportFile = useCallback(
    async (e) => {
      const file = e.target.files && e.target.files[0];
      e.target.value = '';
      if (!file) return;
      try {
        const text = importToText(file.name, await readTextFile(file));
        setSplitText(text);
        setSplitOpen(true);
      } catch (err) {
        console.warn('Story import failed:', err);
        toast.error(t('stories.importFailed'));
      }
    },
    [t],
  );

  // ── Named projects ──────────────────────────────────────────────────────
  const currentProject = storyProjects.find((p) => p.id === currentProjectId) || null;
  useEffect(() => {
    setProjectName(currentProject ? currentProject.name : '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentProjectId]);

  const saveCurrent = useCallback(() => {
    saveProject(projectName.trim() || t('stories.untitled'));
    toast.success(t('stories.projectSaved'));
  }, [projectName, saveProject, t]);
  const newStory = useCallback(() => {
    newProject();
    setProjectName('');
    setActiveTab('script');
  }, [newProject]);
  const createSampleStory = useCallback(() => {
    newProject();
    sampleVoicesBackfilledRef.current = profiles.length > 0;
    setCast(
      SAMPLE_STORY_CAST.map((member, index) => ({
        ...member,
        profileId: profiles.length ? profiles[index % profiles.length].id : null,
      })),
    );
    setStoryTracks(SAMPLE_STORY_LINES.map((line) => makeTrack(line.character, line.text)));
    setProjectName(SAMPLE_STORY_NAME);
    saveProject(SAMPLE_STORY_NAME);
    setSplitOpen(false);
    setActiveTab('script');
  }, [newProject, profiles, saveProject, setCast, setStoryTracks]);
  const loadSampleStory = useCallback(async () => {
    if (
      tracks.some((track) => track.text.trim()) &&
      !(await askConfirm(t('audiobook.load_sample_confirm'), t('audiobook.load_sample')))
    ) {
      return;
    }

    createSampleStory();
    toast.success(t('stories.projectSaved'));
  }, [createSampleStory, t, tracks]);

  useEffect(() => {
    if (sampleBootstrapRef.current) return;
    if (currentProjectId || storyProjects.length || tracks.some((track) => track.text.trim()))
      return;
    sampleBootstrapRef.current = true;
    try {
      if (localStorage.getItem(DEFAULT_SAMPLE_KEY)) return;
      localStorage.setItem(DEFAULT_SAMPLE_KEY, '1');
    } catch {
      // Storage can be unavailable in privacy modes; the pristine-state guard
      // still prevents duplicate projects during this mounted session.
    }
    createSampleStory();
  }, [createSampleStory, currentProjectId, storyProjects.length, tracks]);
  useEffect(() => {
    if (
      sampleVoicesBackfilledRef.current ||
      currentProject?.name !== SAMPLE_STORY_NAME ||
      !profiles.length ||
      cast.some((member) => member.profileId)
    ) {
      return;
    }
    sampleVoicesBackfilledRef.current = true;
    setCast(
      cast.map((member, index) => ({
        ...member,
        profileId: profiles[index % profiles.length].id,
      })),
    );
  }, [cast, currentProject?.name, profiles, setCast]);
  const openProject = useCallback(
    (id) => {
      loadProject(id);
      setActiveTab('script');
    },
    [loadProject],
  );
  const confirmDeleteProject = useCallback(
    async (id) => {
      if (await askConfirm(t('stories.deleteProjectConfirm'), t('stories.deleteProject'))) {
        deleteProject(id);
      }
    },
    [deleteProject, t],
  );

  const addChapter = useCallback(() => {
    const n = tracks.filter((tk) => isChapterText(tk.text)).length + 1;
    setTracks((prev) => [...prev, makeTrack('narrator', `# ${t('stories.chapterN', { n })}`)]);
  }, [tracks, setTracks, t]);

  // Clear every line and chapter at once (an import can add hundreds; the
  // per-line trash icon was the only way to undo one). Cast is kept.
  const clearScript = useCallback(async () => {
    const count = tracks.length + (splitText.trim() ? 1 : 0);
    if (!count) return;
    const ok = await askConfirm(t('stories.clearConfirm', { count }), t('stories.clearScript'));
    if (!ok) return;
    // Invalidate previews still generating for lines that are about to go,
    // and stop whatever is playing (#2203 review).
    previewGenRef.current += 1;
    stopActivePlayback();
    // An empty script is exactly what the first-run bootstrap below treats as
    // "pristine", so mark the sample as shown or it would reseed the demo.
    sampleBootstrapRef.current = true;
    try {
      localStorage.setItem(DEFAULT_SAMPLE_KEY, '1');
    } catch {
      // Storage unavailable: the ref alone covers this mounted session.
    }
    setTracks((prev) => {
      prev.forEach(releasePreview);
      return [];
    });
    setSplitText('');
    setSplitOpen(false);
    toast.success(t('stories.cleared'));
  }, [tracks.length, splitText, setTracks, t]);

  // ── Paste & auto-split ───────────────────────────────────────────────────
  const applySplit = useCallback(() => {
    const chunks = splitStoryText(splitText, splitMode, splitMax);
    if (!chunks.length) return;
    setTracks((prev) => [...prev, ...chunks.map((tx) => makeTrack('narrator', tx))]);
    setSplitText('');
    setSplitOpen(false);
  }, [splitText, splitMode, splitMax, setTracks]);

  const setVoiceForSelection = useCallback(
    (trackId, voiceId) => {
      const el = trackTextRefs.current.get(trackId);
      const start = el?.selectionStart;
      const end = el?.selectionEnd;
      setTracks((prev) =>
        prev.map((tk) => {
          if (tk.id !== trackId) return tk;
          const safeStart = start != null ? start : tk.text.length;
          const safeEnd = end != null ? end : safeStart;
          return { ...tk, text: applyInlineVoice(tk.text, safeStart, safeEnd, voiceId) };
        }),
      );
    },
    [setTracks],
  );

  const insertTokenInto = useCallback(
    (trackId, token) => {
      const el = trackTextRefs.current.get(trackId);
      const caret = el ? el.selectionStart : null;
      setTracks((prev) =>
        prev.map((tk) =>
          tk.id === trackId ? { ...tk, text: insertToken(tk.text, caret, token) } : tk,
        ),
      );
    },
    [setTracks],
  );
  const insertPauseInto = useCallback(
    (trackId) => insertTokenInto(trackId, '[pause 0.5s]'),
    [insertTokenInto],
  );

  const addTrack = useCallback(() => setTracks((prev) => [...prev, makeTrack()]), [setTracks]);
  const removeTrack = useCallback(
    (id) => {
      if (playingPreviewRef.current?.id === id) {
        stopActivePlayback();
        playingPreviewRef.current = null;
      }
      setTracks((prev) =>
        prev.filter((tk) => {
          if (tk.id === id) releasePreview(tk);
          return tk.id !== id;
        }),
      );
    },
    [setTracks],
  );
  const updateTrack = useCallback(
    (id, field, value) => {
      setTracks((prev) => prev.map((tk) => (tk.id === id ? { ...tk, [field]: value } : tk)));
    },
    [setTracks],
  );

  // ── Synthesis (preview + export share one fetch) ─────────────────────────
  const fetchChunkBlob = useCallback(async (text, profileId, speed = 1.0) => {
    const fd = new FormData();
    fd.append('text', text);
    fd.append('speed', String(speed || 1.0));
    if (profileId) fd.append('profile_id', profileId);
    const res = await generateSpeech(fd); // apiFetch: same-origin + PIN-aware
    return res.blob();
  }, []);

  const previewTrack = useCallback(
    async (track) => {
      const raw = (track.text || '').trim();
      if (!raw) return;
      const gen = previewGenRef.current;
      const stale = () =>
        previewGenRef.current !== gen ||
        !useAppStore.getState().storyTracks.some((tk) => tk.id === track.id);
      const playback = { id: track.id };
      const releasePlayback = () => {
        if (playingPreviewRef.current === playback) playingPreviewRef.current = null;
      };
      const pid = effectiveProfile(track, cast);
      const spd = effectiveSpeed(track, globalSpeed);
      setTracks((prev) =>
        prev.map((tk) => (tk.id === track.id ? { ...tk, generating: true } : tk)),
      );

      if (!hasStoryMarkers(raw)) {
        try {
          const blob = await fetchChunkBlob(raw, pid, spd);
          if (stale()) return;
          const url = URL.createObjectURL(blob);
          setTracks((prev) =>
            prev.map((tk) => {
              if (tk.id !== track.id) return tk;
              releasePreview(tk);
              return { ...tk, audioUrl: url, generating: false };
            }),
          );
          // Shared playback path (labelled with the line text): registers with
          // the single-playback manager + global mini-player, and — unlike the
          // old bare `new Audio(blobUrl)` — actually plays under Tauri's
          // WebKit, where blob: URLs are dead in media elements.
          playingPreviewRef.current = playback;
          playBlobAudio(blob, { label: raw, onDone: releasePlayback }).catch(releasePlayback);
        } catch (err) {
          console.warn('Stories preview failed:', err);
          if (err?.code === 'tts_generation_busy') {
            toast.error(t('tts_errors.generation_in_progress'));
          }
          setTracks((prev) =>
            prev.map((tk) => (tk.id === track.id ? { ...tk, generating: false } : tk)),
          );
        }
        return;
      }

      const parsed = parseStoryText(raw, pid);
      try {
        const chunkBlobs = [];
        for (const seg of parsed) {
          chunkBlobs.push(
            seg.type === 'chunk' ? await fetchChunkBlob(seg.text, seg.profileId, spd) : null,
          );
        }
        if (stale()) return;
        let cursor = 0;
        const finish = () => {
          setTracks((prev) =>
            prev.map((tk) => {
              if (tk.id !== track.id) return tk;
              releasePreview(tk);
              return { ...tk, generating: false, audioUrl: null };
            }),
          );
        };
        const step = () => {
          if (stale()) return;
          while (cursor < parsed.length) {
            const seg = parsed[cursor];
            const blob = chunkBlobs[cursor];
            cursor++;
            if (seg.type === 'pause') {
              setTimeout(step, seg.seconds * 1000);
              return;
            }
            if (seg.type === 'chunk' && blob) {
              // Chained through the shared playback path: each chunk claims
              // the global manager (mini-player shows the line), a natural
              // end (or a broken chunk) advances the chain, and stopping from
              // the player/another claim cancels the rest of the chain.
              playingPreviewRef.current = playback;
              playBlobAudio(blob, {
                label: raw,
                onDone: (reason) => {
                  releasePlayback();
                  return reason === 'stopped' ? finish() : step();
                },
              }).catch(() => {
                releasePlayback();
                step();
              });
              return;
            }
          }
          finish();
        };
        step();
      } catch (err) {
        console.warn('Stories chained preview failed:', err);
        if (err?.code === 'tts_generation_busy') {
          toast.error(t('tts_errors.generation_in_progress'));
        }
        setTracks((prev) =>
          prev.map((tk) => (tk.id === track.id ? { ...tk, generating: false } : tk)),
        );
      }
    },
    [fetchChunkBlob, cast, globalSpeed, setTracks, t],
  );

  // Deliver a stitched WAV in the chosen format. MP3 routes through the backend
  // ffmpeg endpoint; if that fails (e.g. no ffmpeg), fall back to the raw WAV.
  const deliver = useCallback(
    async (wavBlob, baseName) => {
      if (exportFormat === 'mp3') {
        try {
          download(await encodeAudio(wavBlob, 'mp3'), `${baseName}.mp3`);
          return;
        } catch (err) {
          console.warn('MP3 encode failed; falling back to WAV:', err);
          toast(t('stories.mp3Fallback'), { icon: '⚠️' });
        }
      }
      download(wavBlob, `${baseName}.wav`);
    },
    [exportFormat, t],
  );

  // Full export now runs on the shared server-side renderer (the Stories +
  // Audiobook convergence): cast + lines compile to a chapter/span plan and
  // stream through /longform/render — gaining chapter markers, resume, and
  // (via the audiobook controls) loudness/metadata. Single-line preview stays
  // client-side for latency. Stems remain a client export below.
  const generateAll = useCallback(async () => {
    const usable = tracks.filter((tk) => (tk.text || '').trim());
    if (!usable.length || exporting) return;
    const chapters = storyToSpans(usable, cast, globalSpeed);
    if (!chapters.length) {
      toast.error(t('stories.exportFailed'));
      return;
    }
    setExporting(true);
    setExportPct(0);
    try {
      const res = await longformRender({
        chapters,
        format: exportFormat === 'mp3' ? 'mp3' : 'm4b',
      });
      let total = 0;
      let output = '';
      let streamErr = null;
      await consumeLongformStream(res, (evt) => {
        if (evt.type === 'started') total = evt.chapters;
        else if (evt.type === 'chapter' || evt.type === 'chapter_error') {
          setExportPct(total ? Math.round(((evt.index + 1) / total) * 100) : 0);
        } else if (evt.type === 'done') output = evt.output;
        else if (evt.type === 'error') streamErr = evt.error || 'render failed';
      });
      if (streamErr) throw new Error(streamErr);
      if (!output) throw new Error('no output produced');
      // Route through the shared save util (#1218): the story mix is a file in
      // OUTPUTS_DIR served at /audio/<output>, so a raw `<a href download>`
      // navigates the Tauri webview to the m4b and hijacks the app. Pass
      // `sourceFilename` so the Tauri copy uses the /export server-side copy.
      const mixName = output.split('/').pop();
      // downloadMedia owns all user-facing toasts (loading → saved/error) and
      // fires onValueMoment only on a real save — so no success toast here (it
      // would fire even when the native save dialog is cancelled) and the
      // donation moment goes through the callback instead of unconditionally.
      await downloadMedia(audioUrl(output), mixName, {
        sourceFilename: mixName,
        onValueMoment: () => recordValueMoment('audiobook'),
      });
    } catch (err) {
      console.warn('Story render failed:', err);
      toast.error(t('stories.exportFailed'));
    } finally {
      setExporting(false);
    }
  }, [tracks, cast, exporting, exportFormat, globalSpeed, t]);

  const exportStemsAll = useCallback(async () => {
    const usable = tracks.filter((tk) => (tk.text || '').trim());
    if (!usable.length || exporting) return;
    setExporting(true);
    setExportPct(0);
    try {
      const stems = await exportStems(
        usable,
        (tk) => ({ profileId: effectiveProfile(tk, cast), speed: effectiveSpeed(tk, globalSpeed) }),
        fetchChunkBlob,
        (d, total) => setExportPct(total ? Math.round((d / total) * 100) : 0),
      );
      for (const s of stems) {
        const name = ((castMember(cast, s.character) || {}).name || s.character).replace(
          /[^\w-]+/g,
          '_',
        );
        await deliver(s.blob, `story-${name}`);
      }
      toast.success(t('stories.stemsDone', { count: stems.length }));
    } catch (err) {
      console.warn('Stems export failed:', err);
      toast.error(
        err?.code === 'tts_generation_busy'
          ? t('tts_errors.generation_in_progress')
          : t('stories.exportFailed'),
      );
    } finally {
      setExporting(false);
    }
  }, [tracks, cast, fetchChunkBlob, exporting, deliver, globalSpeed, t]);

  // ── Stats ─────────────────────────────────────────────────────────────────
  const { totalChars, usedCharacters, estMinutes } = useMemo(() => {
    let chars = 0;
    const characters = new Set();
    for (const track of tracks) {
      chars += track.text.length;
      characters.add(track.character);
    }
    return {
      totalChars: chars,
      usedCharacters: characters.size,
      estMinutes: chars ? Math.ceil(chars / 800) : 0,
    };
  }, [tracks]);
  const profileNames = useMemo(
    () => new Map(profiles.map((profile) => [profile.id, profile.name])),
    [profiles],
  );
  const profileName = useCallback((id) => profileNames.get(id), [profileNames]);

  return (
    <div
      className="stories-editor stories-tabbed flex flex-col h-full w-full min-h-0 font-sans"
      role="region"
      aria-label={t('stories.title')}
    >
      {/* Production header */}
      <header className="stories-header flex items-center justify-between gap-[12px]">
        <div className="stories-identity flex items-center gap-[9px] min-w-0">
          <span className="stories-title__icon" aria-hidden="true">
            <BookOpen size={15} />
          </span>
          <div className="min-w-0">
            <span className="stories-kicker">{t('stories.title')}</span>
            <h1 className="stories-title font-serif text-fg m-0 truncate text-balance">
              {currentProject ? currentProject.name : t('stories.untitled')}
            </h1>
          </div>
        </div>
        <div className="stories-header-output flex items-center gap-[5px]">
          <span className="stories-header-stat">
            {t('stories.lines', { count: tracks.length })} ·{' '}
            {t('stories.minutes', { count: estMinutes })}
          </span>
          <Button
            variant="primary"
            size="sm"
            onClick={generateAll}
            disabled={tracks.length === 0 || exporting}
          >
            <Download size={13} aria-hidden="true" />{' '}
            {exporting ? `${exportPct}%` : t('stories.generateAll')}
          </Button>
        </div>
      </header>

      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        activationMode="manual"
        className="stories-tabbed flex min-h-0 flex-1 flex-col gap-0"
      >
        <div className="stories-tabbar flex shrink-0 px-[12px] py-[8px]">
          <TabsList
            aria-label={t('stories.title')}
            className="grid h-auto w-auto min-w-0 grid-cols-4 gap-[3px] rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-bg)] p-[3px]"
          >
            {[
              { id: 'script', label: t('audiobook.script'), icon: FileText },
              { id: 'cast', label: t('stories.cast'), icon: Users },
              { id: 'export', label: t('projects.export'), icon: Download },
              { id: 'projects', label: t('stories.projects'), icon: Folder },
            ].map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="stories-tab min-h-11 h-auto min-w-0 cursor-pointer whitespace-normal rounded-[var(--chrome-radius-pill)] border border-transparent bg-transparent px-3 py-2 text-sm font-medium text-[color:var(--chrome-fg-muted)] transition-colors data-[state=active]:border-[var(--chrome-accent-border)] data-[state=active]:bg-[var(--chrome-accent-bg)] data-[state=active]:font-semibold data-[state=active]:text-[color:var(--chrome-accent)] data-[state=active]:shadow-none hover:data-[state=inactive]:bg-[var(--chrome-hover-bg)]"
              >
                <tab.icon size={14} aria-hidden="true" />
                <span>{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>
          {activeTab === 'script' && (
            <div className="stories-script-tools flex items-center gap-[4px]">
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.srt,.vtt,text/plain"
                onChange={onImportFile}
                aria-label={t('stories.import')}
                name="story-import-file"
                hidden
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => fileInputRef.current && fileInputRef.current.click()}
              >
                <Upload size={13} aria-hidden="true" /> {t('stories.import')}
              </Button>
              <Button size="sm" variant="ghost" onClick={toggleSplit}>
                <Scissors size={13} aria-hidden="true" /> {t('stories.pasteSplit')}
              </Button>
              <Button size="sm" variant="ghost" onClick={addTrack}>
                <Plus size={13} aria-hidden="true" />
                {t('stories.addLine')}
              </Button>
              <Button size="sm" variant="ghost" onClick={addChapter}>
                <Bookmark size={13} aria-hidden="true" />
                {t('stories.addChapter')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={clearScript}
                disabled={!tracks.length && !splitText.trim()}
                title={t('stories.clearScriptHint')}
              >
                <Trash2 size={13} aria-hidden="true" />
                {t('stories.clearScript')}
              </Button>
            </div>
          )}
        </div>

        <TabsContent
          value="projects"
          className="stories-tab-panel stories-projects-panel min-h-0 flex-1 overflow-y-auto"
        >
          <main className="stories-panel-content mx-auto flex w-full max-w-[1120px] flex-col gap-[12px] p-[12px]">
            <section className="stories-rail-section">
              <div className="stories-rail-heading">
                <div className="stories-section-toggle">
                  <Folder size={13} aria-hidden="true" />
                  <span>{t('stories.projects')}</span>
                  <span className="stories-section-count">{storyProjects.length}</span>
                </div>
                <div className="flex items-center gap-[4px]">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={loadSampleStory}
                    title={t('audiobook.load_sample_hint')}
                  >
                    <Sparkles size={12} aria-hidden="true" />
                    {t('audiobook.load_sample')}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={newStory}>
                    <Plus size={12} aria-hidden="true" />
                    {t('stories.newStory')}
                  </Button>
                </div>
              </div>
              <div className="stories-project-editor">
                <input
                  className={`${NAME_INPUT} min-w-0 flex-1`}
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder={t('stories.untitled')}
                  aria-label={t('stories.projectName')}
                  name="story-project-name"
                  autoComplete="off"
                />
                <Button size="sm" onClick={saveCurrent}>
                  {t('stories.save')}
                </Button>
              </div>
              <div className="stories-project-list">
                {storyProjects.length === 0 ? (
                  <p className="m-0 p-[8px] text-fg-muted [font-size:var(--text-sm)]">
                    {t('stories.noProjects')}
                  </p>
                ) : (
                  storyProjects.map((project) => (
                    <div
                      key={project.id}
                      className={`stories-project-row ${project.id === currentProjectId ? 'stories-project-row--active' : ''}`}
                    >
                      <button type="button" onClick={() => openProject(project.id)}>
                        <span className="truncate">{project.name}</span>
                      </button>
                      <button
                        type="button"
                        className={DEL_BTN}
                        onClick={() => confirmDeleteProject(project.id)}
                        aria-label={t('stories.deleteProject')}
                      >
                        <X size={12} aria-hidden="true" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </section>
          </main>
        </TabsContent>

        <TabsContent
          value="cast"
          className="stories-tab-panel stories-cast-panel min-h-0 flex-1 overflow-y-auto"
        >
          <main className="stories-panel-content mx-auto flex w-full max-w-[1120px] flex-col gap-[12px] p-[12px]">
            <section className="stories-rail-section stories-cast-rail">
              <div className="stories-rail-heading">
                <div className="stories-section-toggle">
                  <Users size={13} aria-hidden="true" />
                  <span>{t('stories.cast')}</span>
                  <span className="stories-section-count">{cast.length}</span>
                </div>
                <Button size="sm" variant="ghost" onClick={addCharacter}>
                  <Plus size={12} aria-hidden="true" />
                  {t('stories.addCharacter')}
                </Button>
              </div>
              <div className="stories-cast-grid">
                {cast.map((member) => (
                  <div key={member.id} className="stories-cast-member min-w-0">
                    <span
                      className="stories-cast-swatch"
                      style={{ background: member.color }}
                      aria-hidden="true"
                    />
                    <input
                      className={NAME_INPUT}
                      value={member.name}
                      onChange={(e) => upsertCastMember({ ...member, name: e.target.value })}
                      aria-label={t('stories.characterName')}
                      name={`story-character-${member.id}`}
                      autoComplete="off"
                    />
                    <span className="min-w-0">
                      <VoiceSelector
                        value={member.profileId || ''}
                        onChange={(value) => setCharacterVoice(member.id, value || null)}
                        profiles={profiles}
                        size="sm"
                        menuPortal
                        defaultLabel={t('stories.defaultVoice')}
                      />
                    </span>
                    <button
                      type="button"
                      className={DEL_BTN}
                      onClick={() => deleteCharacter(member.id)}
                      disabled={member.id === 'narrator'}
                      title={
                        member.id === 'narrator'
                          ? t('stories.narratorLocked')
                          : t('stories.removeCharacter')
                      }
                      aria-label={t('stories.removeCharacter')}
                    >
                      <X size={12} aria-hidden="true" />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          </main>
        </TabsContent>

        <TabsContent
          value="export"
          className="stories-tab-panel stories-export-panel min-h-0 flex-1 overflow-y-auto"
        >
          <main className="stories-panel-content mx-auto flex w-full max-w-[1120px] flex-col gap-[12px] p-[12px]">
            <section className="stories-rail-section stories-output-rail">
              <div className="stories-rail-label">
                <Timer size={12} aria-hidden="true" />
                <span>{t('stories.global_speed')}</span>
                <span className="stories-speed-value">{globalSpeed.toFixed(1)}×</span>
              </div>
              <input
                type="range"
                min="0.5"
                max="2"
                step="0.05"
                value={globalSpeed}
                onChange={(e) => setGlobalSpeed(parseFloat(e.target.value))}
                aria-label={t('stories.global_speed')}
                name="story-global-speed"
                className="w-full"
              />
              <label className="stories-rail-label">
                <Download size={12} aria-hidden="true" />
                <span>{t('stories.format')}</span>
                <select
                  className="input-base ml-auto w-auto [font-size:var(--text-xs)] px-[6px] py-[3px]"
                  value={exportFormat}
                  onChange={(e) => setExportFormat(e.target.value)}
                  name="story-export-format"
                >
                  <option value="m4b">M4B</option>
                  <option value="mp3">MP3</option>
                </select>
              </label>
              <div className="flex flex-wrap items-center gap-[6px]">
                <Button
                  variant="primary"
                  onClick={generateAll}
                  disabled={tracks.length === 0 || exporting}
                >
                  <Download size={13} aria-hidden="true" />
                  {exporting ? `${exportPct}%` : t('stories.generateAll')}
                </Button>
                <Button
                  variant="ghost"
                  onClick={exportStemsAll}
                  disabled={tracks.length === 0 || exporting}
                >
                  <Layers size={13} aria-hidden="true" /> {t('stories.stems')}
                </Button>
              </div>
            </section>
          </main>
        </TabsContent>

        <TabsContent value="script" className="stories-tab-panel min-h-0 flex-1 overflow-hidden">
          <main className="stories-manuscript min-w-0 min-h-0 h-full">
            {/* Paste & split */}
            {splitOpen && (
              <div
                className="stories-panel stories-split-panel flex flex-col gap-[10px]"
                role="region"
                aria-label={t('stories.pasteSplit')}
              >
                <textarea
                  className="w-full min-h-[96px] px-[10px] py-[8px] bg-bg-elev-2 border border-border rounded-sm text-fg [font-family:var(--font-sans)] [font-size:var(--text-sm)] resize-y"
                  placeholder={t('stories.splitPlaceholder')}
                  value={splitText}
                  onChange={(e) => setSplitText(e.target.value)}
                  rows={6}
                  aria-label={t('stories.splitPlaceholder')}
                  name="story-import-text"
                  autoComplete="off"
                />
                <div className="flex items-center gap-[12px] flex-wrap">
                  <label className="flex items-center gap-[6px] [font-size:var(--text-xs)] text-fg-muted">
                    {t('stories.splitMode')}
                    <SearchableSelect
                      value={splitMode}
                      onChange={setSplitMode}
                      options={SPLIT_MODES.map((mode) => ({
                        value: mode,
                        label: t(`stories.split_${mode}`),
                      }))}
                      ariaLabel={t('stories.splitMode')}
                      menuPortal
                      size="sm"
                    />
                  </label>
                  {splitMode === 'sentences' && (
                    <label className="flex items-center gap-[6px] [font-size:var(--text-xs)] text-fg-muted">
                      {t('stories.maxChars')}
                      <input
                        type="number"
                        min={60}
                        max={1000}
                        step={10}
                        value={splitMax}
                        onChange={(e) =>
                          setSplitMax(parseInt(e.target.value, 10) || DEFAULT_SPLIT_MAX.sentences)
                        }
                        name="story-segment-length"
                        inputMode="numeric"
                        className="w-[64px] px-[6px] py-[4px] bg-bg-elev-2 border border-border rounded-sm text-fg [font-family:var(--font-mono)] [font-size:var(--text-xs)]"
                      />
                    </label>
                  )}
                  <span className="flex-1 [font-size:var(--text-xs)] text-fg-subtle">
                    {!splitText
                      ? t('stories.pasteAbove')
                      : splitMode === 'sentences'
                        ? t('stories.segmentsHint', {
                            count: splitStoryText(splitText, splitMode, splitMax).length,
                            max: splitMax,
                          })
                        : t('stories.lines', {
                            count: splitStoryText(splitText, splitMode).length,
                          })}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setSplitText('');
                      setSplitOpen(false);
                    }}
                  >
                    {t('stories.cancel')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={applySplit}
                    disabled={!splitText.trim()}
                  >
                    <Scissors size={13} /> {t('stories.splitIntoTracks')}
                  </Button>
                  <Button
                    size="sm"
                    onClick={autoCast}
                    disabled={!splitText.trim()}
                    title={t('stories.autocastHint')}
                  >
                    <Sparkles size={13} /> {t('stories.autocast')}
                  </Button>
                </div>
              </div>
            )}

            {/* Tracks */}
            {tracks.length === 0 ? (
              <div className="stories-empty flex-1 flex flex-col items-center justify-center gap-[10px] text-fg-muted text-center">
                <span className="stories-empty__icon" aria-hidden="true">
                  <BookOpen size={26} />
                </span>
                <p className="[font-size:var(--text-sm)] max-w-[360px] leading-[1.55] text-pretty">
                  {t('stories.emptyText')}
                </p>
                <div className="flex items-center gap-[6px]">
                  <Button
                    size="sm"
                    onClick={loadSampleStory}
                    title={t('audiobook.load_sample_hint')}
                  >
                    <Sparkles size={13} aria-hidden="true" /> {t('audiobook.load_sample')}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={addTrack}>
                    <Plus size={13} aria-hidden="true" /> {t('stories.addFirst')}
                  </Button>
                </div>
              </div>
            ) : (
              <div
                className="stories-track-list flex-1 flex flex-col gap-[10px] overflow-y-auto"
                role="list"
              >
                {tracks.map((track, index) => {
                  const dragHandleProps = {
                    draggable: true,
                    onDragStart: (e) => {
                      dragId.current = track.id;
                      e.dataTransfer.effectAllowed = 'move';
                    },
                  };
                  const dropProps = {
                    onDragOver: (e) => {
                      e.preventDefault();
                      if (dragOver !== track.id) setDragOver(track.id);
                    },
                    onDragLeave: () => setDragOver((d) => (d === track.id ? null : d)),
                    onDrop: (e) => {
                      e.preventDefault();
                      if (dragId.current != null && dragId.current !== track.id) {
                        setTracks((prev) => reorder(prev, dragId.current, track.id));
                      }
                      dragId.current = null;
                      setDragOver(null);
                    },
                  };

                  // Chapters render as a section bar — no voice / tune / preview.
                  if (isChapterText(track.text)) {
                    const title = track.text.replace(/^#{1,6}\s*/, '');
                    return (
                      <div
                        key={track.id}
                        role="listitem"
                        className={[
                          'stories-chapter group flex items-center gap-[10px] mt-[18px] mb-[2px]',
                          dragOver === track.id
                            ? '[outline:1px_dashed_var(--color-accent)] outline-offset-[2px]'
                            : '',
                        ]
                          .filter(Boolean)
                          .join(' ')}
                        {...dropProps}
                      >
                        <div
                          className="stories-line-number flex items-center justify-center text-fg-subtle cursor-grab"
                          aria-hidden="true"
                          {...dragHandleProps}
                        >
                          {String(index + 1).padStart(2, '0')}
                        </div>
                        <Bookmark size={15} className="flex-none text-accent" aria-hidden="true" />
                        <input
                          className="stories-chapter__input flex-1 min-w-0 bg-transparent border-none [font-family:inherit] text-fg px-0 py-[4px] placeholder:text-fg-subtle placeholder:font-semibold focus-visible:outline-none"
                          value={title}
                          onChange={(e) => updateTrack(track.id, 'text', `# ${e.target.value}`)}
                          placeholder={t('stories.addChapter')}
                          aria-label={t('stories.addChapter')}
                          name={`story-chapter-${track.id}`}
                          autoComplete="off"
                        />
                        <button
                          type="button"
                          className="stories-icon-button flex-none flex p-[6px] bg-transparent border-none text-fg-subtle cursor-pointer opacity-0 group-hover:opacity-70 group-focus-within:opacity-70 hover:!opacity-100 hover:text-danger focus-visible:!opacity-100"
                          onClick={(e) => {
                            e.stopPropagation();
                            removeTrack(track.id);
                          }}
                          title={t('stories.removeLine')}
                          aria-label={t('stories.removeLine')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    );
                  }

                  const member = castMember(cast, track.character);
                  const inheritedId = member && member.profileId;
                  const inheritedName = inheritedId ? profileName(inheritedId) : null;
                  return (
                    <div
                      key={track.id}
                      role="listitem"
                      className={[
                        'stories-line group grid items-center cursor-grab',
                        activeTrack === track.id ? 'stories-line--active' : '',
                        track.character === 'narrator'
                          ? '[border-left:3px_solid_var(--color-accent)]'
                          : '',
                        dragOver === track.id
                          ? '[box-shadow:inset_0_2px_0_0_var(--color-accent)]'
                          : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      onFocusCapture={() => setActiveTrack(track.id)}
                      onBlurCapture={(e) => {
                        if (!e.currentTarget.contains(e.relatedTarget)) setActiveTrack(null);
                      }}
                      {...dropProps}
                    >
                      <div
                        className="stories-line__drag flex flex-col items-center justify-center gap-[2px] text-fg-subtle cursor-grab active:cursor-grabbing"
                        aria-hidden="true"
                        {...dragHandleProps}
                      >
                        <span className="stories-line-number">
                          {String(index + 1).padStart(2, '0')}
                        </span>
                        <GripVertical size={14} />
                      </div>

                      <textarea
                        className="stories-line__text w-full bg-transparent border border-transparent text-fg [font-family:var(--font-sans)] resize-y leading-[1.65] focus-visible:outline-none"
                        ref={(el) => {
                          if (el) trackTextRefs.current.set(track.id, el);
                          else trackTextRefs.current.delete(track.id);
                        }}
                        value={track.text}
                        onChange={(e) => updateTrack(track.id, 'text', e.target.value)}
                        placeholder={t('stories.linePlaceholder')}
                        rows={2}
                        aria-label={`${member ? member.name : ''} ${t('stories.text')}`}
                        name={`story-line-${track.id}`}
                        autoComplete="off"
                      />

                      <div className="stories-line__character flex items-center gap-[7px] min-w-0">
                        <span
                          className="w-[10px] h-[10px] rounded-full shrink-0"
                          style={{ background: member ? member.color : '#a89984' }}
                        />
                        <select
                          className={`${SELECT_CHROME} flex-1`}
                          value={track.character}
                          onChange={(e) => updateTrack(track.id, 'character', e.target.value)}
                          aria-label={t('stories.character')}
                          name={`story-character-for-line-${track.id}`}
                        >
                          {cast.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name}
                            </option>
                          ))}
                        </select>
                      </div>

                      {/* Per-line voice override → shared gallery-enabled picker
                    (#1220). '' inherits the character's cast voice (label shows
                    "↳ <name>"); any pick stores a real profile id (gallery picks
                    materialize first). `|| null` keeps the store's null-default
                    shape so existing projects load unchanged. */}
                      <span className="stories-line__voice min-w-0">
                        <VoiceSelector
                          value={track.profileId || ''}
                          onChange={(v) => updateTrack(track.id, 'profileId', v || null)}
                          profiles={profiles}
                          size="sm"
                          menuPortal
                          defaultLabel={
                            inheritedName ? `↳ ${inheritedName}` : t('stories.defaultVoice')
                          }
                        />
                      </span>

                      <div
                        className={`stories-line__actions flex gap-[4px] [transition:opacity_0.12s_ease] ${
                          activeTrack === track.id
                            ? 'opacity-100'
                            : 'opacity-45 group-hover:opacity-100 group-focus-within:opacity-100'
                        }`}
                      >
                        <Menu
                          placement="bottom-end"
                          items={[
                            ...(profiles.length === 0
                              ? [{ id: 'noprof', label: t('stories.noProfiles'), disabled: true }]
                              : profiles.map((p) => ({
                                  id: `voice-${p.id}`,
                                  label: p.name,
                                  onSelect: () => setVoiceForSelection(track.id, p.id),
                                }))),
                            'separator',
                            {
                              id: 'voice-default',
                              label: t('stories.resetInlineVoice'),
                              onSelect: () => setVoiceForSelection(track.id, 'default'),
                            },
                          ]}
                        >
                          <button
                            type="button"
                            className={`${TRACK_BTN} hover:text-fg`}
                            onClick={(e) => e.stopPropagation()}
                            title={t('stories.inlineVoiceHint')}
                            aria-label={t('stories.inlineVoice')}
                          >
                            <Users size={12} />
                          </button>
                        </Menu>
                        <button
                          type="button"
                          className={`${TRACK_BTN} hover:text-fg ${expandedLine === track.id ? 'text-accent bg-white/[0.06]' : ''}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedLine((id) => (id === track.id ? null : track.id));
                          }}
                          title={t('stories.tune')}
                          aria-label={t('stories.tune')}
                        >
                          <SlidersHorizontal size={12} />
                        </button>
                        <button
                          type="button"
                          className={`${TRACK_BTN} hover:text-fg`}
                          onClick={(e) => {
                            e.stopPropagation();
                            insertPauseInto(track.id);
                          }}
                          title={t('stories.insertPause')}
                          aria-label={t('stories.insertPause')}
                        >
                          <PauseIcon size={12} />
                        </button>
                        <button
                          type="button"
                          className={`${TRACK_BTN} hover:text-fg`}
                          onClick={(e) => {
                            e.stopPropagation();
                            previewTrack(track);
                          }}
                          disabled={track.generating || !track.text.trim()}
                          title={t('stories.preview')}
                          aria-label={t('stories.preview')}
                        >
                          {track.generating ? (
                            <Mic size={12} className="spinner" />
                          ) : (
                            <Play size={12} />
                          )}
                        </button>
                        <button
                          type="button"
                          className={`${TRACK_BTN} hover:text-danger`}
                          onClick={(e) => {
                            e.stopPropagation();
                            removeTrack(track.id);
                          }}
                          title={t('stories.removeLine')}
                          aria-label={t('stories.removeLine')}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>

                      {expandedLine === track.id && (
                        <div
                          className="stories-line__drawer basis-full flex flex-wrap items-center gap-[12px]"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <div className="flex flex-wrap gap-[4px]">
                            {STORY_TONES.map((tn) => (
                              <button
                                key={tn.tag}
                                type="button"
                                className="inline-flex items-center gap-[4px] bg-bg-elev-2 border border-border rounded-full text-fg [font-size:var(--text-xs)] px-[9px] py-[3px] cursor-pointer hover:text-accent"
                                onClick={() => insertTokenInto(track.id, tn.tag)}
                                title={tn.tag}
                              >
                                <tn.icon size={12} aria-hidden="true" />{' '}
                                {t(`stories.tones.${tn.key}`)}
                              </button>
                            ))}
                          </div>
                          <label className="inline-flex items-center gap-[8px] [font-size:var(--text-xs)] text-fg-subtle">
                            <span>{t('stories.speed')}</span>
                            <input
                              type="range"
                              min="0.5"
                              max="2"
                              step="0.05"
                              value={track.speed || 1}
                              onChange={(e) =>
                                updateTrack(track.id, 'speed', parseFloat(e.target.value))
                              }
                              aria-label={t('stories.speed')}
                              name={`story-speed-${track.id}`}
                              className={SPEED_RANGE}
                            />
                            <span className="[font-family:var(--font-mono)] text-fg min-w-[44px]">
                              {(track.speed || 1).toFixed(2)}×
                            </span>
                            {track.speed != null && (
                              <button
                                type="button"
                                className={RESET_BTN}
                                onClick={() => updateTrack(track.id, 'speed', null)}
                              >
                                {t('stories.reset')}
                              </button>
                            )}
                          </label>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Footer stats */}
            {tracks.length > 0 && (
              <footer className="stories-statusbar flex items-center justify-between">
                <div className="[font-size:var(--text-xs)] text-fg-subtle flex flex-wrap gap-[14px]">
                  <span className="flex items-center gap-[4px]">
                    <FileText size={12} aria-hidden="true" />{' '}
                    {t('stories.lines', { count: tracks.length })}
                  </span>
                  <span className="flex items-center gap-[4px]">
                    <Drama size={12} aria-hidden="true" />{' '}
                    {t('stories.characters', { count: usedCharacters })}
                  </span>
                  <span className="flex items-center gap-[4px]">
                    <Timer size={12} aria-hidden="true" />{' '}
                    {t('stories.minutes', { count: estMinutes })}
                  </span>
                  <span className="flex items-center gap-[4px]">
                    <ChartColumn size={12} aria-hidden="true" />{' '}
                    {t('stories.chars', { count: totalChars })}
                  </span>
                  {exporting && (
                    <span className="flex items-center gap-[4px] text-accent" aria-live="polite">
                      <Hourglass size={12} aria-hidden="true" /> {exportPct}%
                    </span>
                  )}
                </div>
              </footer>
            )}
          </main>
        </TabsContent>
      </Tabs>
    </div>
  );
}
