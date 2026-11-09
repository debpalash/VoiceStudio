import { PencilLineIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

/** Asks the Clone reference panel to open and focus its transcript box. */
export const FOCUS_TRANSCRIPT_EVENT = 'voicestudio:focus-clone-transcript';

/** Offered beside a "no transcript" failure: typing the transcript is the other fix. */
export function AddTranscriptButton() {
  const { t } = useTranslation();
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={() => window.dispatchEvent(new Event(FOCUS_TRANSCRIPT_EVENT))}
    >
      <PencilLineIcon />
      {t('failureAction.add_transcript')}
    </Button>
  );
}
