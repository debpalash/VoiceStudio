import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { CopyIcon, MicIcon, PauseIcon, PlayIcon, SquareIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useBackendStatus } from '@/hooks/use-backend-status';
import { LiveDictation } from './live-dictation';
import { addTranscription } from '@shared/utils/transcriptionsStore';

/** Native recorder stays separate from page navigation and the script editor. */
export function CaptureWidget() {
  const { t } = useTranslation();
  const live = useMemo(() => new LiveDictation(), []);
  const state = useSyncExternalStore(live.subscribe, live.getSnapshot);
  const backend = useBackendStatus();
  const [delivery, setDelivery] = useState<'copied' | 'failed' | null>(null);
  const session = useRef<number | null>(null);
  const stopRequested = useRef(false);
  const output = useRef<Promise<unknown>>(Promise.resolve());
  const finishTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const api = window.voicestudio?.capture;

  useEffect(() => {
    if (!api || backend.stage !== 'ready' || !backend.baseUrl) return;
    const unsubscribe = api.onEvent((event) => {
      if (event.action === 'cancel') {
        if (session.current !== event.session) return;
        session.current = null;
        live.cancel();
        return;
      }
      if (event.action === 'stop') {
        if (session.current !== event.session) return;
        stopRequested.current = true;
        void live.stop().catch(() => setDelivery('failed'));
        return;
      }
      if (session.current !== null) return;
      session.current = event.session;
      stopRequested.current = false;
      setDelivery(null);
      output.current = Promise.resolve();
      let sequence = 0;
      void api
        .accept(event.session)
        .then(() => {
          if (session.current !== event.session) return;
          return live.start((entry) => {
            addTranscription(entry);
            const text = entry.refined_text || entry.text || '';
            const index = sequence++;
            output.current = output.current
              .then(async () => {
                if (session.current !== event.session) return;
                const outcome = await api.deliver(event.session, index, (index ? ' ' : '') + text);
                if (session.current === event.session && outcome === 'copied')
                  setDelivery('copied');
              })
              .catch(() => {
                if (session.current === event.session) setDelivery('failed');
                throw new Error('Output failed');
              });
            // A later result or finish observes the error; suppress unhandled rejection now.
            void output.current.catch(() => {});
          });
        })
        .then(() => {
          if (stopRequested.current && session.current === event.session)
            void live.stop().catch(() => setDelivery('failed'));
        })
        .catch(() => {
          if (session.current === event.session) setDelivery('failed');
        });
    });
    void api.ready().catch(() => setDelivery('failed'));
    return () => {
      unsubscribe();
      const id = session.current;
      session.current = null;
      live.cancel();
      if (id !== null) void api.cancel(id).catch(() => {});
      if (finishTimer.current) clearTimeout(finishTimer.current);
    };
  }, [api, backend.stage, backend.baseUrl, live]);

  useEffect(() => {
    const id = session.current;
    const phase = delivery === 'failed' ? 'error' : state.stage;
    if (id !== null && phase !== 'idle') void api?.phase(id, phase).catch(() => {});
  }, [api, state.stage, delivery]);

  useEffect(() => {
    const id = session.current;
    if (!api || id === null || state.stage !== 'done' || !state.text) return;
    let active = true;
    void output.current
      .then(() => {
        if (!active || session.current !== id) return;
        // Leave clipboard-only completion visible instead of claiming insertion.
        finishTimer.current = setTimeout(
          () => {
            if (session.current !== id) return;
            void api
              .finish(id)
              .then(() => {
                if (session.current === id) {
                  session.current = null;
                  live.cancel();
                }
              })
              .catch(() => {
                if (session.current === id) setDelivery('failed');
              });
          },
          delivery === 'copied' ? 4000 : 700,
        );
      })
      .catch(() => {
        if (active && session.current === id) setDelivery('failed');
      });
    return () => {
      active = false;
      if (finishTimer.current) clearTimeout(finishTimer.current);
    };
  }, [api, state.stage, state.text, delivery, live]);

  const cancel = () => {
    const id = session.current;
    session.current = null;
    live.cancel();
    void api?.cancel(id).catch(() => {});
  };
  const label =
    delivery === 'failed'
      ? t('common.error')
      : delivery === 'copied'
        ? t('transcriptions.copied')
        : state.issue === 'model'
          ? t('transcriptions.missing_model')
          : state.stage === 'error'
            ? t('transcriptions.capture_failed')
            : state.modelStage === 'loading'
              ? t('capture.model_loading')
              : state.stage === 'transcribing'
                ? t('capture.transcribing_label')
                : state.stage === 'done'
                  ? t(state.text ? 'engineSidebar.dictation' : 'capture.no_speech')
                  : state.stage === 'starting' || state.stage === 'idle'
                    ? t('common.loading')
                    : state.paused
                      ? t('common.paused')
                      : t('capture.listening_label');
  return (
    <main
      className="flex h-screen flex-col gap-3 rounded-xl border border-border bg-background p-4 text-foreground"
      onKeyDown={(event) => {
        if (event.key === 'Escape') cancel();
      }}
    >
      <div className="app-drag flex items-center gap-2">
        <MicIcon className="size-4 shrink-0 text-primary" />
        <span role="status" className="min-w-0 flex-1 text-sm font-medium">
          {label}
        </span>
        <Button
          className="app-no-drag"
          variant="ghost"
          size="icon-sm"
          aria-label={t('common.cancel')}
          onClick={cancel}
        >
          <XIcon />
        </Button>
      </div>
      <p className="min-h-0 flex-1 overflow-auto text-sm leading-relaxed text-muted-foreground">
        {state.text}
      </p>
      <div className="flex items-center justify-end gap-2">
        {state.text && (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={t('transcriptions.copy')}
            onClick={() =>
              void navigator.clipboard
                .writeText(state.text)
                .then(() => setDelivery('copied'))
                .catch(() => setDelivery('failed'))
            }
          >
            <CopyIcon />
          </Button>
        )}
        {state.stage === 'recording' && (
          <>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t(state.paused ? 'common.resume' : 'common.pause')}
              onClick={() => live.pause()}
            >
              {state.paused ? <PlayIcon /> : <PauseIcon />}
            </Button>
            <Button size="sm" onClick={() => void live.stop().catch(() => setDelivery('failed'))}>
              <SquareIcon />
              {t('common.stop')}
            </Button>
          </>
        )}
      </div>
    </main>
  );
}
