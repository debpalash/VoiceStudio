import { Image as ImageIcon, X } from 'lucide-react';

import { Button } from '../../ui';
import { buttonVariants } from '@/components/ui/button.tsx';

const META_FIELDS = ['title', 'author', 'narrator', 'year', 'genre'];

/**
 * Cover picker + the embedded-metadata fields for the Audiobook tab. Extracted
 * from AudiobookTab so the tab stays under the max-lines lint and the block can
 * live inside a collapsible Section. Behaviour-identical to the inline version:
 * same inputs, same aria-labels, same store-backed `meta` / `setMetaField`.
 */
export default function BookDetails({
  t,
  coverPreview,
  onCoverPick,
  clearCover,
  meta,
  setMetaField,
}) {
  return (
    <div className="flex items-start gap-[9px]">
      <div className="relative size-[64px] shrink-0">
        {coverPreview ? (
          <>
            <img
              src={coverPreview}
              alt={t('audiobook.cover')}
              width="64"
              height="64"
              className="size-[64px] rounded-[7px] object-cover"
            />
            <Button
              variant="icon"
              iconSize="sm"
              onClick={clearCover}
              aria-label={t('audiobook.cover_remove')}
              style={{ position: 'absolute', top: 4, right: 4 }}
            >
              <X size={14} />
            </Button>
          </>
        ) : (
          <label
            className={buttonVariants({ variant: 'subtle', size: 'omniMd' })}
            style={{
              width: 64,
              height: 64,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 4,
              cursor: 'pointer',
            }}
          >
            <ImageIcon size={17} />
            <span className="text-[0.58rem]">{t('audiobook.cover_add')}</span>
            <input
              type="file"
              accept="image/png,image/jpeg"
              onChange={onCoverPick}
              style={{ display: 'none' }}
            />
          </label>
        )}
      </div>
      <div className="grid min-w-0 flex-1 grid-cols-2 gap-[6px]">
        {META_FIELDS.map((k) => (
          <input
            key={k}
            className="input-base"
            name={`audiobook-${k}`}
            autoComplete="off"
            placeholder={t(`audiobook.meta_${k}`)}
            value={meta[k]}
            onChange={setMetaField(k)}
            aria-label={t(`audiobook.meta_${k}`)}
          />
        ))}
        <input
          className="input-base"
          name="audiobook-description"
          autoComplete="off"
          placeholder={t('audiobook.meta_description')}
          value={meta.description}
          onChange={setMetaField('description')}
          aria-label={t('audiobook.meta_description')}
          style={{ gridColumn: '1 / -1' }}
        />
      </div>
    </div>
  );
}
