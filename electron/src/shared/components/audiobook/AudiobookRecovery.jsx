import React, { useEffect, useMemo, useRef, useState } from 'react';
import { FolderOpen, RotateCcw } from 'lucide-react';
import toast from 'react-hot-toast';

import { audiobookListJobs } from '../../api/audiobook';
import { exportReveal } from '../../api/exports';
import { useSystemInfo } from '../../api/hooks';
import { Button } from '../../ui';

/** Local recovery affordance for interrupted audiobook renders (not a global job manager). */
export default function AudiobookRecovery({ t, generating, onResume }) {
  const [jobs, setJobs] = useState([]);
  const [hiddenJobId, setHiddenJobId] = useState(null);
  const refreshIdRef = useRef(0);
  const { data: systemInfo } = useSystemInfo();

  useEffect(() => {
    const refreshId = ++refreshIdRef.current;
    audiobookListJobs()
      .then((result) => {
        if (refreshId === refreshIdRef.current) {
          setJobs(Array.isArray(result?.jobs) ? result.jobs : []);
        }
      })
      .catch(() => {});
    return () => {
      // Invalidate any in-flight response so an unmounted recovery card is
      // never repopulated by a late inventory request.
      refreshIdRef.current += 1;
    };
  }, []);

  const job = useMemo(
    () =>
      jobs
        .filter((candidate) => candidate.type === 'audiobook' && candidate.job_id !== hiddenJobId)
        .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0],
    [hiddenJobId, jobs],
  );
  const cachePath = systemInfo?.outputs_dir
    ? `${systemInfo.outputs_dir.replace(/[\\/]+$/, '')}/longform_cache`
    : '';
  const manifestPath = systemInfo?.outputs_dir
    ? `${systemInfo.outputs_dir.replace(/[\\/]+$/, '')}/audiobook_${job?.job_id}/resume.json`
    : '';

  if (!job) return null;

  const resume = async () => {
    const jobId = job.job_id;
    setHiddenJobId(jobId);
    await onResume(jobId);
    // A resumed render runs under a fresh backend id. If it is stopped or
    // fails, that fresh manifest becomes the next recovery card; if it
    // completes, the inventory is empty. Refresh in every terminal case.
    const refreshId = ++refreshIdRef.current;
    try {
      const result = await audiobookListJobs();
      if (refreshId === refreshIdRef.current) {
        setJobs(Array.isArray(result?.jobs) ? result.jobs : []);
        setHiddenJobId(null);
      }
    } catch {
      setHiddenJobId(null);
    }
  };
  const openCache = async () => {
    if (!cachePath) return;
    try {
      await exportReveal({ path: cachePath });
    } catch (error) {
      toast.error(error?.message || t('audiobook.open_cache_failed'));
    }
  };

  return (
    <section className="flex flex-wrap items-center justify-between gap-[10px] rounded-[12px] border border-[color-mix(in_srgb,var(--chrome-accent)_25%,transparent)] bg-[var(--color-bg-elev-2)] px-[12px] py-[9px]">
      <div className="min-w-0">
        <h3 className="m-0 text-[var(--text-sm)] font-semibold text-fg">
          {t('audiobook.recovery_title')}
        </h3>
        <p className="m-[3px_0_0] text-[var(--text-sm)] text-fg-muted">
          <strong className="text-fg">{job.title || t('audiobook.untitled')}</strong>
          {' · '}
          {t('audiobook.recovery_progress', {
            done: job.chapters_done || 0,
            total: job.total_chapters || 0,
          })}
        </p>
        <p className="m-[3px_0_0] text-[var(--text-xs)] text-fg-dim">
          {t('audiobook.recovery_hint')}
        </p>
        <p className="m-[3px_0_0] text-[var(--text-xs)] text-fg-dim">
          {t('audiobook.cache_location_hint', { cachePath, manifestPath })}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-[4px]">
        <Button
          variant="subtle"
          size="sm"
          onClick={openCache}
          disabled={!cachePath}
          leading={<FolderOpen />}
        >
          {t('audiobook.open_chapter_cache')}
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={() => void resume()}
          disabled={generating}
          leading={<RotateCcw />}
        >
          {t('audiobook.resume')}
        </Button>
      </div>
    </section>
  );
}
