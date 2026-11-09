import type { Profile } from '@/lib/api/types';
export type CloneBlocker =
  | 'engine'
  | 'cloning'
  | 'reference'
  | 'text'
  | 'loading'
  | 'preparing'
  | null;
export function cloneBlocker({
  text,
  profileId,
  profiles,
  fileSize,
  busy = false,
  needsReference = true,
}: {
  text: string;
  profileId: string | null;
  profiles: Profile[] | undefined;
  fileSize: number;
  busy?: boolean;
  /** False on a preset-only engine, which speaks without any reference. */
  needsReference?: boolean;
}): CloneBlocker {
  if (busy) return 'preparing';
  if (!needsReference) return text.trim() ? null : 'text';
  if (profileId) {
    if (!profiles) return 'loading';
    if (
      !profiles.some(
        (profile) =>
          profile.id === profileId && profile.kind === 'clone' && Boolean(profile.ref_audio_path),
      )
    )
      return 'reference';
  } else if (fileSize <= 0) return 'reference';
  if (!text.trim()) return 'text';
  return null;
}
