/**
 * Pure reducer for the Model Store's per-repo download `rowState`, driven by the
 * shared `/setup/download-stream` SSE feed. Extracted from ModelStoreTab so the
 * state transitions are unit-testable — in particular that an `install_error`
 * event is STORED (with its mirror-aware `ev.error` text) and PERSISTS across
 * later events, instead of vanishing (P1-A: async install errors were invisible).
 *
 * Shape: { [repo_id]: { phase, files: {[filename]: {...}}, error, agg, plan,
 *                       resolvingStep, retryAttempt } }
 */

/**
 * Terminal phases that are safe to auto-purge from row state after a short beat
 * (the row then reverts to the authoritative `installed` flag from /models).
 * `install_error` is intentionally NOT here — an error must stay on the row,
 * with a Retry/Dismiss affordance, until the user acts on it (P1-A).
 */
const AUTO_PURGE_TERMINALS = new Set(['install_done', 'delete_done', 'install_cancelled']);

export function downloadKey(target, repoId) {
  return `${target || 'local'}\u0000${repoId}`;
}

export function progressForRepo(progress, repoId, target = null) {
  if (target) return progress?.[downloadKey(target, repoId)];
  return Object.entries(progress || {}).find(([key]) => key.endsWith(`\u0000${repoId}`))?.[1];
}

export function isAutoPurgeTerminal(phase) {
  return AUTO_PURGE_TERMINALS.has(phase);
}

/** Any terminal phase (success OR error) — used to trigger a list refetch. */
export function isTerminalPhase(phase) {
  return isAutoPurgeTerminal(phase) || phase === 'install_error';
}

/**
 * Fold one SSE event into the rowState map. Returns the map unchanged when the
 * event carries no repo_id (keepalive / malformed). Never mutates `prev`.
 */
export function reduceModelDownloadEvent(prev, ev) {
  if (!ev || !ev.repo_id) return prev;
  const key = downloadKey(ev.target, ev.repo_id);
  const cur = prev[key] || { phase: 'active', files: {} };

  // Lifecycle events flip the row's phase without touching per-file accounting.
  if (ev.phase === 'install_start' || ev.phase === 'delete_start') {
    return { ...prev, [key]: { phase: ev.phase, files: {}, error: null } };
  }
  // Heartbeat from backend while resolving repo metadata.
  if (ev.phase === 'resolving') {
    return {
      ...prev,
      [key]: { ...cur, phase: 'resolving', resolvingStep: ev.step || 0 },
    };
  }
  if (ev.phase === 'install_retry') {
    return {
      ...prev,
      [key]: { ...cur, phase: 'install_retry', retryAttempt: ev.attempt, error: ev.error },
    };
  }
  if (ev.phase === 'install_done') {
    return { ...prev, [key]: { ...cur, phase: 'install_done' } };
  }
  if (ev.phase === 'delete_done') {
    return { ...prev, [key]: { ...cur, phase: 'delete_done' } };
  }
  // Errors carry the mirror-aware failure text (#890 core/failure.py). Keep it
  // on the row — the purge effect must NOT auto-clear it (P1-A).
  if (ev.phase === 'install_error') {
    return { ...prev, [key]: { ...cur, phase: 'install_error', error: ev.error } };
  }
  if (ev.phase === 'install_cancelled') {
    return { ...prev, [key]: { ...cur, phase: 'install_cancelled' } };
  }
  // Pre-flight plan (FDL-05): accurate total/cached/remaining BEFORE bytes flow.
  // Keep the current phase (usually resolving) — the plan is metadata.
  if (ev.phase === 'install_plan') {
    return {
      ...prev,
      [key]: {
        ...cur,
        plan: {
          total_bytes: ev.total_bytes ?? null,
          cached_bytes: ev.cached_bytes ?? null,
          to_download_bytes: ev.to_download_bytes ?? null,
          n_files: ev.n_files ?? null,
          n_cached: ev.n_cached ?? null,
        },
      },
    };
  }
  // Overall aggregate (FDL-06): the source of truth for bar / speed / ETA.
  if (ev.phase === 'aggregate') {
    return {
      ...prev,
      [key]: {
        ...cur,
        phase: 'active',
        agg: {
          bytes_done: ev.bytes_done ?? 0,
          total_bytes: ev.total_bytes ?? null,
          rate: ev.rate ?? 0,
          eta_seconds: ev.eta_seconds ?? null,
          files_done: ev.files_done ?? 0,
          files_total: ev.files_total ?? null,
        },
      },
    };
  }
  // Per-file tqdm events — aggregate across files.
  const files = {
    ...cur.files,
    [ev.filename]: {
      downloaded: ev.downloaded || 0,
      total: ev.total || 0,
      pct: ev.pct || 0,
      phase: ev.phase,
      rate: ev.rate || 0,
    },
  };
  return { ...prev, [key]: { ...cur, phase: 'active', files } };
}
