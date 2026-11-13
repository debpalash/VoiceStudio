import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import WaveSurfer from 'wavesurfer.js';
import Regions, { type Region } from 'wavesurfer.js/dist/plugins/regions.esm.js';
import { ArrowLeftIcon, MaximizeIcon, RepeatIcon, ZoomInIcon, ZoomOutIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PipelineFailure } from '@/components/pipeline-failure';
import { WaveformPlayer } from '@/components/waveform-player';
import { apiFetch, describeError } from '@/lib/api/client';
import {
  decodeToMonoLowRate,
  sliceToMono,
  encodeWav,
} from '@shared/utils/audioTrim';

const MAX_TRIM_SECONDS = 60;

export function trimmedAudio(buffer: AudioBuffer, start: number, end: number): Blob {
  if (
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    start < 0 ||
    end > buffer.duration ||
    end - start < 0.02 ||
    end - start > MAX_TRIM_SECONDS + 0.001
  )
    throw new Error('Invalid trim range');
  return new Blob([encodeWav(sliceToMono(buffer, start, end), buffer.sampleRate)], {
    type: 'audio/wav',
  });
}
export function TrimAudio({
  src,
  name,
  busy,
  saveError,
  onSave,
  onCancel,
}: {
  src: string;
  name: string;
  busy: boolean;
  saveError?: string;
  onSave(file: File): void;
  onCancel(): void;
}) {
  const { t } = useTranslation();
  const previewContainer = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const waveform = useRef<WaveSurfer | null>(null);
  const region = useRef<Region | null>(null);
  const buffer = useRef<AudioBuffer | null>(null);
  const [duration, setDuration] = useState(0);
  const [range, setRange] = useState({ start: 0, end: 0 });
  const [preview, setPreview] = useState<{ blob: Blob; url: string } | null>(null);
  const [error, setError] = useState('');
  const [loop, setLoop] = useState(true);
  const zoom = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    void (async () => {
      try {
        const response = await apiFetch(src, { signal: controller.signal });
        const decoded = await decodeToMonoLowRate(await response.blob());
        if (disposed || !container.current) return;
        if (!Number.isFinite(decoded.duration) || decoded.duration < 0.02)
          throw new Error('Audio too short');
        buffer.current = decoded;
        const regions = Regions.create();
        const wave = WaveSurfer.create({
          container: container.current,
          peaks: [decoded.getChannelData(0)],
          duration: decoded.duration,
          height: 130,
          waveColor: getComputedStyle(container.current).color,
          cursorWidth: 0,
          interact: false,
          plugins: [regions],
        });
        waveform.current = wave;
        wave.on('error', (error) => {
          if (!disposed) setError(describeError(error));
        });
        const initial = { start: 0, end: Math.min(MAX_TRIM_SECONDS, decoded.duration) };
        wave.once('ready', () => {
          if (disposed) return;
          region.current = regions.addRegion({
            ...initial,
            minLength: 0.02,
            maxLength: MAX_TRIM_SECONDS,
            drag: true,
            resize: true,
            color: 'color-mix(in srgb, var(--primary) 20%, transparent)',
          });
          regions.on('region-updated', (selected) =>
            setRange({ start: selected.start, end: selected.end }),
          );
          setDuration(decoded.duration);
          setRange(initial);
        });
      } catch (error) {
        if (!disposed) setError(describeError(error));
      }
    })();
    return () => {
      disposed = true;
      controller.abort();
      waveform.current?.destroy();
      waveform.current = null;
      buffer.current = null;
    };
  }, [src]);
  useEffect(() => {
    if (!buffer.current || !duration) return;
    try {
      const blob = trimmedAudio(buffer.current, range.start, range.end);
      const url = URL.createObjectURL(blob);
      setPreview({ blob, url });
      return () => URL.revokeObjectURL(url);
    } catch {
      setPreview(null);
    }
  }, [range, duration]);
  useEffect(() => {
    region.current?.setOptions({ drag: !busy, resize: !busy });
  }, [busy, duration]);
  const change = (key: 'start' | 'end', value: number) => {
    if (!Number.isFinite(value)) return;
    const next =
      key === 'start'
        ? { start: Math.max(0, Math.min(value, range.end - 0.02)), end: range.end }
        : { start: range.start, end: Math.min(duration, Math.max(value, range.start + 0.02)) };
    if (next.end - next.start > MAX_TRIM_SECONDS) {
      if (key === 'start') next.end = next.start + MAX_TRIM_SECONDS;
      else next.start = next.end - MAX_TRIM_SECONDS;
    }
    region.current?.setOptions(next);
    setRange(next);
  };
  const magnify = (factor: number) => {
    const fit = (container.current?.clientWidth || 600) / duration;
    zoom.current = Math.min(2000, Math.max(fit, (zoom.current || fit) * factor));
    waveform.current?.zoom(zoom.current);
  };
  const save = () => {
    if (preview && !busy && !error)
      onSave(new File([preview.blob], name + '.wav', { type: 'audio/wav' }));
  };
  return (
    <section
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !busy) {
          event.preventDefault();
          event.stopPropagation();
          onCancel();
          return;
        }
        if ((event.target as HTMLElement).closest('input,textarea,button')) return;
        if (event.key === ' ' && preview) {
          event.preventDefault();
          event.stopPropagation();
          previewContainer.current?.querySelector('button')?.click();
        }
        if (event.key === 'Enter') {
          event.preventDefault();
          event.stopPropagation();
          save();
        }
      }}
      tabIndex={-1}
      className="min-h-0 flex-1 overflow-y-auto p-6"
    >
      <div className="mx-auto max-w-5xl space-y-5">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={busy}
            aria-label={t('common.cancel')}
            onClick={onCancel}
          >
            <ArrowLeftIcon />
          </Button>
          <div>
            <h2 className="text-sm font-medium">{t('trimmer.title')}</h2>
            <p className="text-xs text-muted-foreground">{name}</p>
          </div>
        </div>
        {saveError && (
          <p role="alert" className="text-sm text-destructive">
            {saveError}
          </p>
        )}
        {error && (
          <PipelineFailure
            fallback={`${t('trimmer.audio_load_failed')} ${error}`}
            onDismiss={() => setError('')}
          />
        )}
        {!duration && !error && (
          <p role="status" className="text-sm text-muted-foreground">
            {t('trimmer.decoding')}
          </p>
        )}
        <div
          ref={container}
          className="overflow-hidden rounded-xl border border-border/50 p-3 text-muted-foreground"
        />
        {duration > 0 && (
          <>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t('trimmer.zoom_in')}
                onClick={() => magnify(2)}
              >
                <ZoomInIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t('trimmer.zoom_out')}
                onClick={() => magnify(0.5)}
              >
                <ZoomOutIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t('trimmer.fit_all')}
                onClick={() => {
                  zoom.current = 0;
                  waveform.current?.zoom(0);
                }}
              >
                <MaximizeIcon />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                aria-label={t('trimmer.fit_selection')}
                onClick={() => {
                  zoom.current =
                    (container.current?.clientWidth || 600) /
                    Math.max(0.02, range.end - range.start);
                  waveform.current?.zoom(zoom.current);
                  waveform.current?.setScrollTime(range.start);
                }}
              >
                {t('trimmer.fit_sel_btn')}
              </Button>
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                {duration.toFixed(2)} {t('trimmer.unit_seconds')}
              </span>
            </div>
            <div className="flex flex-wrap items-end gap-4">
              {(['start', 'end'] as const).map((key) => (
                <label key={key} className="flex flex-col gap-2 text-xs">
                  <span>{t('trimmer.' + key + '_label')}</span>
                  <Input
                    type="number"
                    min={0}
                    max={duration}
                    step="0.01"
                    value={Number(range[key].toFixed(2))}
                    disabled={busy}
                    onChange={(event) => change(key, event.target.valueAsNumber)}
                    className="w-32 tabular-nums"
                  />
                </label>
              ))}
              <span className="pb-2 text-xs tabular-nums text-muted-foreground">
                {t('trimmer.length_label')}: {(range.end - range.start).toFixed(2)} /{' '}
                {MAX_TRIM_SECONDS} {t('trimmer.unit_seconds')}
              </span>
            </div>
            {preview && (
              <div ref={previewContainer} className="flex items-center gap-2">
                <WaveformPlayer
                  key={preview.url}
                  src={preview.url}
                  source="gallery-trim"
                  loop={loop}
                  className="flex-1"
                />
                <Button
                  variant={loop ? 'secondary' : 'ghost'}
                  size="icon-sm"
                  aria-label={t('trimmer.loop_preview')}
                  aria-pressed={loop}
                  onClick={() => setLoop(!loop)}
                >
                  <RepeatIcon />
                </Button>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" disabled={busy} onClick={onCancel}>
                {t('common.cancel')}
              </Button>
              <Button disabled={busy || !preview || Boolean(error)} onClick={save}>
                {t(busy ? 'common.loading' : 'trimmer.use_trimmed')}
              </Button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
