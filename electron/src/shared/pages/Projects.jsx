import React, { useMemo, useState } from 'react';
import { copyText } from '../utils/copyText';
import { useTranslation } from 'react-i18next';
import {
  Search,
  FolderOpen,
  Film,
  Fingerprint,
  Wand2,
  Music,
  Download,
  LayoutGrid,
  List as ListIcon,
  Clock,
  FileText,
  Mic,
  BookMarked,
  BookOpen,
} from 'lucide-react';
import { apiFetch } from '../api/client';
import { timeAgo, toMillis } from '../utils/relativeTime';
import { loadTranscriptions, TRANSCRIPTION_EVENT } from '../utils/transcriptionsStore';
import { audioUrl } from '../api/generate';
import { playBlobAudio } from '../utils/media';

/**
 * OmniDrive — browse everything (studio dubs, voice profiles, generation
 * history, exports) in one place.
 *
 * Shape:
 *   ┌─────────────────────────────────────────────┐
 *   │ header strip (search + view toggle)         │
 *   ├─ filter rail ─┬── content grid/list ────────┤
 *   │ All           │                              │
 *   │ Dubs      (3) │   [card] [card] [card]      │
 *   │ Profiles (12) │   [card] [card] ...         │
 *   │ History  (48) │                              │
 *   │ Exports   (7) │                              │
 *   └───────────────┴──────────────────────────────┘
 *
 * Props reuse what App.jsx already loads — no new fetchers are added so
 * this page stays in sync with the Sidebar and Launchpad automatically.
 */

function fmtDuration(sec) {
  if (!sec) return '';
  const n = Number(sec);
  if (!Number.isFinite(n)) return '';
  if (n < 60) return `${Math.floor(n)}s`;
  const m = Math.floor(n / 60);
  const s = Math.floor(n % 60);
  return `${m}m ${s}s`;
}

/**
 * Preview a finished render (audiobook/story) inside the app.
 *
 * Previously the card did `window.open(url, '_blank')`. Under Tauri's WebView2
 * on Windows that handed the file to a new webview/OS media surface, spawning a
 * separate black playback window with centered controls that the user couldn't
 * close without force-quitting the whole app (#532). Fetch the file and route
 * it through the shared single-playback manager instead — identical, in-app
 * behavior on macOS/Windows/Linux, and starting another preview stops this one.
 */
async function playRenderInApp(url, label) {
  try {
    const resp = await apiFetch(url, { cache: 'no-store' });
    await playBlobAudio(await resp.blob(), { label });
  } catch (e) {
    console.error('[Projects] render playback failed:', e);
  }
}

function Card({ kind, accent, title, subtitle, trailing, onClick, IconC, view }) {
  const list = view === 'list';
  return (
    <button
      type="button"
      className={`flex cursor-pointer rounded-[var(--chrome-radius-pill)] border border-transparent [border-left:3px_solid_var(--card-accent,var(--chrome-accent))] bg-[var(--chrome-bg)] text-left [font-family:inherit] text-[var(--chrome-fg)] transition-[border-color,background,transform] duration-[0.12s] hover:border-transparent hover:bg-[color-mix(in_srgb,var(--card-accent,var(--chrome-accent))_5%,var(--chrome-bg))] active:translate-y-[1px] ${
        list
          ? 'flex-row items-center gap-[14px] px-[12px] py-[6px]'
          : 'flex-col gap-[6px] px-[12px] py-[10px]'
      }`}
      onClick={onClick}
      style={{ '--card-accent': accent }}
    >
      <div
        className={`flex items-center justify-between gap-[8px] ${list ? 'w-[110px] shrink-0' : ''}`}
      >
        <span className="inline-flex items-center gap-[4px] [font-family:var(--chrome-font-mono)] text-[10px] font-semibold uppercase [letter-spacing:var(--chrome-label-track)] text-[var(--card-accent,var(--chrome-accent))]">
          {IconC && <IconC size={11} />}
          {kind}
        </span>
        <span className="text-[10.5px] text-[var(--chrome-fg-dim)]">{trailing}</span>
      </div>
      <div
        className={`overflow-hidden text-ellipsis whitespace-nowrap text-[0.92rem] font-semibold text-[var(--chrome-fg)] ${list ? 'flex-1' : ''}`}
        title={title}
      >
        {title}
      </div>
      {subtitle && (
        <div
          className={`overflow-hidden text-ellipsis whitespace-nowrap text-[0.74rem] text-[var(--chrome-fg-dim)] ${list ? 'w-[120px] shrink-0' : ''}`}
          title={subtitle}
        >
          {subtitle}
        </div>
      )}
    </button>
  );
}

export default function Projects({
  studioProjects = [],
  profiles = [],
  history = [],
  exportHistory = [],
  storyProjects = [],
  onOpenDub, // (projectId) => void — loads project + switches to dub mode
  onOpenProfile, // (voiceId)   => void
  onOpenStory, // (storyId)   => void — loads story + switches to stories mode
  onRevealExport, // (path)      => void
}) {
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [view, setView] = useState('grid'); // grid | list
  const { t } = useTranslation();

  const FILTERS = [
    { id: 'all', label: t('projects.all'), Icon: FolderOpen },
    { id: 'dubs', label: t('projects.dub_projects'), Icon: Film },
    { id: 'stories', label: t('projects.stories'), Icon: BookOpen },
    { id: 'profiles', label: t('projects.voice_profiles'), Icon: Fingerprint },
    { id: 'transcripts', label: t('projects.transcripts'), Icon: Mic },
    { id: 'audiobooks', label: t('projects.audiobooks'), Icon: BookMarked },
    { id: 'history', label: t('projects.history'), Icon: Music },
    { id: 'exports', label: t('projects.exports'), Icon: Download },
  ];

  // Finished Audiobook + Story renders (server-side longform library).
  const [longformJobs, setLongformJobs] = useState([]);
  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await apiFetch('/longform/jobs');
        const data = await res.json();
        if (alive) setLongformJobs(data.jobs || []);
      } catch {
        /* offline / no backend — leave empty */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Load transcriptions from localStorage (same source as TranscriptionsPage)
  const [transcriptions, setTranscriptions] = useState(loadTranscriptions);
  // Listen for new transcriptions
  React.useEffect(() => {
    const handler = () => setTranscriptions(loadTranscriptions());
    window.addEventListener(TRANSCRIPTION_EVENT, handler);
    return () => window.removeEventListener(TRANSCRIPTION_EVENT, handler);
  }, []);

  // Normalise every source into a common shape so the filter + search +
  // sort pipeline is identical regardless of origin. Timestamps arrive in
  // mixed units (backend rows: Unix seconds; story projects: Date.now() ms;
  // transcriptions: ISO strings) — toMillis() normalizes them all to epoch
  // ms (or 0 when missing) so both the sort and timeAgo() stay unit-safe.
  const items = useMemo(() => {
    const list = [];
    for (const p of studioProjects) {
      list.push({
        type: 'dubs',
        id: p.id,
        title: p.name || p.video_path?.split('/').pop() || p.id,
        subtitle: fmtDuration(p.duration),
        ts: toMillis(p.updated_at || p.created_at) ?? 0,
        accent: '#fe8019',
        Icon: Film,
        onClick: () => onOpenDub?.(p.id),
      });
    }
    for (const sp of storyProjects) {
      const tracks = (sp.tracks || []).length;
      const chars = new Set((sp.cast || []).map((c) => c.id)).size;
      list.push({
        type: 'stories',
        id: sp.id,
        title: sp.name || t('projects.untitled_story'),
        subtitle: [
          tracks ? t('projects.story_lines', { count: tracks }) : '',
          chars ? t('projects.story_voices', { count: chars }) : '',
        ]
          .filter(Boolean)
          .join(' · '),
        ts: toMillis(sp.updatedAt) ?? 0,
        accent: '#83a598',
        Icon: BookOpen,
        onClick: () => onOpenStory?.(sp.id),
      });
    }
    for (const pr of profiles) {
      const kind = pr.kind || 'clone';
      list.push({
        type: 'profiles',
        id: pr.id,
        title: pr.name || pr.id,
        subtitle: kind === 'design' ? t('projects.designed_voice') : t('projects.cloned_voice'),
        ts: toMillis(pr.updated_at || pr.created_at) ?? 0,
        accent: kind === 'design' ? '#8ec07c' : '#d3869b',
        Icon: kind === 'design' ? Wand2 : Fingerprint,
        onClick: () => onOpenProfile?.(pr.id),
      });
    }
    for (const h of history) {
      list.push({
        type: 'history',
        id: h.filename || h.id || String(Math.random()),
        title: (h.text || h.prompt || h.filename || t('projects.generated_audio')).slice(0, 80),
        subtitle: h.language || h.voice || '',
        // #epoch-bug regression site: generation_history rows carry created_at
        // in Unix SECONDS — feeding them to a ms-based diff rendered every
        // history card as "20617d ago" (1970) and sorted them last.
        ts: toMillis(h.timestamp || h.created_at) ?? 0,
        accent: '#f3a5b6',
        Icon: Music,
        onClick: undefined,
      });
    }
    for (const e of exportHistory) {
      list.push({
        type: 'exports',
        id: e.path || e.id,
        title: e.path?.split('/').pop() || e.filename || t('projects.export'),
        subtitle: e.mode || '',
        ts: toMillis(e.created_at) ?? 0,
        accent: '#fabd2f',
        Icon: Download,
        onClick: () => e.path && onRevealExport?.(e.path),
      });
    }
    for (const j of longformJobs) {
      const mins = j.duration_s ? `${Math.round(j.duration_s / 60)} min` : '';
      list.push({
        type: 'audiobooks',
        id: j.job_id,
        title: j.title || j.output,
        subtitle: [
          j.type === 'story' ? t('projects.story') : t('projects.audiobook'),
          j.chapters ? `${j.chapters} ch` : '',
          mins,
        ]
          .filter(Boolean)
          .join(' · '),
        ts: toMillis(j.created_at) ?? 0,
        accent: '#d3869b',
        Icon: BookMarked,
        onClick: () => j.output && playRenderInApp(audioUrl(j.output), j.title || j.output),
      });
    }
    for (const tr of transcriptions) {
      list.push({
        type: 'transcripts',
        id: tr.id || String(Math.random()),
        title: (tr.text || t('projects.transcription')).slice(0, 120),
        subtitle: [tr.language, tr.duration_s ? `${Math.round(tr.duration_s)}s` : '']
          .filter(Boolean)
          .join(' · '),
        ts: toMillis(tr.timestamp) ?? 0,
        accent: '#83a598',
        Icon: FileText,
        onClick: () => {
          copyText(tr.text || '');
        },
      });
    }
    list.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    return list;
  }, [
    studioProjects,
    profiles,
    history,
    exportHistory,
    transcriptions,
    longformJobs,
    storyProjects,
    onOpenDub,
    onOpenProfile,
    onOpenStory,
    onRevealExport,
    t,
  ]);

  const counts = useMemo(() => {
    const c = { all: items.length };
    for (const it of items) c[it.type] = (c[it.type] || 0) + 1;
    return c;
  }, [items]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((it) => {
      if (filter !== 'all' && it.type !== filter) return false;
      if (!q) return true;
      return (it.title + ' ' + (it.subtitle || '')).toLowerCase().includes(q);
    });
  }, [items, filter, query]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--bg)] text-[var(--text-primary)]">
      <div className="flex shrink-0 items-center gap-[12px] px-[18px] py-[12px] [border-bottom:1px_solid_var(--chrome-border)] bg-[var(--chrome-bg)]">
        <h1 className="m-0 shrink-0 [font-family:var(--font-sans)] text-[0.92rem] font-semibold tracking-[0.02em] text-[var(--chrome-fg)]">
          {t('projects.title')}
        </h1>
        <div className="flex flex-1 items-center justify-end gap-[8px]">
          <div className="flex h-[var(--chrome-pill-h)] max-w-[420px] flex-1 items-center gap-[6px] rounded-[var(--chrome-radius-pill)] [border:1px_solid_var(--chrome-border)] bg-[var(--chrome-hover-bg)] px-[10px] text-[var(--chrome-fg-muted)]">
            <Search size={12} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('projects.search_placeholder')}
              spellCheck={false}
              className="min-w-0 flex-1 border-0 bg-transparent text-[12px] [font-family:inherit] text-[var(--chrome-fg)] outline-none placeholder:text-[var(--chrome-fg-dim)]"
            />
          </div>
          <div className="inline-flex gap-[1px] overflow-hidden rounded-[var(--chrome-radius-pill)] [border:1px_solid_var(--chrome-border)] bg-[var(--chrome-hover-bg)]">
            {[
              { id: 'grid', Icon: LayoutGrid, title: t('projects.card_grid') },
              { id: 'list', Icon: ListIcon, title: t('projects.list') },
            ].map(({ id, Icon, title }) => (
              <button
                key={id}
                className={`inline-flex h-[var(--chrome-pill-h)] w-[26px] cursor-pointer items-center justify-center border-0 bg-transparent transition-all duration-[0.1s] ${
                  view === id
                    ? 'bg-[var(--chrome-accent-bg)] text-[var(--chrome-accent)]'
                    : 'text-[var(--chrome-fg-muted)] hover:text-[var(--chrome-fg)]'
                }`}
                onClick={() => setView(id)}
                title={title}
                type="button"
              >
                <Icon size={12} />
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[200px_minmax(0,1fr)]">
        <aside className="flex flex-col gap-[2px] overflow-y-auto px-[8px] py-[10px] [border-right:1px_solid_var(--chrome-border)] bg-[var(--chrome-bg)]">
          {FILTERS.map((f) => {
            const FI = f.Icon;
            const n = counts[f.id] ?? 0;
            return (
              <button
                key={f.id}
                type="button"
                className={`flex h-[28px] cursor-pointer items-center gap-[8px] rounded-[var(--chrome-radius-pill)] border px-[10px] py-[6px] text-left text-[12px] [font-family:inherit] transition-all duration-[0.12s] ${
                  filter === f.id
                    ? 'border-[var(--chrome-accent-border)] bg-[var(--chrome-accent-bg)] text-[var(--chrome-accent)]'
                    : 'border-transparent bg-transparent text-[var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--chrome-fg)]'
                }`}
                onClick={() => setFilter(f.id)}
              >
                <FI size={12} />
                <span className="flex-1">{f.label}</span>
                <span
                  className={`[font-family:var(--chrome-font-mono)] text-[10.5px] [font-variant-numeric:tabular-nums] ${
                    filter === f.id ? 'text-[var(--chrome-accent)]' : 'text-[var(--chrome-fg-dim)]'
                  }`}
                >
                  {n}
                </span>
              </button>
            );
          })}
        </aside>

        <section
          className={`min-h-0 overflow-y-auto px-[18px] py-[14px] ${
            view === 'list'
              ? 'flex flex-col gap-[4px]'
              : 'grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-[10px] [align-content:start]'
          }`}
        >
          {visible.length === 0 && (
            <div className="flex flex-col items-center gap-[8px] px-[16px] py-[48px] text-center text-[var(--chrome-fg-dim)] [grid-column:1/-1]">
              <FolderOpen size={28} />
              <p className="max-w-[380px] m-0 text-[0.82rem] leading-[1.5]">
                {query ? t('projects.no_matches', { query }) : t('projects.empty_hint')}
              </p>
            </div>
          )}
          {visible.map((it) => (
            <Card
              key={`${it.type}:${it.id}`}
              kind={it.type.toUpperCase()}
              accent={it.accent}
              title={it.title}
              subtitle={it.subtitle}
              trailing={
                <span className="inline-flex items-center gap-[3px] [font-family:var(--chrome-font-mono)]">
                  <Clock size={10} />
                  {timeAgo(it.ts)}
                </span>
              }
              onClick={it.onClick}
              IconC={it.Icon}
              view={view}
            />
          ))}
        </section>
      </div>
    </div>
  );
}
