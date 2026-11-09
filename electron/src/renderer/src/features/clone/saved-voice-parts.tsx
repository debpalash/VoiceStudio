import { useQueryClient } from '@tanstack/react-query';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import type { TtsReadinessBlocker } from '@/hooks/use-tts-readiness';
import { profileAudioUrl } from '@/lib/api/client';
import type { Profile } from '@/lib/api/types';
import { queryKeys } from '@/lib/query';

export const VIRTUALIZE_ABOVE = 30;

export function profileListPresentation(
  count: number,
  library: boolean,
): 'grid' | 'list' | 'virtual' {
  if (library) return 'grid';
  return count > VIRTUALIZE_ABOVE ? 'virtual' : 'list';
}

export function PreviewButton({
  profile,
  ttsBlocker,
  disabledLabel,
}: {
  profile: Profile;
  ttsBlocker: TtsReadinessBlocker;
  disabledLabel: string;
}) {
  const client = useQueryClient();
  const pendingIdentity =
    profile.kind === 'design' && !profile.ref_audio_path && !profile.locked_audio_path;
  return (
    <AudioPreviewButton
      src={profileAudioUrl(profile.id, profile.audio_url)}
      source={'profile-' + profile.id}
      activity={pendingIdentity ? 'synthesis' : undefined}
      disabled={pendingIdentity && Boolean(ttsBlocker)}
      disabledLabel={disabledLabel}
      onReady={
        pendingIdentity
          ? () => void client.invalidateQueries({ queryKey: queryKeys.profiles })
          : undefined
      }
    />
  );
}

/**
 * The saved voice the current workspace is using. Design keeps its own
 * selection, every other workspace uses the cloning voice, so the list never
 * marks two voices as selected at once.
 */
export function selectedVoiceId(
  pathname: string,
  cloneProfileId: string | null | undefined,
  designProfileId: string | null | undefined,
): string | null {
  const design = pathname.replace(/\/+$/, '') === '/design';
  return (design ? designProfileId : cloneProfileId) ?? null;
}
