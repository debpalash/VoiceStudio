import { useRef } from 'react';
import { BookMarked, BookOpen, FileUp, ListTree, Loader, Sparkles, Square } from 'lucide-react';

import { Button } from '../../ui';

/** The inviting front door for the long-form workflow: context, path, and actions. */
export default function AudiobookHero({
  t,
  busy,
  importing,
  planLoading,
  generating,
  canRun,
  onImport,
  onLoadSample,
  onPreview,
  onCreate,
  onStop,
}) {
  const importInputRef = useRef(null);

  return (
    <section className="rounded-[12px] border border-transparent bg-[var(--color-bg-elev-2)] px-[12px] py-[9px]">
      <div className="flex flex-wrap items-center justify-between gap-[10px]">
        <div className="flex min-w-0 items-center gap-[9px]">
          <div
            className="relative flex h-[34px] w-[28px] shrink-0 items-center justify-center rounded-[5px_8px_8px_5px] bg-primary/[0.13] text-primary shadow-[inset_3px_0_0_color-mix(in_srgb,var(--color-brand)_25%,transparent)]"
            aria-hidden="true"
          >
            <BookMarked size={15} strokeWidth={1.8} />
          </div>
          <h2 className="m-0 [font-family:var(--font-serif)] text-[var(--text-lg)] font-semibold text-fg">
            {t('audiobook.title')}
          </h2>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-[4px]">
          <Button
            type="button"
            variant="subtle"
            size="sm"
            title={t('audiobook.import')}
            aria-label={t('audiobook.import')}
            onClick={() => importInputRef.current?.click()}
            disabled={busy}
            leading={importing ? <Loader className="animate-spin" /> : <FileUp />}
          >
            {t('audiobook.import')}
          </Button>
          <input
            ref={importInputRef}
            type="file"
            accept=".txt,.md,.epub,.pdf"
            onChange={onImport}
            disabled={busy}
            tabIndex={-1}
            aria-hidden="true"
            className="hidden"
          />
          <Button
            variant="subtle"
            size="sm"
            onClick={onLoadSample}
            disabled={busy}
            title={t('audiobook.load_sample_hint')}
            aria-label={t('audiobook.load_sample')}
            leading={<BookOpen />}
          >
            {t('audiobook.load_sample')}
          </Button>
          <Button
            variant="subtle"
            size="sm"
            onClick={onPreview}
            disabled={!canRun}
            loading={planLoading}
            title={t('audiobook.preview_plan')}
            aria-label={t('audiobook.preview_plan')}
            leading={<ListTree />}
          >
            {t('audiobook.preview_plan')}
          </Button>
          {generating ? (
            <Button variant="danger" size="sm" onClick={onStop} leading={<Square />}>
              {t('audiobook.stop')}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              onClick={onCreate}
              disabled={!canRun}
              leading={<Sparkles />}
            >
              {t('audiobook.create')}
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}
