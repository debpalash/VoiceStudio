import { DisabledReason } from '@/components/disabled-reason';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SaveIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiFetch } from '@/lib/api/client';
import type { HistoryItem } from '@/lib/api/types';
import { SaveProfileForm } from './reference-panel';

export function SaveTakeProfile({ item }: { item: HistoryItem }) {
  const { t } = useTranslation();
  const request = useRef<AbortController | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => () => request.current?.abort(), []);
  const start = async () => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setFailed(false);
    try {
      const response = await apiFetch('/audio/' + encodeURIComponent(item.audio_path), {
        signal: controller.signal,
      });
      const blob = await response.blob();
      if (!controller.signal.aborted)
        setFile(new File([blob], item.audio_path, { type: blob.type || 'audio/wav' }));
    } catch {
      if (!controller.signal.aborted) setFailed(true);
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  return (
    // Collapsed, the button sits in the take's action row as its primary action;
    // the form it opens takes the full row.
    <div className={file || failed ? 'w-full basis-full space-y-2' : 'contents'}>
      {file ? (
        <>
          <SaveProfileForm
            file={file}
            selectOnSave={false}
            metadata={{
              // Never the raw take text: it may be cut short or carry script
              // markup the take never spoke (#3062). Empty transcribes the audio.
              refText: item.transcript ?? '',
              instruct: item.instruct || '',
              language: item.language || 'Auto',
              seed: item.seed,
            }}
            onDone={() => setFile(null)}
          />
        </>
      ) : (
        <DisabledReason
          className={failed ? undefined : 'min-w-0 flex-1'}
          reason={item.audio_path ? null : t('disabledReason.audio_missing')}
        >
          {(describedBy) => (
            <Button
              type="button"
              size="sm"
              className={failed ? undefined : 'min-w-0 flex-1'}
              disabled={busy || !item.audio_path}
              aria-describedby={describedBy}
              onClick={() => void start()}
            >
              <SaveIcon />
              {t(busy ? 'preferences.loading' : 'clone.save_as_profile')}
            </Button>
          )}
        </DisabledReason>
      )}
      {failed && (
        <p role="alert" className="text-xs text-destructive">
          {t('clone.save_failed', { message: t('backend.retry') })}
        </p>
      )}
    </div>
  );
}
