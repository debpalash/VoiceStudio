import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { DownloadIcon, LayersIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiFetch, describeError } from '@/lib/api/client';
import { renderStoryStems } from './story-preview';
import { storyVoicesReady } from './story-inputs';
import { saveLocalFile } from '@/lib/local-export';
import type { Draft } from './longform-session';

export function StoryStems({
  draft,
  profiles,
  disabled,
  onBusy,
}: {
  draft: Draft;
  profiles: { id: string }[];
  disabled: boolean;
  onBusy?: (busy: boolean) => void;
}) {
  const { t } = useTranslation();
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [error, setError] = useState<string | null>(null);
  const [stems, setStems] = useState<{ name: string; url: string; filename: string; blob: Blob }[]>(
    [],
  );
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => () => stems.forEach((stem) => URL.revokeObjectURL(stem.url)), [stems]);
  useEffect(() => {
    setStems([]);
  }, [
    draft.lines,
    draft.cast,
    draft.voice,
    draft.language,
    draft.voiceCast,
    draft.overrides,
    draft.globalSpeed,
    draft.format,
  ]);
  const render = async () => {
    if (disabled || controller.current) return;
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    onBusy?.(true);
    setError(null);
    setProgress({ done: 0, total: 0 });
    setStems([]);
    try {
      const result = await renderStoryStems(draft, current.signal, profiles, (done, total) => {
        if (!current.signal.aborted) setProgress({ done, total });
      });
      if (!current.signal.aborted) {
        let usedFallback = false;
        const outputs = await Promise.all(
          result.map(async (stem, index) => {
            const name =
              draft.cast.find((character) => character.id === stem.character)?.name ||
              (stem.character === 'narrator' ? t('audiobook.meta_narrator') : stem.character);
            let blob = stem.blob;
            let extension = 'wav';
            if (draft.format === 'mp3') {
              const body = new FormData();
              body.set('file', stem.blob, 'story.wav');
              body.set('format', 'mp3');
              body.set('bitrate', '192k');
              try {
                blob = await (
                  await apiFetch('/stories/encode', {
                    method: 'POST',
                    body,
                    signal: current.signal,
                  })
                ).blob();
                extension = 'mp3';
              } catch (cause) {
                if (current.signal.aborted) throw cause;
                usedFallback = true;
              }
            }
            return {
              name,
              blob,
              // Strip control characters forbidden in native filenames.
              // eslint-disable-next-line no-control-regex
              filename: `story-${index + 1}-${name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 80)}.${extension}`,
            };
          }),
        );
        if (!current.signal.aborted) {
          setStems(outputs.map((stem) => ({ ...stem, url: URL.createObjectURL(stem.blob) })));
          if (usedFallback) toast.warning(t('stories.mp3Fallback'));
        }
      }
    } catch (cause) {
      if (!current.signal.aborted) setError(describeError(cause));
    } finally {
      if (controller.current === current) {
        controller.current = null;
        setBusy(false);
        onBusy?.(false);
      }
    }
  };
  const saveStem = async (blob: Blob, filename: string) => {
    setError(null);
    try {
      await saveLocalFile(blob, filename);
    } catch (cause) {
      setError(describeError(cause));
    }
  };
  return (
    <details className="space-y-3 border-t border-border/40 pt-3">
      <summary className="cursor-pointer text-sm font-medium">{t('stories.stems')}</summary>
      <div className="flex items-center gap-3">
        <Button
          size="sm"
          variant="outline"
          disabled={disabled || !storyVoicesReady(draft, profiles)}
          onClick={() => void render()}
        >
          <LayersIcon />
          {t('stories.stems')}
        </Button>
        {busy && (
          <>
            <span role="status" className="text-xs text-muted-foreground">
              {progress.total ? `${progress.done} / ${progress.total}` : t('common.loading')}
            </span>
            <Button size="sm" variant="ghost" onClick={() => controller.current?.abort()}>
              {t('common.stop')}
            </Button>
          </>
        )}
      </div>
      {error && <PipelineFailure fallback={error} onDismiss={() => setError(null)} />}
      {stems.length > 0 && (
        <ul className="space-y-1">
          {stems.map((stem) => (
            <li key={stem.filename}>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void saveStem(stem.blob, stem.filename)}
              >
                <DownloadIcon />
                {stem.name}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
