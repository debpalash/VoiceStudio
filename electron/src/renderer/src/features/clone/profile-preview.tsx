import { LockVoice } from './lock-voice';
import { useEffect, useId, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { PlayIcon, LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EngineNotice } from '@/components/engine-notice';
import { WaveformPlayer } from '@/components/waveform-player';
import { generateClone } from '@/lib/api/generate';
import { describeError } from '@/lib/api/client';
import type { Profile } from '@/lib/api/types';
import { DEFAULT_CLONE_SETTINGS } from '@/lib/store/clone-settings';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { queryKeys } from '@/lib/query';
import { beginAppActivity } from '@/lib/app-activity';
import type { TtsReadinessBlocker } from '@/hooks/use-tts-readiness';
import { announceDroppedSpeech } from '@/lib/dropped-speech';

export function ProfilePreview({
  profile,
  ttsBlocker = null,
}: {
  profile: Profile;
  ttsBlocker?: TtsReadinessBlocker;
}) {
  const { t } = useTranslation();
  const id = useId();
  const client = useQueryClient();
  const [text, setText] = useState(() => t('voice_profile.test_text'));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState('');
  const [take, setTake] = useState<{ id: string; seed: number | null } | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(
    () => () => {
      if (url) URL.revokeObjectURL(url);
    },
    [url],
  );
  const generate = async () => {
    if (request.current || !text.trim() || ttsBlocker) return;
    const release = acquireSynthesis();
    if (!release) {
      setError(t('tts_errors.generation_in_progress'));
      return;
    }
    const controller = new AbortController();
    const finishActivity = beginAppActivity('synthesis');
    request.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result = await generateClone(
        {
          ...DEFAULT_CLONE_SETTINGS,
          text,
          profileId: profile.id,
          language: profile.language || 'Auto',
          instruct: profile.instruct || '',
          seed: profile.seed ?? undefined,
        },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      announceDroppedSpeech(result.dropped);
      setUrl(URL.createObjectURL(result.blob));
      setTake(result.id ? { id: result.id, seed: result.seed } : null);
      toast.success(t('tts.generationComplete'));
      void client.invalidateQueries({ queryKey: queryKeys.history });
      void client.invalidateQueries({
        queryKey: ['profile-usage', profile.id],
      });
    } catch (cause) {
      if (!controller.signal.aborted) setError(describeError(cause));
    } finally {
      finishActivity();
      release();
      request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  return (
    <details className="space-y-3 border-t border-border/50 pt-4">
      <summary className="cursor-pointer text-sm font-medium">
        {t('voice_profile.try_voice')}
      </summary>
      <label htmlFor={id} className="block text-xs text-muted-foreground">
        {t('voice_profile.test_phrase')}
      </label>
      <textarea
        id={id}
        rows={3}
        value={text}
        disabled={busy}
        onChange={(event) => setText(event.target.value)}
        placeholder={t('voice_profile.test_placeholder')}
        className="w-full resize-y rounded-md border border-input bg-transparent p-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      <Button
        type="button"
        size="sm"
        disabled={busy || !text.trim() || ttsBlocker !== null}
        onClick={() => void generate()}
      >
        {busy ? (
          <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
        ) : (
          <PlayIcon />
        )}
        {t(busy ? 'voice_profile.generating' : 'voice_profile.gen_preview')}
      </Button>
      {ttsBlocker === 'engine' && <EngineNotice operation="profile-preview" compact />}
      {ttsBlocker === 'loading' && (
        <p role="status" className="text-xs text-muted-foreground">
          {t('preferences.loading')}
        </p>
      )}
      {error && (
        <div
          role="alert"
          className="rounded-md border border-border/60 bg-muted/20 p-3 text-xs"
        >
          <p className="font-medium">
            {t('profileIdentity.generation_failed')}
          </p>
          <details className="mt-1 text-muted-foreground">
            <summary className="cursor-pointer rounded py-1 focus-visible:outline-ring">
              {t('profileIdentity.details')}
            </summary>
            <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 p-2 font-mono text-xs">
              {error}
            </pre>
            <Link to="/settings/logs" className="mt-2 inline-block underline">
              {t('settings.logs')}
            </Link>
          </details>
        </div>
      )}
      {url && (
        <WaveformPlayer key={url} src={url} source={'profile-preview-' + profile.id} height={36} />
      )}
      {take && (
        <LockVoice
          locked={Boolean(profile.is_locked)}
          key={take.id}
          profileId={profile.id}
          historyId={take.id}
          seed={take.seed}
          disabled={busy}
        />
      )}
    </details>
  );
}
