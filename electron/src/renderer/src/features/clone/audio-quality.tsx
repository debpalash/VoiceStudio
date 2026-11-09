import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import { requestPlaybackSeek } from '@/lib/audio/playback-clock';
import { formatClock } from '@/components/waveform-player';

interface QualityReport {
  truncated: boolean;
  warnings: {
    kind: 'empty' | 'silence' | 'quiet' | 'loud' | 'clipping' | 'invalid';
    start: number;
    end: number;
  }[];
}

/** Mounted with the output URL as key so dismissed state never leaks between takes. */
export function AudioQuality({ audioPath }: { audioPath: string }) {
  const { t } = useTranslation();
  const [requested, setRequested] = useState(false);
  const id = /^([0-9a-f]{8})\.wav$/.exec(audioPath)?.[1];
  const report = useQuery({
    queryKey: ['audio-quality', audioPath],
    queryFn: ({ signal }) => apiJson<QualityReport>(`/audio/${id}/quality`, { signal }),
    enabled: requested && Boolean(id),
    retry: false,
    staleTime: Infinity,
  });
  if (!id) return null;
  return (
    <div className="contents text-xs text-muted-foreground">
      <Button
        variant="ghost"
        size="xs"
        className="ml-auto"
        onClick={() => setRequested(!requested)}
      >
        {t(requested ? 'common.close' : 'audioQuality.check')}
      </Button>
      {requested ? (
        <div aria-live="polite" className="order-last w-full basis-full space-y-1">
          {report.isFetching ? <p>{t('common.loading')}</p> : null}
          {report.isError ? <p>{t('audioQuality.failed')}</p> : null}
          {report.data ? (
            <>
              <p>{t('audioQuality.advisory')}</p>
              {report.data.truncated ? <p>{t('audioQuality.truncated')}</p> : null}
              {!report.data.warnings.length ? <p>{t('audioQuality.clear')}</p> : null}
              <ul className="max-h-32 overflow-y-auto">
                {report.data.warnings.map((warning, index) => (
                  <li key={index}>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => requestPlaybackSeek('output', warning.start)}
                    >
                      {formatClock(warning.start)}–{formatClock(warning.end)}:{' '}
                      {t(`audioQuality.${warning.kind}`)}
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
