/**
 * WizardLibrary — the first-run "stock the studio" act as ONE unified list.
 *
 * Models and engines are different things (weights vs backends), but the
 * user's question is singular — "what do I need to get?" — so every
 * installable is a row of the same grammar:
 *
 *   LED · name · chip (required / engine / optional) · size · one action
 *
 * Required models lead (they gate the wizard's continue), the TTS engines
 * follow (Use = switch, heavy installs deferred to Settings), and the long
 * tail of optional models folds behind a quiet count. Live download
 * progress rides the same SSE stream the Settings model store uses; the
 * full management surface (search, HF token, deletes) stays in Settings —
 * a first run needs a checklist, not a store.
 *
 * Built on standard shadcn primitives (Badge / Button / Progress) + Tailwind
 * utilities themed by the palette tokens.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-hot-toast';
import { Check, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useEngines, useModels, useInstallModel, useSelectEngine } from '../api/hooks';
import { setupDownloadStreamUrl } from '../api/setup';
import { notifyEngineSelected } from '../utils/engineSelectToast';
import MirrorRescue from './MirrorRescue';
import { Badge, Button } from '../ui';

const fmtGB = (gb) => (gb == null ? '' : `${gb.toFixed(gb < 10 ? 1 : 0)} GB`);

/** Human-readable byte size, e.g. 734003200 -> "700 MB", 1610612736 -> "1.5 GB". */
export function fmtBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  const mb = bytes / (1024 * 1024);
  if (mb < 1024) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
  const gb = mb / 1024;
  return `${gb < 10 ? gb.toFixed(1) : Math.round(gb)} GB`;
}

/** Instantaneous download rate, e.g. 5452595 -> "5.2 MB/s". Blank when idle. */
export function fmtRate(bytesPerSec) {
  if (!Number.isFinite(bytesPerSec) || bytesPerSec <= 0) return '';
  const mb = bytesPerSec / (1024 * 1024);
  if (mb >= 1) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB/s`;
  const kb = bytesPerSec / 1024;
  return `${Math.max(1, Math.round(kb))} KB/s`;
}

/**
 * A model is a "platform pick" when it explicitly targets one of THIS host's
 * platform tags (the MLX mac-ARM speedups, CUDA-tuned variants, …) — the best
 * optional models for this machine, surfaced by default instead of buried in
 * the tail. Models with no `platforms` field are universal (not a pick — they
 * ride the fold). Pure + exported so the split is unit-testable.
 */
export function isPlatformPick(model, platformTags) {
  return (
    Array.isArray(model?.platforms) &&
    Array.isArray(platformTags) &&
    model.platforms.some((p) => platformTags.includes(p))
  );
}

/**
 * A model is "recommended" for this host when the backend marks it curated
 * (`curated_on` in models.yaml — the same signal GET /setup/recommendations
 * and the Settings model store use), so the wizard and the store can never
 * disagree about what "recommended" means. Falls back to the platform-tag
 * heuristic for older backends whose /models rows carry no `curated` flag.
 * Required models are excluded — they already wear the stronger chip.
 * Pure + exported for unit tests.
 */
export function isRecommendedPick(model, platformTags) {
  if (!model || model.required) return false;
  if (typeof model.curated === 'boolean') return model.curated;
  return isPlatformPick(model, platformTags);
}

/** Overall progress from the backend's authoritative `aggregate` SSE event.
 * This is the TRUSTWORTHY source: under parallel/segmented fetch the per-file
 * tqdm events are unreliable (big weight shards may report total/rate as 0),
 * so summing them on the frontend gave wrong numbers — e.g. "8% · 1 KB/s ·
 * 0.0 MB left". The backend computes one windowed rate + ETA + bytes_done /
 * total_bytes (seeded by the dry-run preflight), which is what we render. Pure
 * + exported for unit tests. Returns null until totals are known. */
export function progressFromAgg(agg) {
  if (!agg || !(agg.totalBytes > 0)) return null;
  const pct = Math.min(100, Math.round((agg.bytesDone / agg.totalBytes) * 100));
  const remaining = agg.totalBytes > agg.bytesDone ? agg.totalBytes - agg.bytesDone : null;
  return { pct, remaining, rate: agg.rate || 0, etaSec: agg.etaSeconds ?? null };
}

/** Fallback: aggregate one repo's per-file SSE events when the authoritative
 * `aggregate` event hasn't arrived yet. Exported (pure) for unit tests. */
export function aggregate(files) {
  let done = 0;
  let total = 0;
  let rate = 0;
  for (const f of Object.values(files || {})) {
    done += f.downloaded || 0;
    total += f.total || 0;
    if ((f.total || 0) > (f.downloaded || 0)) rate += f.rate || 0;
  }
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;
  const remaining = total > done ? total - done : null;
  const etaSec = rate > 0 && remaining ? remaining / rate : null;
  return { pct, etaSec, rate, remaining };
}

function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  if (seconds < 60) return '<1m';
  return `${Math.round(seconds / 60)}m`;
}

/**
 * Fold one download-stream SSE event into the wizard's per-repo progress map.
 * Pure + exported for unit tests. Mirrors the Settings store's transitions, but
 * with the wizard's leaner shape ({ phase, files, agg }).
 *
 * Key fix (P1-A): an `install_error` event is STORED with its `ev.error` text
 * (the mirror-aware failure hint) and the row PERSISTS — previously the wizard
 * deleted the row on error exactly like a success, so the user saw the download
 * vanish with no reason. `install_done` still drops the row (it reverts to the
 * authoritative `installed` flag); the caller does the list refetch.
 */
export function reduceWizardDownloadEvent(prev, ev) {
  if (!ev || !ev.repo_id) return prev;
  const key = `${ev.target || 'local'}\u0000${ev.repo_id}`;
  const cur = prev[key] || { phase: 'active', files: {} };
  // Lifecycle markers gate reset; a file-level 'done' must NOT clear the repo.
  if (ev.phase === 'install_start') {
    return { ...prev, [key]: { phase: 'active', files: {} } };
  }
  // Success terminal → drop the transient row.
  if (ev.phase === 'install_done') {
    const next = { ...prev };
    delete next[key];
    return next;
  }
  // Error terminal → KEEP the row + its message so it renders with a Retry.
  // docs_topic carries the backend's failure class (core.failure.classify) so
  // the wizard can react structurally — e.g. HF_MIRROR_UNREACHABLE raises the
  // inline mirror picker — instead of string-matching the error text.
  if (ev.phase === 'install_error') {
    return {
      ...prev,
      [key]: {
        ...cur,
        phase: 'install_error',
        error: ev.error,
        docsTopic: ev.docs_topic || '',
      },
    };
  }
  // Authoritative overall progress (download_aggregator).
  if (ev.phase === 'aggregate') {
    return {
      ...prev,
      [key]: {
        ...cur,
        agg: {
          bytesDone: ev.bytes_done || 0,
          totalBytes: ev.total_bytes || 0,
          rate: ev.rate || 0,
          etaSeconds: ev.eta_seconds ?? null,
          filesDone: ev.files_done || 0,
          filesTotal: ev.files_total || 0,
        },
      },
    };
  }
  if (!ev.filename) return prev;
  const files = {
    ...cur.files,
    [ev.filename]: {
      downloaded: ev.downloaded || 0,
      total: ev.total || 0,
      rate: ev.rate || 0,
    },
  };
  return { ...prev, [key]: { ...cur, files } };
}

/**
 * Repo ids whose install failed because the CONFIGURED Hugging Face mirror is
 * unreachable (#874 class). Non-empty → the wizard shows the inline mirror
 * picker next to the failed rows: during first-run the Settings panel the
 * error hint names is unreachable (the wizard gates the studio), so the
 * switch-and-retry affordance must live right here. Pure + exported for tests.
 */
export function mirrorBlockedRepos(progress) {
  return Object.entries(progress || {})
    .filter(([, p]) => p?.phase === 'install_error' && p?.docsTopic === 'HF_MIRROR_UNREACHABLE')
    .map(([key]) => key.split('\u0000').at(-1));
}

// LED dot tone per row state.
const LED_TONE = {
  ok: 'bg-success shadow-[0_0_5px_1px_color-mix(in_srgb,var(--color-success)_50%,transparent)]',
  active: 'bg-primary shadow-[0_0_6px_1px_var(--color-brand-glow)]',
  busy: 'bg-primary fr-pulse',
  off: 'bg-fg-subtle/40',
};

// Chip Badge tone per chip category.
const CHIP_TONE = { req: 'brand', rec: 'success', eng: 'neutral', opt: 'neutral' };

function Row({ led, name, chip, chipTone, chipTitle, size, action, sub }) {
  return (
    <div className="flex items-center gap-3 rounded-md px-3 py-2 transition-colors hover:bg-bg-elev-3">
      <span
        className={cn('h-1.5 w-1.5 shrink-0 rounded-full', LED_TONE[led] || LED_TONE.off)}
        aria-hidden="true"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex items-center gap-2 text-sm font-semibold">
          {name}
          {chip && (
            <Badge tone={CHIP_TONE[chipTone] || 'neutral'} size="xs" title={chipTitle}>
              {chip}
            </Badge>
          )}
        </span>
        {sub && <span className="block min-w-0">{sub}</span>}
      </div>
      <span className="shrink-0 font-mono text-[0.64rem] tabular-nums text-fg-muted">{size}</span>
      {action}
    </div>
  );
}

export default function WizardLibrary() {
  const { t } = useTranslation();
  const modelsQuery = useModels();
  const installMutation = useInstallModel();
  const { data: engineInventory } = useEngines();
  const selectMutation = useSelectEngine();
  const engines = engineInventory?.tts ?? null;
  const [progress, setProgress] = useState({}); // { repo_id: { phase, files } }
  const [showTail, setShowTail] = useState(false);
  const [switching, setSwitching] = useState(null);
  const esRef = useRef(null);

  const models = useMemo(() => {
    const list = modelsQuery.data;
    return Array.isArray(list) ? list : (list?.models ?? []);
  }, [modelsQuery.data]);

  // Host platform tags (e.g. ['darwin', 'darwin-arm64']) so we can surface the
  // models tuned for THIS machine by default instead of burying them.
  const platformTags = useMemo(() => {
    const d = modelsQuery.data;
    return Array.isArray(d) ? [] : (d?.platform_tags ?? []);
  }, [modelsQuery.data]);

  // One SSE stream for all rows (same channel the Settings store uses).
  useEffect(() => {
    const es = new EventSource(setupDownloadStreamUrl());
    esRef.current = es;
    es.onmessage = (evt) => {
      try {
        const ev = JSON.parse(evt.data);
        if (!ev?.repo_id) return;
        // Refetch the list once the repo finishes so the row flips to installed.
        // (The reducer is pure — the side-effect stays here.)
        if (ev.phase === 'install_done') modelsQuery.refetch();
        // Pure reducer (exported for tests). Full phase taxonomy:
        // SetupProgressEvent in api/setup.ts. install_error now PERSISTS with
        // its message instead of the row silently vanishing (P1-A).
        setProgress((prev) => reduceWizardDownloadEvent(prev, ev));
      } catch {
        /* keepalive */
      }
    };
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const install = (repoId) => {
    const key = `local\u0000${repoId}`;
    setProgress((p) => ({ ...p, [key]: { phase: 'active', files: {} } }));
    installMutation.mutate(repoId, {
      onError: (e) => {
        toast.error(e?.message || 'install failed');
        setProgress((p) => {
          const n = { ...p };
          delete n[key];
          return n;
        });
      },
    });
  };

  const useEngine = async (id) => {
    setSwitching(id);
    try {
      const r = await selectMutation.mutateAsync({ family: 'tts', backendId: id });
      // Consume the routing echo: warn when the pick lands on a CPU fallback
      // on this host, otherwise confirm the switch. See notifyEngineSelected.
      notifyEngineSelected(r, t, 'tts');
    } catch (e) {
      toast.error(e?.message || 'switch failed');
    } finally {
      setSwitching(null);
    }
  };

  const supported = models.filter((m) => m.supported !== false);
  const required = supported.filter((m) => m.required);
  const optionalAll = supported.filter((m) => !m.required);
  // Curated "best for your system" optionals lead (shown by default with the
  // recommended chip — same `curated` signal the Settings model store badges);
  // the universal long tail still folds behind a quiet count.
  const platformPicks = optionalAll.filter((m) => isRecommendedPick(m, platformTags));
  const tail = optionalAll.filter((m) => !isRecommendedPick(m, platformTags));

  const modelRow = (m, chip, chipTone, note, chipTitle) => {
    const p = Object.entries(progress).find(([key]) => key.endsWith(`\u0000${m.repo_id}`))?.[1];
    // A failed install PERSISTS (P1-A): show the mirror-aware reason + a Retry
    // instead of the row silently vanishing.
    const errored = p?.phase === 'install_error';
    // Prefer the backend's authoritative aggregate; fall back to per-file sums
    // only until that event arrives (then to nulls when nothing's streaming).
    const { pct, etaSec, rate, remaining } =
      progressFromAgg(p?.agg) ||
      (p ? aggregate(p.files) : { pct: null, etaSec: null, rate: 0, remaining: null });
    const downloading = !!p && !errored;
    // Live telemetry line: "5.2 MB/s · 700 MB left · ~3m". Each part only shows
    // once the SSE stream has the data, so early on it degrades to "downloading…".
    const rateStr = fmtRate(rate);
    const remainStr = fmtBytes(remaining);
    const etaStr = etaSec != null ? formatEta(etaSec) : '';
    const statParts = [
      pct != null ? `${pct}%` : null,
      rateStr || null,
      remainStr
        ? t('firstrun.size_left', { size: remainStr, defaultValue: '{{size}} left' })
        : null,
      etaStr ? t('firstrun.eta_left', { eta: etaStr, defaultValue: '~{{eta}} left' }) : null,
    ].filter(Boolean);
    return (
      <Row
        key={m.repo_id}
        led={m.installed ? 'ok' : downloading ? 'busy' : 'off'}
        name={m.label}
        chip={chip}
        chipTone={chipTone}
        chipTitle={chipTitle}
        size={fmtGB(m.size_gb)}
        sub={
          errored ? (
            <span className="block max-w-[520px] font-mono text-[0.64rem] leading-snug text-danger">
              {t('firstrun.lib_install_failed', {
                error: p.error,
                defaultValue: 'Install failed: {{error}}',
              })}
            </span>
          ) : downloading ? (
            <span className="block h-[3px] max-w-[280px] overflow-hidden rounded-full bg-fg/[0.08]">
              <span
                className="block h-full rounded-full bg-primary transition-[width] duration-300"
                style={{ width: `${pct ?? 4}%` }}
              />
            </span>
          ) : (
            note || null
          )
        }
        action={
          m.installed ? (
            <Check size={14} className="shrink-0 text-success" aria-hidden="true" />
          ) : errored ? (
            <Button variant="ghost" size="sm" onClick={() => install(m.repo_id)}>
              {t('firstrun.lib_retry', 'Retry')}
            </Button>
          ) : downloading ? (
            <span className="shrink-0 font-mono text-[0.64rem] tabular-nums text-primary">
              {statParts.length
                ? statParts.join(' · ')
                : t('firstrun.lib_downloading', 'downloading…')}
            </span>
          ) : (
            <Button variant="ghost" size="sm" onClick={() => install(m.repo_id)}>
              {t('firstrun.lib_download', 'Download')}
            </Button>
          )
        }
      />
    );
  };

  return (
    <div className="flex max-h-[min(56vh,620px)] flex-col gap-1 overflow-y-auto">
      {required.map((m) => modelRow(m, t('firstrun.chip_required', 'required'), 'req'))}

      {/* Curated optionals for THIS machine — shown by default with the
          catalog note explaining why (e.g. "5× faster on Apple Silicon").
          The chip tooltip spells out that these are optional. */}
      {platformPicks.map((m) =>
        modelRow(
          m,
          t('firstrun.chip_recommended', 'recommended'),
          'rec',
          m.note,
          t('firstrun.chip_recommended_title', 'Recommended for this machine — optional'),
        ),
      )}

      {(engines?.backends ?? []).map((b) => (
        <Row
          key={b.id}
          led={b.id === engines.active ? 'active' : b.available ? 'ok' : 'off'}
          name={b.display_name}
          chip={t('firstrun.chip_engine', 'engine')}
          chipTone="eng"
          size=""
          action={
            b.id === engines.active ? (
              <span className="shrink-0 font-mono text-[0.64rem] text-primary">
                {t('firstrun.lib_active', 'active')}
              </span>
            ) : b.available ? (
              <Button
                variant="ghost"
                size="sm"
                disabled={switching === b.id}
                // eslint-disable-next-line react-hooks/rules-of-hooks -- useEngine is an action fn, not a React hook
                onClick={() => useEngine(b.id)}
              >
                {t('firstrun.lib_use', 'Use')}
              </Button>
            ) : (
              <span
                className="shrink-0 font-mono text-[0.64rem] text-fg-muted"
                title={b.reason || undefined}
              >
                {t('firstrun.lib_in_settings', 'install later in Settings')}
              </span>
            )
          }
        />
      ))}

      {tail.length > 0 && !showTail && (
        <Button
          variant="ghost"
          size="sm"
          className="self-start"
          onClick={() => setShowTail(true)}
          leading={<ChevronRight size={12} />}
        >
          {t('firstrun.lib_show_all', {
            count: tail.length,
            defaultValue: 'Show {{count}} more models',
          })}
        </Button>
      )}
      {showTail && tail.map((m) => modelRow(m, t('firstrun.chip_optional', 'optional'), 'opt'))}
      {/* A download failed because the CONFIGURED mirror is unreachable: the
          error hint points at Settings, but the wizard gates the studio — so
          the mirror picker renders right here. PUT /hf-mirror applies to
          downloads immediately and clears the install cooldown, so the failed
          rows retry the moment a new endpoint is applied. */}
      {mirrorBlockedRepos(progress).length > 0 && (
        <MirrorRescue
          onApplied={() => mirrorBlockedRepos(progress).forEach((repoId) => install(repoId))}
        />
      )}
      {Object.keys(progress).length > 0 && (
        <p className="m-0 text-xs text-fg-subtle">
          {t(
            'firstrun.resume_note',
            'Interrupted downloads resume automatically — closing the app is safe.',
          )}
        </p>
      )}
    </div>
  );
}
