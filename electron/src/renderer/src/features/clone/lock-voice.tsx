import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { LockIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';
import { beginVoiceChange } from '@/lib/store/transcript-saves';
import { queryKeys } from '@/lib/query';

export function LockVoice({
  profileId,
  historyId,
  seed,
  locked = false,
  disabled = false,
}: {
  profileId: string;
  historyId: string;
  seed: number | null;
  locked?: boolean;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const pending = useRef(false);
  const [state, setState] = useState<'idle' | 'busy' | 'done' | 'failed'>('idle');
  useEffect(() => {
    if (!locked) setState((current) => (current === 'done' ? 'idle' : current));
  }, [locked]);
  const lock = async () => {
    if (pending.current || disabled) return;
    pending.current = true;
    setState('busy');
    const settled = beginVoiceChange(profileId);
    try {
      const body = new FormData();
      body.append('history_id', historyId);
      if (seed !== null) body.append('seed', String(seed));
      await apiJson('/profiles/' + encodeURIComponent(profileId) + '/lock', {
        method: 'POST',
        body,
      });
      await client.invalidateQueries({ queryKey: queryKeys.profiles });
      setState('done');
    } catch {
      setState('failed');
    } finally {
      settled();
      pending.current = false;
    }
  };
  return (
    <div className="space-y-1">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled || state === 'busy' || state === 'done'}
        onClick={() => void lock()}
      >
        <LockIcon />
        {t(state === 'done' ? 'voice_profile.locked' : 'sidebar.lock_identity')}
      </Button>
      {state === 'done' && (
        <p role="status" className="text-xs text-muted-foreground">
          {t('profiles.locked')}
        </p>
      )}
      {state === 'failed' && (
        <p role="alert" className="text-xs text-destructive">
          {t('profiles.lock_failed')}
        </p>
      )}
    </div>
  );
}
