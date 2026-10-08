import { useState, type DragEvent } from 'react';
import { CameraIcon, LoaderCircleIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ProfileAvatar } from '@/components/profile-avatar';
import { describeError } from '@/lib/api/client';
import { updateProfileImage } from '@/lib/api/profiles';
import type { Profile } from '@/lib/api/types';
import { queryKeys } from '@/lib/query';
import { cn } from '@/lib/utils';

export const PROFILE_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
export const PROFILE_IMAGE_LIMIT = 5 * 1024 * 1024;

/** One validation path for picked, dropped and searched images. */
export function acceptProfileImage(file: File, onReject: () => void): boolean {
  if (!PROFILE_IMAGE_TYPES.includes(file.type) || file.size > PROFILE_IMAGE_LIMIT) {
    onReject();
    return false;
  }
  return true;
}

/** Saves a saved voice's photo and patches the cached profile list in place. */
export function useProfileImageSave(profile: Pick<Profile, 'id'>) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const save = async (file: File) => {
    if (busy || !acceptProfileImage(file, () => toast.error(t('profileIdentity.image_limit'))))
      return;
    setBusy(true);
    try {
      const updated = await updateProfileImage(profile.id, file);
      queryClient.setQueryData<Profile[]>(queryKeys.profiles, (old) =>
        old?.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (error) {
      toast.error(t('clone.save_failed', { message: describeError(error) }));
    } finally {
      setBusy(false);
    }
  };
  return { busy, save };
}

/** Avatar that doubles as a photo picker: click to browse, or drop an image on it. */
export function ProfilePhoto({
  name,
  imageUrl,
  onFile,
  busy = false,
  className,
  label,
}: {
  name: string;
  imageUrl?: string | null;
  onFile: (file: File) => void;
  busy?: boolean;
  className?: string;
  label?: string;
}) {
  const { t } = useTranslation();
  const [dragging, setDragging] = useState(false);
  const accessibleLabel = label ?? t('cloneFlow.change_photo');
  const take = (file: File | undefined) => {
    if (file && acceptProfileImage(file, () => toast.error(t('profileIdentity.image_limit'))))
      onFile(file);
  };
  const hasImage = (event: DragEvent) => Array.from(event.dataTransfer.types).includes('Files');
  return (
    <label
      title={accessibleLabel}
      className={cn(
        'group/photo relative isolate block shrink-0 cursor-pointer rounded-full outline-none has-focus-visible:ring-2 has-focus-visible:ring-ring',
        busy && 'pointer-events-none',
        className,
      )}
      onDragOver={(event) => {
        if (!hasImage(event)) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        if (!hasImage(event)) return;
        event.preventDefault();
        setDragging(false);
        take(event.dataTransfer.files[0]);
      }}
    >
      <ProfileAvatar name={name} imageUrl={imageUrl} className="size-full text-[inherit]" />
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-0 grid place-items-center rounded-full bg-black/55 text-white opacity-0 transition-opacity duration-150 group-hover/photo:opacity-100 group-has-focus-visible/photo:opacity-100 motion-reduce:transition-none',
          (busy || dragging) && 'opacity-100',
          dragging && 'bg-primary/70 ring-2 ring-primary',
        )}
      >
        {busy ? (
          <LoaderCircleIcon className="size-1/3 animate-spin motion-reduce:animate-none" />
        ) : (
          <CameraIcon className="size-1/3 max-h-5 max-w-5" />
        )}
      </span>
      <input
        type="file"
        className="sr-only"
        aria-label={accessibleLabel}
        accept={PROFILE_IMAGE_TYPES.join(',')}
        disabled={busy}
        onChange={(event) => {
          take(event.target.files?.[0]);
          event.target.value = '';
        }}
      />
    </label>
  );
}
