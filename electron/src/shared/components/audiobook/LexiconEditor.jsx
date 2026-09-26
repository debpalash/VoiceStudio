import { Plus, X } from 'lucide-react';

import { Button } from '../../ui';

/**
 * Pronunciation lexicon editor — the editable {word → respelling} rows plus the
 * Add button. Extracted from AudiobookTab so the block can live inside a
 * collapsible Section and the tab stays under the max-lines lint. Behaviour is
 * identical: the parent still owns the `lex` rows and the row mutators.
 */
export default function LexiconEditor({ t, lex, setLexRow, addLexRow, removeLexRow }) {
  return (
    <>
      {lex.map((row, i) => (
        <div key={i} className="flex gap-[6px]">
          <input
            className="input-base"
            name={`lexicon-word-${i}`}
            autoComplete="off"
            placeholder={t('audiobook.lex_word')}
            value={row.word}
            onChange={setLexRow(i, 'word')}
            aria-label={t('audiobook.lex_word')}
            style={{ flex: 1, minWidth: 0 }}
          />
          <input
            className="input-base"
            name={`lexicon-pronunciation-${i}`}
            autoComplete="off"
            placeholder={t('audiobook.lex_say')}
            value={row.say}
            onChange={setLexRow(i, 'say')}
            aria-label={t('audiobook.lex_say')}
            style={{ flex: 1, minWidth: 0 }}
          />
          <Button
            variant="icon"
            iconSize="sm"
            onClick={() => removeLexRow(i)}
            aria-label={t('audiobook.lex_remove')}
          >
            <X size={14} />
          </Button>
        </div>
      ))}
      <Button
        variant="subtle"
        onClick={addLexRow}
        leading={<Plus size={14} />}
        style={{ alignSelf: 'flex-start' }}
      >
        {t('audiobook.lex_add')}
      </Button>
    </>
  );
}
