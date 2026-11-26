import { CheckCheckIcon, PlayIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

export function DubSpeechReady({ onPlay, loading }: { onPlay: () => void; loading: boolean }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex shrink-0 items-center gap-1 text-emerald-600 dark:text-emerald-400">
      <CheckCheckIcon className="size-3" aria-hidden="true" />
      <span className="text-[10px] font-medium">{t('dub.speech_ready')}</span>
      <Button
        size="icon-xs"
        variant="ghost"
        disabled={loading}
        aria-label={t('dub.play_generated_speech')}
        title={t('dub.speech_preview_hint')}
        aria-busy={loading}
        onClick={onPlay}
      >
        <PlayIcon className="size-3" />
      </Button>
    </span>
  );
}
