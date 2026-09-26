import { fmtBytes } from './format';

/**
 * The one-line progress caption under a downloading model row — shared by the
 * catalogue's per-engine weights list and the leftover model store so the two
 * never drift. `rt` is the row runtime from computeRowRuntime; `speedRef` the
 * host's per-repo speed sampler (fallback when the backend sends no rate).
 */
export function describeProgress(rt, t, speedRef, repoId) {
  if (rt.isDeleting) return t('models.removing_cached');
  const hasAgg = !!rt.agg;
  if (!rt.hasFiles && !hasAgg) {
    if (rt.phase === 'resolving') {
      const dots = '.'.repeat((rt.rs?.resolvingStep || 0) % 4);
      // Once the preflight plan lands we can show the real size even before
      // the first byte.
      const planBytes = rt.plan?.to_download_bytes;
      const planStr = planBytes
        ? ` · ${fmtBytes(planBytes)} ${t('models.to_download') || 'to download'}`
        : '';
      return `${t('models.resolving_metadata')}${dots}${planStr}`;
    }
    if (rt.phase === 'install_retry') {
      return t('models.retry_attempt', {
        attempt: rt.rs?.retryAttempt || '?',
        error: rt.rs?.error || 'reconnecting',
      });
    }
    return t('models.connecting_hf');
  }

  // Prefer the backend aggregate's windowed rate; fall back to the frontend
  // sampler only until it arrives.
  let speed = rt.aggRate ?? 0;
  if (!(speed > 0)) {
    const sp = speedRef.current[repoId];
    const now = Date.now();
    if (sp && rt.dispDownloaded > 0) {
      const dt = (now - sp.lastTime) / 1000;
      if (dt >= 1) {
        sp.speed = Math.max(0, (rt.dispDownloaded - sp.lastBytes) / dt);
        sp.lastBytes = rt.dispDownloaded;
        sp.lastTime = now;
      }
    } else {
      speedRef.current[repoId] = { lastBytes: rt.dispDownloaded, lastTime: now, speed: 0 };
    }
    speed = rt.backendRate > 0 ? rt.backendRate : sp?.speed || 0;
  }

  // Total unknown and nothing downloaded yet → still resolving
  if (rt.dispTotal === 0 && rt.dispDownloaded === 0) {
    const activeFile = rt.activeFilename?.split('/').pop();
    return activeFile
      ? t('models.resolving_files_active', { count: rt.fileList.length, file: activeFile })
      : t('models.resolving_files', { count: rt.fileList.length });
  }

  // ETA: prefer the backend's, else derive from remaining/speed.
  const remaining = Math.max(0, rt.dispTotal - rt.dispDownloaded);
  const etaSec =
    rt.aggEtaSec != null ? rt.aggEtaSec : speed > 0 && rt.dispTotal > 0 ? remaining / speed : 0;
  const etaStr =
    etaSec > 0
      ? etaSec < 60
        ? `~${Math.ceil(etaSec)}s`
        : etaSec < 3600
          ? `~${Math.ceil(etaSec / 60)}m`
          : `~${(etaSec / 3600).toFixed(1)}h`
      : '';
  const dlStr = fmtBytes(rt.dispDownloaded) || '0 B';
  const totalStr = rt.dispTotal > 0 ? fmtBytes(rt.dispTotal) : '…';
  const pctStr = rt.aggPct != null && rt.aggPct > 0 ? `${Math.round(rt.aggPct)}%` : '';
  const speedStr = speed > 0 ? `${fmtBytes(speed)}/s` : '';

  const parts = [
    `${dlStr} / ${totalStr}`,
    pctStr,
    speedStr || (rt.dispDownloaded > 0 ? t('models.measuring') : ''),
    etaStr,
  ].filter(Boolean);

  const extra = [];
  if (rt.cachedBytes > 0)
    extra.push(`${fmtBytes(rt.cachedBytes)} ${t('models.cached') || 'cached'}`);
  if (rt.filesTotal > 1)
    extra.push(t('models.files_progress', { done: rt.filesDone, total: rt.filesTotal }));
  if (rt.activeFilename) extra.push(rt.activeFilename.split('/').pop());

  return extra.length ? `${parts.join(' · ')}  ⸱  ${extra.join(' · ')}` : parts.join(' · ');
}
