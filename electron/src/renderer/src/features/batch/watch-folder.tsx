import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FolderOpenIcon, PauseIcon, PlayIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { getBridge } from '@/components/bridge';
import { PipelineFailure } from '@/components/pipeline-failure';
import { describeError } from '@/lib/api/client';
import {
  createIngestTracker,
  WATCH_POLL_MS,
} from '@shared/utils/watchFolderTracker';
import type { WatchSelection } from '../../../../preload/index.d';

export function WatchFolder({
  langs,
  voiceId,
  preserveBg,
  disabled = false,
  onAdded,
}: {
  langs: string[];
  voiceId: string;
  preserveBg: boolean;
  disabled?: boolean;
  onAdded(): void;
}) {
  const { t } = useTranslation();
  const bridge = getBridge();
  const [source, setSource] = useState<WatchSelection | null>(null);
  const [starting, setStarting] = useState(false);
  const [paused, setPaused] = useState(false);
  const [added, setAdded] = useState(0);
  const [error, setError] = useState('');
  const current = useRef<WatchSelection | null>(null);
  const mounted = useRef(false);
  const startingRef = useRef(false);
  const pausedRef = useRef(false);
  const tracker = useRef(createIngestTracker());
  const settings = useRef({ langs, voiceId, preserveBg, disabled, onAdded });
  settings.current = { langs, voiceId, preserveBg, disabled, onAdded };
  const stop = () => {
    const previous = current.current;
    current.current = null;
    if (previous) void bridge?.watch.stop(previous.token).catch(() => {});
    setSource(null);
    setPaused(false);
    pausedRef.current = false;
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      const previous = current.current;
      current.current = null;
      if (previous) void bridge?.watch.stop(previous.token).catch(() => {});
    };
  }, [bridge]);
  const start = async () => {
    if (!bridge?.watch || startingRef.current) return;
    startingRef.current = true;
    setStarting(true);
    setError('');
    let selected: WatchSelection | null = null;
    try {
      selected = await bridge.watch.pick();
      if (!selected) return;
      const next = createIngestTracker();
      next.prime(await bridge.watch.scan(selected.token));
      if (!mounted.current) {
        await bridge.watch.stop(selected.token);
        return;
      }
      tracker.current = next;
      current.current = selected;
      setSource(selected);
      setAdded(0);
      setPaused(false);
      pausedRef.current = false;
    } catch (error) {
      if (selected) void bridge.watch.stop(selected.token).catch(() => {});
      if (mounted.current) setError(`${t('batch.watch_start_failed')} ${describeError(error)}`);
    } finally {
      startingRef.current = false;
      if (mounted.current) setStarting(false);
    }
  };
  useEffect(() => {
    if (!source || !bridge?.watch) return;
    let ticking = false;
    const live = () => mounted.current && current.current === source;
    const tick = async () => {
      if (
        ticking ||
        !live() ||
        pausedRef.current ||
        settings.current.disabled ||
        !settings.current.langs.length
      )
        return;
      ticking = true;
      const ingest = tracker.current;
      try {
        const entries = ingest.next(await bridge.watch.scan(source.token));
        for (const entry of entries) {
          if (!live() || pausedRef.current || !settings.current.langs.length) {
            ingest.unsee(entry);
            continue;
          }
          try {
            const options = settings.current;
            await bridge.watch.enqueue({
              token: source.token,
              entry,
              langs: options.langs,
              voiceId: options.voiceId,
              preserveBg: options.preserveBg,
            });
            if (live()) {
              setAdded((count) => count + 1);
              setError('');
              options.onAdded();
            }
          } catch (error) {
            ingest.retry(entry);
            if (live()) setError(describeError(error));
          }
        }
      } catch (error) {
        if (live()) {
          current.current = null;
          setSource(null);
          setError(`${t('batch.watch_failed')} ${describeError(error)}`);
          void bridge.watch.stop(source.token).catch(() => {});
        }
      } finally {
        ticking = false;
      }
    };
    const timer = setInterval(() => void tick(), WATCH_POLL_MS);
    return () => clearInterval(timer);
  }, [source, bridge]);
  if (!bridge?.watch) return null;
  const label = source?.path.split(/[\\/]/).filter(Boolean).at(-1) || '';
  return (
    <div className="space-y-2 border-t border-border/50 pt-4">
      {source ? (
        <>
          <div className="flex items-center gap-2 text-xs" role="status">
            <FolderOpenIcon className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate" title={source.path}>
              {t(paused || disabled || !langs.length ? 'batch.watch_paused' : 'batch.watching', {
                folder: label,
              })}
            </span>
          </div>
          {!paused && !disabled && langs.length > 0 && (
            <p className="text-xs leading-5 text-muted-foreground">
              {t('batch.watch_started', { folder: label })}
            </p>
          )}
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs tabular-nums text-muted-foreground">
              {t('batch.watch_added', { count: added })}
            </span>
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={t(paused ? 'batch.watch_resume' : 'batch.watch_pause')}
                onClick={() => {
                  pausedRef.current = !pausedRef.current;
                  setPaused(pausedRef.current);
                }}
              >
                {paused ? <PlayIcon /> : <PauseIcon />}
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={t('batch.watch_stop')}
                onClick={stop}
              >
                <XIcon />
              </Button>
            </div>
          </div>
        </>
      ) : (
        <Button
          variant="ghost"
          className="w-full justify-start"
          disabled={starting || disabled || !langs.length}
          onClick={() => void start()}
        >
          <FolderOpenIcon />
          {t(starting ? 'common.loading' : 'batch.watch_folder')}
        </Button>
      )}
      {error && (
        <PipelineFailure fallback={error} onDismiss={() => setError('')} className="text-xs" />
      )}
    </div>
  );
}
