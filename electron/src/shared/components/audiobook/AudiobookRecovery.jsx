import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArchiveRestore, FolderSearch, RotateCcw } from 'lucide-react';
import toast from 'react-hot-toast';
import { audiobookListJobs } from '../../api/audiobook';
import { exportReveal } from '../../api/exports';
import { useSystemInfo } from '../../api/hooks';
import { Button } from '../../ui';

const newestFirst = (current, item) =>
  !current || (item.created_at ?? 0) > (current.created_at ?? 0) ? item : current;

export default function AudiobookRecovery({ t, generating, onResume }) {
  const [inventory, setInventory] = useState([]);
  const [suppressedId, setSuppressedId] = useState('');
  const [loadFailed, setLoadFailed] = useState(false);
  const generation = useRef(0);
  const alive = useRef(false);
  const { data: machine } = useSystemInfo();

  const acceptInventory = useCallback(async () => {
    const ticket = ++generation.current;
    try {
      const payload = await audiobookListJobs();
      if (alive.current && ticket === generation.current) {
        setInventory(payload.jobs ?? []);
        setLoadFailed(false);
      }
    } catch {
      // Without the inventory nothing would refetch until the view remounts.
      if (alive.current && ticket === generation.current) setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void acceptInventory();
    return () => {
      alive.current = false;
      ++generation.current;
    };
  }, [acceptInventory]);

  const recoverable = useMemo(
    () =>
      inventory
        .filter(({ type, job_id: id }) => type === 'audiobook' && id !== suppressedId)
        .reduce(newestFirst, null),
    [inventory, suppressedId],
  );

  const locations = useMemo(() => {
    const base = String(machine?.outputs_dir ?? '').replace(/[\\/]+$/, '');
    if (!base || !recoverable) return { cache: '', manifest: '' };
    return {
      cache: `${base}/longform_cache`,
      manifest: `${base}/audiobook_${recoverable.job_id}/resume.json`,
    };
  }, [machine?.outputs_dir, recoverable]);

  const revealChapters = useCallback(async () => {
    try {
      await exportReveal({ path: locations.cache });
    } catch (problem) {
      toast.error(problem?.message || t('audiobook.open_cache_failed'));
    }
  }, [locations.cache, t]);

  const continueRender = useCallback(async () => {
    if (!recoverable) return;
    const id = recoverable.job_id;
    setSuppressedId(id);
    try {
      await onResume(id);
      const ticket = ++generation.current;
      const next = await audiobookListJobs();
      if (!alive.current || ticket !== generation.current) return;
      setInventory(next.jobs ?? []);
      setSuppressedId('');
    } catch {
      if (alive.current) setSuppressedId('');
    }
  }, [onResume, recoverable]);

  if (!recoverable) {
    if (!loadFailed) return null;
    return (
      <div
        role="status"
        className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--chrome-radius-card)] border border-[var(--chrome-border)] bg-[var(--chrome-bg)] px-4 py-3 text-xs text-[var(--chrome-fg-muted)]"
      >
        <span>{t('audiobook.recovery_load_failed')}</span>
        <Button
          size="sm"
          variant="ghost"
          leading={<RotateCcw size={14} />}
          onClick={() => void acceptInventory()}
        >
          {t('audiobook.recovery_retry')}
        </Button>
      </div>
    );
  }

  return (
    <section className="rounded-[var(--chrome-radius-card)] border border-[var(--chrome-border)] bg-[var(--chrome-bg)] p-4">
      <header className="space-y-1">
        <h2 className="text-sm font-semibold text-[var(--chrome-fg)]">
          {t('audiobook.recovery_title')}
        </h2>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <p className="min-w-0 truncate text-sm text-[var(--chrome-fg)]">
            {recoverable.title || t('audiobook.untitled')}
          </p>
          <p className="text-xs text-[var(--chrome-fg-muted)]">
            {t('audiobook.recovery_progress', {
              done: recoverable.chapters_done ?? 0,
              total: recoverable.total_chapters ?? 0,
            })}
          </p>
        </div>
      </header>
      <p className="mt-2 text-xs text-[var(--chrome-fg-dim)]">{t('audiobook.recovery_hint')}</p>
      <p className="mt-2 break-all text-xs text-[var(--chrome-fg-dim)]">
        {t('audiobook.cache_location_hint', {
          cachePath: locations.cache,
          manifestPath: locations.manifest,
        })}
      </p>
      <footer className="mt-3 flex flex-wrap justify-end gap-2">
        <Button
          size="sm"
          variant="ghost"
          leading={<FolderSearch size={14} />}
          disabled={!locations.cache}
          onClick={revealChapters}
        >
          {t('audiobook.open_chapter_cache')}
        </Button>
        <Button
          size="sm"
          leading={<ArchiveRestore size={14} />}
          disabled={generating}
          onClick={continueRender}
        >
          {t('audiobook.resume')}
        </Button>
      </footer>
    </section>
  );
}
