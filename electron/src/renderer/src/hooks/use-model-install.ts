import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { apiJson, describeError } from '@/lib/api/client';
import { useModelInstallJobs, type ModelInstallJobsResponse } from '@/hooks/use-model-install-sync';
import {
  ACTIVE_INSTALL_STAGES,
  describeInstall,
  terminalInstallView,
  type InstallJobLike,
  type InstallView,
} from '@/lib/model-install-state';

interface CatalogueEntry {
  repo_id: string;
  installed?: boolean;
  incomplete?: boolean;
}

/** False only when the catalogue lists the model as not (fully) installed. */
async function confirmInstalled(repo: string): Promise<boolean> {
  try {
    const catalogue = await apiJson<{ models?: CatalogueEntry[] }>('/models');
    const entry = Array.isArray(catalogue?.models)
      ? catalogue.models.find((model) => model.repo_id === repo)
      : undefined;
    return !entry || (Boolean(entry.installed) && !entry.incomplete);
  } catch {
    return true;
  }
}

export interface ModelInstallCallbacks {
  /** Fires once when a download this hook watched finishes. */
  onReady?: (repoId: string) => void;
  /** Fires once when it fails (never for a user cancel). */
  onFailed?: (view: InstallView, repoId: string) => void;
}

/**
 * Start, follow, cancel and retry one model download outside Settings ->
 * Models. The backend forgets a job once it stops, so the hook remembers how
 * the download it watched ended (ready, cancelled or failed) and keeps that
 * visible until the next start or a dismiss.
 *
 * `repoId` may be unknown until the user acts (a prompt that resolves the
 * recommended model on click); `start(target)` then names it.
 */
export function useModelInstall(repoId?: string, callbacks: ModelInstallCallbacks = {}) {
  const client = useQueryClient();
  const jobs = useModelInstallJobs<InstallJobLike>();
  const [chosen, setChosen] = useState<string | undefined>(undefined);
  const [watching, setWatching] = useState(false);
  const [outcome, setOutcome] = useState<InstallView | null>(null);
  const [busy, setBusy] = useState(false);
  // The backend registers a job before answering the install request, so a
  // job missing from a job list fetched after that answer has already
  // finished (a cached model) rather than not started yet.
  const [goneAfterPost, setGoneAfterPost] = useState(false);
  const seen = useRef(false);
  const cancelRequested = useRef(false);
  // The cooldown failure a new start replaced; it must not end that attempt.
  const [staleFailure, setStaleFailure] = useState<number | null>(null);
  const latest = useRef(callbacks);
  latest.current = callbacks;

  const repo = chosen ?? repoId;
  // Tolerate a malformed status body (an older backend, a proxy error page).
  const list = Array.isArray(jobs.data?.jobs) ? jobs.data.jobs : [];
  const job = repo ? list.find((item) => item.repo_id === repo) : undefined;
  const live = job ? describeInstall(job) : null;
  const active = Boolean(live && ACTIVE_INSTALL_STAGES.has(live.stage));
  const stale = watching && live?.stage === 'failed' && (job?.failed_at ?? 0) === staleFailure;
  const liveStage = live?.stage;
  const failedAt = job?.failed_at;

  // A download already running for this model (started in Settings, the
  // bell or another prompt) is followed like one started here, so its
  // completion still reaches `onReady`.
  useEffect(() => {
    if (watching || !active || !repo) return;
    seen.current = true;
    setOutcome(null);
    setWatching(true);
  }, [active, repo, watching]);

  useEffect(() => {
    if (!watching || !jobs.data || !repo) return;
    if (active) {
      seen.current = true;
      // Cancelled from anywhere (this prompt, the bell, Settings).
      if (live?.stage === 'cancelling') cancelRequested.current = true;
      return;
    }
    if (stale || (!seen.current && !goneAfterPost && !live)) return;
    const finish = (view: InstallView) => {
      setWatching(false);
      setStaleFailure(null);
      setGoneAfterPost(false);
      seen.current = false;
      cancelRequested.current = false;
      setOutcome(view);
      return view;
    };
    if (live?.stage === 'failed') {
      latest.current.onFailed?.(finish(live), repo);
      return;
    }
    if (cancelRequested.current || live?.stage === 'cancelled') {
      finish(terminalInstallView('cancelled'));
      return;
    }
    finish(terminalInstallView('ready'));
    // The backend drops a job both when it finishes and when it is
    // cancelled between two polls; only the catalogue tells them apart.
    void confirmInstalled(repo).then((installed) => {
      if (installed) latest.current.onReady?.(repo);
      else setOutcome(terminalInstallView('cancelled'));
    });
    // `liveStage`/`failedAt` stand in for `live`, a fresh object every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, failedAt, goneAfterPost, jobs.data, liveStage, repo, stale, watching]);

  const start = useCallback(
    async (target?: string): Promise<'started' | 'joined' | null> => {
      const next = target ?? repo;
      if (!next || busy) return null;
      const existing = list.find((item) => item.repo_id === next);
      const existingView = existing ? describeInstall(existing) : null;
      if (target) setChosen(target);
      setOutcome(null);
      setGoneAfterPost(false);
      seen.current = false;
      cancelRequested.current = false;
      setStaleFailure(existing?.state === 'failed' ? (existing.failed_at ?? 0) : null);
      setWatching(true);
      if (existingView && ACTIVE_INSTALL_STAGES.has(existingView.stage)) {
        // Already downloading (started elsewhere): follow it, don't restart.
        seen.current = true;
        return 'joined';
      }
      setBusy(true);
      try {
        await apiJson('/models/install', {
          method: 'POST',
          body: JSON.stringify({ repo_id: next }),
        });
        const answeredAt = Date.now();
        await client.invalidateQueries({ queryKey: ['model-install-jobs'] });
        const fresh = client.getQueryState<ModelInstallJobsResponse>(['model-install-jobs']);
        if (
          Array.isArray(fresh?.data?.jobs) &&
          fresh.dataUpdatedAt >= answeredAt &&
          !fresh.data.jobs.some((item) => item.repo_id === next)
        )
          setGoneAfterPost(true);
        return 'started';
      } catch (error) {
        setWatching(false);
        setStaleFailure(null);
        const view = terminalInstallView('failed', describeError(error));
        setOutcome(view);
        latest.current.onFailed?.(view, next);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [busy, client, list, repo],
  );

  const cancel = useCallback(async () => {
    if (!repo) return;
    cancelRequested.current = true;
    setBusy(true);
    try {
      await apiJson('/models/install/cancel', {
        method: 'POST',
        body: JSON.stringify({ repo_id: repo }),
      });
    } catch {
      cancelRequested.current = false;
    } finally {
      setBusy(false);
      await client.invalidateQueries({ queryKey: ['model-install-jobs'] }).catch(() => undefined);
    }
  }, [client, repo]);

  const [dismissedFailure, setDismissedFailure] = useState<number | null>(null);
  const dismiss = useCallback(() => {
    setOutcome(null);
    setDismissedFailure(failedAt ?? 0);
  }, [failedAt]);

  // A live job wins; a remembered outcome covers the gap after the backend
  // drops it. A cooldown failure not started here still shows (until
  // dismissed), so Retry is one click away. The failure a new start replaced
  // reads as queued.
  const liveFailureShown =
    live?.stage === 'failed' && (watching || (failedAt ?? 0) !== dismissedFailure);
  const queued = describeInstall({ repo_id: repo ?? '', state: 'downloading' });
  const view: InstallView | null = stale
    ? queued
    : live && (active || liveFailureShown || (watching && live.stage !== 'failed'))
      ? live
      : watching
        ? queued
        : outcome;
  return {
    repoId: repo,
    view,
    active: active || watching,
    busy,
    start,
    retry: () => start(),
    cancel,
    dismiss,
  };
}

export type ModelInstall = ReturnType<typeof useModelInstall>;
