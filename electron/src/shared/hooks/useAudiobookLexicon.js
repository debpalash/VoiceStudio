import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../store';

/**
 * Pronunciation-lexicon editor state for the Audiobook tab (extracted from
 * AudiobookTab so the page stays under the max-lines lint, #1217). Rows stay
 * LOCAL (half-typed rows aren't junk-persisted); the filtered {word → say} dict
 * flushes to the store so it survives a reload, and hydrates back on mount.
 */
export function useAudiobookLexicon() {
  const setLexiconStore = useAppStore((s) => s.setLexicon);
  const storeLexicon = useAppStore((s) => s.lexicon);
  const [lex, setLex] = useState([]); // [{ word, say }]
  const lexHydrated = useRef(false);
  useEffect(() => {
    if (lexHydrated.current) return;
    lexHydrated.current = true;
    const rows = Object.entries(storeLexicon || {}).map(([word, say]) => ({ word, say }));
    if (rows.length) setLex(rows);
  }, [storeLexicon]);
  const lexDict = () =>
    Object.fromEntries(
      lex.filter((r) => r.word.trim() && r.say.trim()).map((r) => [r.word.trim(), r.say.trim()]),
    );
  // Flush the filtered dict to the store whenever rows change (after hydration).
  useEffect(() => {
    if (!lexHydrated.current) return;
    setLexiconStore(lexDict());
  }, [lex]); // eslint-disable-line react-hooks/exhaustive-deps
  const setLexRow = (i, k) => (e) =>
    setLex((rows) => rows.map((r, j) => (j === i ? { ...r, [k]: e.target.value } : r)));
  const addLexRow = () => setLex((rows) => [...rows, { word: '', say: '' }]);
  const removeLexRow = (i) => setLex((rows) => rows.filter((_, j) => j !== i));
  return { lex, lexDict, setLexRow, addLexRow, removeLexRow };
}
