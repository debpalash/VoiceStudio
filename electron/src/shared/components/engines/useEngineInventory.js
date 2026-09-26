import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toastErrorWithReport } from '../../utils/errorToast';
import {
  listEngines,
  getEngineHealth,
  selfTestEngine,
  installSidecarEngine,
  getSidecarInstallStatus,
  getEngineDiskUsage,
} from '../../api/engines';
import { listLoadedModels, unloadLoadedModel } from '../../api/system';
import { copyText } from '../../utils/copyText';

/**
 * useEngineInventory — every piece of engine-list state that is NOT layout.
 *
 * Extracted verbatim from the former EngineCompatibilityMatrix component so
 * the list + detail view can be re-laid-out without touching the parts that
 * carry the hard-won behaviour: the shared-vs-local /engines fetch, memory
 * residency, health + self-test cooldowns, the one-click install poller with
 * its overlap guard and per-engine request epoch, disk-usage generations,
 * license dialogs and the curated-model pick.
 *
 * Cross-platform contract (unchanged): nothing here spawns a sidecar on
 * mount; the user must click Test engine. The 5 s cooldown on Test /
 * Self-test prevents click-storms.
 */
export const TEST_COOLDOWN_MS = 5000;

// How long a forced (Install-click) status refresh will wait for an already
// in-flight request to settle before proceeding anyway. A wedged request has
// no abort signal, so without a bound the click would trade "silently
// dropped" for "silently stuck"; the per-engine epoch makes proceeding safe.
// Exported for the regression test (fake timers).
export const FORCE_WAIT_TIMEOUT_MS = 5000;

export const FAMILIES = ['tts', 'asr', 'llm'];

/** Subset of the unified engine entry the list actually reads. */
export function normalizeEntry(entry) {
  return {
    id: entry.id,
    display_name: entry.display_name,
    available: !!entry.available,
    reason: entry.reason || null,
    // Available-but-has-advice (e.g. VoxCPM2's upgrade hint). Absent on legacy payloads.
    hint: entry.hint || null,
    // Cloning capability: only an explicit true earns the badge (null =
    // model-dependent, e.g. mlx-audio; absent = legacy payload).
    supports_cloning: entry.supports_cloning === true,
    install_hint: entry.install_hint || null,
    last_error: entry.last_error || null,
    isolation_mode: entry.isolation_mode || 'in-process',
    gpu_compat:
      Array.isArray(entry.gpu_compat) && entry.gpu_compat.length > 0 ? entry.gpu_compat : ['cpu'],
    // Copy-paste `export VAR=...` line for a path-gated opt-in engine, or null.
    setup_snippet: entry.setup_snippet || null,
    // Where to send a user whose engine is unavailable (#1866).
    docs_url: entry.docs_url || null,
    // The backend's sidecar provisioner can install this engine in-app.
    one_click_install: entry.one_click_install === true,
    local_install_required: entry.local_install_required === true,
    // Routing (#21) — may be absent on a legacy/older backend payload.
    effective_device: entry.effective_device || null,
    routing_status: entry.routing_status || null,
    routing_reason: entry.routing_reason || null,
    // #981 — mlx-audio ONLY: the curated-model roster + current pick.
    curated_models: Array.isArray(entry.curated_models) ? entry.curated_models : null,
    active_model_id: entry.active_model_id || null,
    disk_usage: entry.disk_usage || null,
  };
}

export default function useEngineInventory({
  family = 'tts',
  onSelect = null,
  reloadToken = 0,
  sharedEngines = null,
  apiListEngines = listEngines,
  apiGetEngineHealth = getEngineHealth,
  apiSelfTestEngine = selfTestEngine,
  apiListLoadedModels = listLoadedModels,
  apiUnloadModel = unloadLoadedModel,
  apiInstallEngine = installSidecarEngine,
  apiInstallStatus = getSidecarInstallStatus,
  apiGetDiskUsage = getEngineDiskUsage,
}) {
  const { t } = useTranslation();
  const [localData, setLocalData] = useState(null);
  const [localLoading, setLocalLoading] = useState(true);
  const [localError, setLocalError] = useState(null);
  const sharedRefetch = sharedEngines?.refetch;
  const isShared = Boolean(sharedEngines);
  const sharedReloadToken = useRef(reloadToken);
  const data = sharedEngines?.data ?? localData;
  const loading = isShared ? sharedEngines.isLoading : localLoading;
  const error = sharedEngines?.error ?? localError;
  const [activeFamily, setActiveFamily] = useState(family);
  // Which engine has its license dialog currently open, or null.
  const [licenseDialogFor, setLicenseDialogFor] = useState(null);

  // health state keyed by engine id:
  //   { [id]: { inflight, ok?, message?, latency_ms?, lastClickAt? } }
  const [healthByEngine, setHealthByEngine] = useState({});
  // Self-test (real tiny synthesis) state keyed by engine id, same shape as
  // health plus { duration_ms, sample_rate, audio_seconds, timed_out }.
  const [selfTestByEngine, setSelfTestByEngine] = useState({});
  // Which engine's setup snippet was just copied (transient ✓ affordance).
  const [copiedId, setCopiedId] = useState(null);
  const [diskByEngine, setDiskByEngine] = useState({});
  const diskGenerationRef = useRef(0);

  const invalidateDiskUsage = useCallback(() => {
    diskGenerationRef.current += 1;
    setDiskByEngine({});
  }, []);
  // Memory residency: engine id → its /model/loaded entry. Advisory — load
  // failures leave it empty and the list renders without residency chips.
  const [loadedByEngine, setLoadedByEngine] = useState({});
  const [unloadingId, setUnloadingId] = useState(null);

  useEffect(() => {
    setActiveFamily(family);
  }, [family]);

  const refreshResidency = useCallback(async () => {
    try {
      const res = await apiListLoadedModels();
      const byEngine = {};
      for (const m of res?.models || []) {
        if (m?.engine_id) byEngine[m.engine_id] = m;
      }
      setLoadedByEngine(byEngine);
    } catch {
      // /model/loaded is cheap but advisory — never let it break the list.
      setLoadedByEngine({});
    }
  }, [apiListLoadedModels]);

  const reload = useCallback(async () => {
    invalidateDiskUsage();
    if (sharedRefetch) {
      const result = await sharedRefetch();
      if (result.error) {
        const message = result.error?.message || String(result.error);
        toastErrorWithReport(t('engines.loadFailed', { message }), result.error);
      }
    } else {
      setLocalLoading(true);
      setLocalError(null);
      try {
        setLocalData(await apiListEngines());
      } catch (requestError) {
        const message = requestError?.message || String(requestError);
        setLocalError(requestError);
        toastErrorWithReport(t('engines.loadFailed', { message }), requestError);
      } finally {
        setLocalLoading(false);
      }
    }
    refreshResidency();
  }, [apiListEngines, invalidateDiskUsage, refreshResidency, sharedRefetch, t]);

  useEffect(() => {
    if (isShared) {
      if (sharedReloadToken.current !== reloadToken) {
        sharedReloadToken.current = reloadToken;
        void reload();
        return;
      }
      refreshResidency();
      return;
    }
    void reload();
    // reloadToken: an external bump (e.g. the ASR config panel just saved a
    // server URL) refetches so availability + "Use" reflect the new config.
  }, [reload, reloadToken, refreshResidency, isShared]);

  // Unload a resident engine's model/sidecar by its /model/loaded id. Safe by
  // contract: the model reloads lazily on the next generation.
  const unloadEngine = useCallback(
    async (engineId) => {
      const entry = loadedByEngine[engineId];
      if (!entry || unloadingId) return;
      setUnloadingId(engineId);
      try {
        await apiUnloadModel(entry.id);
      } catch (e) {
        toastErrorWithReport(t('engines.unloadFailed', { message: e?.message || String(e) }), e);
      } finally {
        setUnloadingId(null);
        refreshResidency();
      }
    },
    [apiUnloadModel, loadedByEngine, refreshResidency, t, unloadingId],
  );

  const familyData = data?.[activeFamily];
  // Available engines first, unavailable after — the list is something you
  // pick FROM. Within each group the backend's registration order is kept.
  const backends = useMemo(() => {
    const rows = (familyData?.backends || []).map(normalizeEntry);
    return rows
      .map((row, index) => ({ row, index }))
      .sort((a, b) => Number(b.row.available) - Number(a.row.available) || a.index - b.index)
      .map(({ row }) => row);
  }, [familyData]);
  const families = useMemo(() => FAMILIES.filter((f) => data?.[f]?.backends), [data]);

  const testHealth = useCallback(
    async (id) => {
      const now = Date.now();
      const cur = healthByEngine[id];
      if (cur?.inflight) return;
      if (cur?.lastClickAt && now - cur.lastClickAt < TEST_COOLDOWN_MS) {
        return; // click-storm cooldown — silently ignore
      }
      setHealthByEngine((prev) => ({ ...prev, [id]: { inflight: true, lastClickAt: now } }));
      try {
        const result = await apiGetEngineHealth(id);
        setHealthByEngine((prev) => ({
          ...prev,
          [id]: {
            inflight: false,
            ok: !!result.ok,
            message: result.message || '',
            latency_ms: Math.round(result.latency_ms || 0),
            lastClickAt: now,
          },
        }));
      } catch (e) {
        setHealthByEngine((prev) => ({
          ...prev,
          [id]: {
            inflight: false,
            ok: false,
            message: e?.message || String(e),
            latency_ms: 0,
            lastClickAt: now,
          },
        }));
      }
    },
    [apiGetEngineHealth, healthByEngine],
  );

  const runSelfTest = useCallback(
    async (id) => {
      const now = Date.now();
      const cur = selfTestByEngine[id];
      if (cur?.inflight) return;
      if (cur?.lastClickAt && now - cur.lastClickAt < TEST_COOLDOWN_MS) {
        return; // cooldown; the backend also serialises self-tests
      }
      setSelfTestByEngine((prev) => ({ ...prev, [id]: { inflight: true, lastClickAt: now } }));
      try {
        const result = await apiSelfTestEngine(id);
        setSelfTestByEngine((prev) => ({
          ...prev,
          [id]: { inflight: false, lastClickAt: now, ...result },
        }));
      } catch (e) {
        setSelfTestByEngine((prev) => ({
          ...prev,
          [id]: { inflight: false, ok: false, message: e?.message || String(e), lastClickAt: now },
        }));
      }
    },
    [apiSelfTestEngine, selfTestByEngine],
  );

  // One-click sidecar install: POST starts a resumable background job; this
  // map holds the latest polled status per engine id.
  const [installByEngine, setInstallByEngine] = useState({});
  // At most ONE in-flight status request per engine — otherwise a slow
  // backend lets responses land out of order. Maps id → { promise }.
  const installInflightRef = useRef(new Map());
  // Monotonic per-engine request epoch: a response may only be applied if no
  // NEWER request has started since it was issued.
  const installReqEpochRef = useRef({});
  // Consecutive poll failures per engine — after a few in a row the backend
  // is gone, so drop the stale snapshot instead of polling forever.
  const installPollFailuresRef = useRef({});

  const refreshInstall = useCallback(
    async (id, { force = false } = {}) => {
      // Advisory callers (the poller, the mount re-attach probe) drop on
      // overlap. The Install click's refresh must NOT be droppable: it waits
      // until it owns the per-engine slot, re-checking after every await, with
      // a bounded wait so a wedged probe cannot stall the click; the epoch
      // check below makes the wedged request's late response harmless.
      let inflight = installInflightRef.current.get(id);
      while (inflight) {
        if (!force) return null;
        let waitTimer;
        const timedOut = await Promise.race([
          inflight.promise.then(
            () => false,
            () => false,
          ),
          new Promise((resolve) => {
            waitTimer = setTimeout(() => resolve(true), FORCE_WAIT_TIMEOUT_MS);
          }),
        ]);
        clearTimeout(waitTimer);
        if (timedOut) break;
        inflight = installInflightRef.current.get(id);
      }
      const epoch = (installReqEpochRef.current[id] = (installReqEpochRef.current[id] || 0) + 1);
      const entry = { promise: null };
      entry.promise = (async () => {
        const st = await apiInstallStatus(id);
        installPollFailuresRef.current[id] = 0;
        if (installReqEpochRef.current[id] === epoch) {
          setInstallByEngine((prev) => ({ ...prev, [id]: st }));
        }
        return st;
      })();
      installInflightRef.current.set(id, entry);
      try {
        return await entry.promise;
      } catch {
        const n = (installPollFailuresRef.current[id] || 0) + 1;
        installPollFailuresRef.current[id] = n;
        if (n >= 4 && installReqEpochRef.current[id] === epoch) {
          installPollFailuresRef.current[id] = 0;
          setInstallByEngine((prev) => {
            const { [id]: _stale, ...rest } = prev;
            return rest;
          });
        }
        return null; // advisory — polling errors never break the list
      } finally {
        if (installInflightRef.current.get(id) === entry) {
          installInflightRef.current.delete(id);
        }
      }
    },
    [apiInstallStatus],
  );

  const startInstall = useCallback(
    async (id) => {
      try {
        const res = await apiInstallEngine(id);
        if (res.status === 'already_installed') {
          reload();
          return;
        }
        // force: this snapshot must never be dropped by the overlap guard —
        // it's what makes the progress panel appear at all.
        const st = await refreshInstall(id, { force: true });
        if (st?.job?.state === 'succeeded') reload();
      } catch (e) {
        toastErrorWithReport(t('engines.installFailed', { message: e?.message || String(e) }), e);
      }
    },
    [apiInstallEngine, refreshInstall, reload, t],
  );

  // Poll running install jobs every 1.5 s; keyed on the SET of running ids so
  // every poll's map replacement does not tear down the interval.
  const runningInstallKey = Object.entries(installByEngine)
    .filter(([, st]) => st?.job?.state === 'running')
    .map(([id]) => id)
    .sort()
    .join(',');
  useEffect(() => {
    if (!runningInstallKey) return undefined;
    const ids = runningInstallKey.split(',');
    const iv = setInterval(async () => {
      for (const id of ids) {
        const st = await refreshInstall(id);
        if (st?.job?.state === 'succeeded') reload();
      }
    }, 1500);
    return () => clearInterval(iv);
  }, [runningInstallKey, refreshInstall, reload]);

  // Re-attach to an in-flight install after a remount: one cheap status
  // probe per installable-but-unavailable row restores progress + poller.
  useEffect(() => {
    for (const b of data?.tts?.backends || []) {
      if (b.one_click_install === true && !b.available) refreshInstall(b.id);
    }
  }, [data, refreshInstall]);

  const copySetup = useCallback(async (id, snippet) => {
    const ok = await copyText(snippet);
    if (!ok) return;
    setCopiedId(id);
    setTimeout(() => setCopiedId((c) => (c === id ? null : c)), 1500);
  }, []);

  // Lazily measure disk usage for one engine (TTS rows carrying an estimate).
  // Ignores a measurement that lands after the inventory reloaded.
  const loadDiskUsage = useCallback(
    async (id) => {
      const generation = diskGenerationRef.current;
      try {
        const usage = await apiGetDiskUsage(id);
        if (diskGenerationRef.current === generation) {
          setDiskByEngine((current) => ({ ...current, [id]: usage }));
        }
      } catch {
        // Estimates remain useful when measurement is unavailable.
      }
    },
    [apiGetDiskUsage],
  );

  // #981 — mlx-audio's curated-model picker: same onSelect as "Use", with
  // the curated model key as the third arg, then reload for active_model_id.
  const changeModel = useCallback(
    async (id, modelId) => {
      if (!onSelect || !modelId) return;
      await onSelect(activeFamily, id, modelId);
      reload();
    },
    [onSelect, activeFamily, reload],
  );

  const selectEngine = useCallback(
    async (id) => {
      if (!onSelect) return;
      await onSelect(activeFamily, id);
      reload();
    },
    [onSelect, activeFamily, reload],
  );

  return {
    data,
    loading,
    error,
    activeFamily,
    setActiveFamily,
    families,
    familyData,
    backends,
    reload,
    healthByEngine,
    testHealth,
    selfTestByEngine,
    runSelfTest,
    installByEngine,
    startInstall,
    loadedByEngine,
    unloadingId,
    unloadEngine,
    diskByEngine,
    loadDiskUsage,
    copiedId,
    copySetup,
    changeModel,
    selectEngine,
    licenseDialogFor,
    setLicenseDialogFor,
  };
}
