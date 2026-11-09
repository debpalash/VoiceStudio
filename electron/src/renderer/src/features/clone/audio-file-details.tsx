import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { readWavInfo, type WavInfo } from '@/lib/audio/wav-info';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

/** File facts for a take. `compact` shows just the sample rate, with the full line as a tooltip. */
export function AudioFileDetails({ blob, compact = false }: { blob: Blob; compact?: boolean }) {
  const { t } = useTranslation();
  const [info, setInfo] = useState<{ blob: Blob; wav: WavInfo | null } | null>(null);
  useEffect(() => {
    let active = true;
    void blob
      .slice(0, 65536)
      .arrayBuffer()
      .then((buffer) => {
        if (active) setInfo({ blob, wav: readWavInfo(buffer) });
      })
      .catch(() => {
        if (active) setInfo(null);
      });
    return () => {
      active = false;
    };
  }, [blob]);
  const wav = info?.blob === blob ? info.wav : null;
  const size = (blob.size / 1048576).toFixed(2);
  const full = wav
    ? t('cloneQuality.fileInfo', {
        size,
        rate: wav.sampleRate / 1000,
        channels: wav.channels,
        bits: wav.bits,
        encoding: wav.encoding === 'float' ? t('cloneQuality.float') : 'PCM',
      })
    : t('cloneQuality.fileSize', { size });
  if (compact) {
    if (!wav) return <span className="tabular-nums">{full}</span>;
    // A focusable trigger so keyboard users can open the full file facts too.
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              aria-label={full}
              className="rounded-sm tabular-nums underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          }
        >
          {t('cloneQuality.sampleRate', { rate: wav.sampleRate / 1000 })}
        </TooltipTrigger>
        <TooltipContent surface="theme" side="top">
          {full}
        </TooltipContent>
      </Tooltip>
    );
  }
  return <p className="text-xs text-muted-foreground tabular-nums">{full}</p>;
}
