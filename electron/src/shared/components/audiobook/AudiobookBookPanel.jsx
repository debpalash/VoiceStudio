import { BookText, Disc3, SpellCheck, Code } from 'lucide-react';

import Section from './Section';
import SearchableSelect from '../SearchableSelect';
import BookDetails from './BookDetails';
import LexiconEditor from './LexiconEditor';

/**
 * AudiobookBookPanel — the Produce tab: the book as an artefact.
 *
 * Output format + loudness stay pinned on top (they shape the render); book
 * details, pronunciation lexicon, and the markup cheat-sheet fold into
 * collapsible sections so the tab stays scannable. Mirrors the clone
 * workspace's section rhythm (label-row kickers, borderless surfaces).
 */
export default function AudiobookBookPanel({
  t,
  format,
  setFormat,
  loudness,
  setLoudness,
  coverPreview,
  onCoverPick,
  clearCover,
  meta,
  setMetaField,
  lex,
  setLexRow,
  addLexRow,
  removeLexRow,
  detailCount,
  lexiconCount,
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[14px] overflow-y-auto px-[2px] py-[2px]">
      <div className="grid grid-cols-2 gap-[10px] max-[640px]:grid-cols-1">
        <div className="flex min-w-0 flex-col gap-[4px]">
          <span className="label-row mb-0">
            <Disc3 className="label-icon" size={14} aria-hidden="true" /> {t('audiobook.format')}
          </span>
          <SearchableSelect
            value={format}
            onChange={setFormat}
            ariaLabel={t('audiobook.format')}
            menuPortal
            options={[
              { value: 'm4b', label: t('audiobook.format_m4b') },
              { value: 'mp3', label: t('audiobook.format_mp3') },
            ]}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-[4px]">
          <span className="label-row mb-0">{t('audiobook.loudness')}</span>
          <SearchableSelect
            value={loudness}
            onChange={setLoudness}
            ariaLabel={t('audiobook.loudness')}
            menuPortal
            options={[
              { value: 'off', label: t('audiobook.loudness_off') },
              { value: 'acx', label: t('audiobook.loudness_acx') },
              { value: 'podcast', label: t('audiobook.loudness_podcast') },
            ]}
          />
        </div>
      </div>

      <Section
        title={`${t('audiobook.details')}${detailCount > 0 ? ` · ${detailCount}` : ''}`}
        icon={<BookText size={12} aria-hidden="true" />}
        defaultOpen={detailCount > 0}
      >
        <BookDetails
          t={t}
          coverPreview={coverPreview}
          onCoverPick={onCoverPick}
          clearCover={clearCover}
          meta={meta}
          setMetaField={setMetaField}
        />
      </Section>

      <Section
        title={`${t('audiobook.lexicon')}${lexiconCount > 0 ? ` · ${lexiconCount}` : ''}`}
        icon={<SpellCheck size={12} aria-hidden="true" />}
        defaultOpen={lexiconCount > 0}
      >
        <LexiconEditor
          t={t}
          lex={lex}
          setLexRow={setLexRow}
          addLexRow={addLexRow}
          removeLexRow={removeLexRow}
        />
      </Section>

      <Section title={t('audiobook.markup_help')} icon={<Code size={12} aria-hidden="true" />}>
        <p className="m-0 text-[0.68rem] leading-[1.55] text-fg-muted">
          {t('audiobook.markup_hint')}
        </p>
      </Section>
    </div>
  );
}
