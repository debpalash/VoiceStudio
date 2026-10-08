import { useTranslation } from 'react-i18next';
import type { Profile } from '@/lib/api/types';
import { PortraitSearch } from './portrait-search';
import { ProfilePhoto, useProfileImageSave } from './profile-photo';

export function ProfileImageEditor({ profile }: { profile: Profile }) {
  const { t } = useTranslation();
  const { busy, save } = useProfileImageSave(profile);
  return (
    <div className="space-y-2 py-2">
      <div className="flex items-center gap-3">
        <ProfilePhoto
          name={profile.name}
          imageUrl={profile.image_url}
          busy={busy}
          onFile={(file) => void save(file)}
          className="size-12 text-base"
        />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{profile.name}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {busy ? t('preferences.loading') : t('cloneFlow.photo_hint')}
          </p>
        </div>
      </div>
      <PortraitSearch name={profile.name} disabled={busy} onSelect={(file) => void save(file)} />
    </div>
  );
}
