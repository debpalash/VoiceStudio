/**
 * Derive a model row's runtime display state from the live SSE-driven
 * `rowState` map and the set of `busy` repo ids. Pure — no React/closure deps
 * beyond its arguments — so it can be memoized by the host component.
 */
export function computeRowRuntime(m, rowState, busy) {
  const rs = rowState[m.repo_id];
  const rowBusy = busy.has(m.repo_id);
  const isInstalling =
    rs?.phase === 'install_start' || (rs?.phase === 'active' && !rs.files && !rs.error);
  const isDeleting = rs?.phase === 'delete_start';
  const phase = rs?.phase;
  const fileList = rs?.files ? Object.entries(rs.files) : [];
  const totals = fileList.reduce(
    (a, [, f]) => ({
      downloaded: a.downloaded + (f.downloaded || 0),
      total: a.total + (f.total || 0),
      done: a.done + (f.phase === 'done' ? 1 : 0),
    }),
    { downloaded: 0, total: 0, done: 0 },
  );
  // Sum backend-reported rate from active (non-done) files
  const backendRate = fileList
    .filter(([, f]) => f.phase !== 'done' && f.rate > 0)
    .reduce((s, [, f]) => s + f.rate, 0);
  const hasFiles = fileList.length > 0;
  const showBar = [
    'install_start',
    'resolving',
    'install_retry',
    'active',
    'delete_start',
  ].includes(phase);
  const activeFilename = fileList.find(([, f]) => f.phase !== 'done')?.[0];
  const unsupported = m.supported === false;

  // Overall progress: prefer the backend aggregate (FDL-06) — it sums bytes
  // across all parallel files/chunks and samples a windowed rate, which is
  // accurate under Xet's parallel fetch. Fall back to per-file summation +
  // the frontend speed sampler only until the first aggregate event lands.
  const agg = rs?.agg || null;
  const plan = rs?.plan || null;
  const dispDownloaded = agg ? agg.bytes_done || 0 : totals.downloaded;
  // Denominator: aggregate total → preflight "to download" → per-file totals.
  const dispTotal = (agg?.total_bytes ?? plan?.to_download_bytes ?? totals.total) || 0;
  // Bar %: prefer byte-fraction, but under Xet the byte bars don't advance
  // mid-download (only the file-count bar does), so fall back to the file
  // fraction so the bar still moves. Take the max so whichever signal is
  // live drives it; complete() flushes both to 100% at the end.
  const bytePct = dispTotal > 0 ? (dispDownloaded / dispTotal) * 100 : 0;
  const filesTotalForPct = agg?.files_total ?? plan?.n_files ?? 0;
  const filePct = filesTotalForPct > 0 ? ((agg?.files_done ?? 0) / filesTotalForPct) * 100 : 0;
  const aggPct = dispTotal > 0 || filesTotalForPct > 0 ? Math.max(bytePct, filePct) : null;
  const cachedBytes = plan?.cached_bytes ?? null;
  const filesTotal = agg?.files_total ?? plan?.n_files ?? (hasFiles ? fileList.length : null);
  const filesDone = agg?.files_done ?? totals.done;
  // Backend rate (windowed aggregate) wins over the per-file rate sum.
  const aggRate = agg?.rate ?? null;
  const aggEtaSec = agg?.eta_seconds ?? null;

  return {
    rs,
    rowBusy,
    isInstalling,
    isDeleting,
    phase,
    fileList,
    totals,
    hasFiles,
    aggPct,
    showBar,
    activeFilename,
    unsupported,
    backendRate,
    agg,
    plan,
    dispDownloaded,
    dispTotal,
    cachedBytes,
    filesTotal,
    filesDone,
    aggRate,
    aggEtaSec,
  };
}
