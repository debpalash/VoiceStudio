import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { replaceDubSegmentTexts } from './dub-session';
import {
  applyReplaceAll,
  collectMatches,
  nextMatchIndex,
  replaceCurrentMatch,
  textUpdates,
  type FindableSegment,
  type TextMatch,
} from './segment-find';

/** Keystrokes inside the find bar keep native input behaviour (e.g. text undo). */
export function isFindBarTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest('[data-slot="dub-find-bar"]'));
}

/**
 * Find and replace over the Dubbing transcript: query state, the current hit,
 * keeping it scrolled into view, and replacements committed as one undo step.
 */
export function useSegmentFind({
  segments,
  targetCode,
  select,
  scrollToRow,
}: {
  segments: ReadonlyArray<FindableSegment>;
  targetCode?: string;
  select: (id: string) => void;
  scrollToRow: (row: number) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [replacement, setReplacement] = useState('');
  const [matchIndex, setMatchIndex] = useState(0);
  // False until the current hit has been scrolled into view, so the first Next
  // lands on hit 1 instead of skipping it.
  const anchored = useRef(false);
  const followAfterReplace = useRef(false);
  const matches = useMemo(() => collectMatches(segments, query), [segments, query]);
  const current: TextMatch | undefined = matches[matchIndex];

  useEffect(() => {
    if (!matches.length) {
      if (matchIndex !== 0) setMatchIndex(0);
      return;
    }
    if (matchIndex >= matches.length) setMatchIndex(matches.length - 1);
  }, [matches, matchIndex]);

  const goTo = useCallback(
    (index: number) => {
      if (index < 0 || index >= matches.length) return;
      const match = matches[index];
      anchored.current = true;
      setMatchIndex(index);
      select(match.id);
      const row = segments.findIndex((segment) => String(segment.id) === match.id);
      if (row >= 0) requestAnimationFrame(() => scrollToRow(row));
    },
    [matches, segments, select, scrollToRow],
  );

  // After a replacement the current hit may move to another row; follow it.
  useEffect(() => {
    if (!followAfterReplace.current) return;
    followAfterReplace.current = false;
    if (matches.length) goTo(Math.min(matchIndex, matches.length - 1));
  }, [matches, goTo, matchIndex]);

  const applySpoken = useCallback(
    (next: ReadonlyArray<FindableSegment>) => {
      const updates = textUpdates(segments, next);
      if (!updates.length) return 0;
      return replaceDubSegmentTexts(updates, targetCode);
    },
    [segments, targetCode],
  );

  /** The active hit's range inside one row field, for highlighting. */
  const activeRange = useCallback(
    (id: string, field: TextMatch['field']) =>
      current?.id === String(id) && current.field === field
        ? { activeStart: current.start, activeEnd: current.end }
        : { activeStart: undefined, activeEnd: undefined },
    [current],
  );

  return {
    query,
    activeRange,
    /** Whether this row field holds the active hit (show it unclamped). */
    holdsActive: (id: string, field: TextMatch['field']) =>
      current?.id === String(id) && current.field === field,
    bar: {
      query,
      replacement,
      matchIndex,
      matchCount: matches.length,
      canReplace: current?.field === 'text',
      canReplaceAll: matches.some((match) => match.field === 'text'),
      onQuery: (value: string) => {
        setQuery(value);
        setMatchIndex(0);
        anchored.current = false;
      },
      onReplacement: setReplacement,
      onPrev: () => goTo(nextMatchIndex(matches.length, matchIndex, -1)),
      onNext: () =>
        goTo(
          anchored.current
            ? nextMatchIndex(matches.length, matchIndex, 1)
            : Math.min(matchIndex, matches.length - 1),
        ),
      onReplace: () => {
        if (!current || current.field !== 'text') return;
        followAfterReplace.current = true;
        if (!applySpoken(replaceCurrentMatch(segments, current, replacement))) {
          followAfterReplace.current = false;
          return;
        }
        toast.success(t('segment.replace_done', { count: 1 }));
      },
      onReplaceAll: () => {
        const { segments: next, count } = applyReplaceAll(segments, query, replacement);
        if (!count || !applySpoken(next)) return;
        toast.success(t('segment.replace_done', { count }));
        setMatchIndex(0);
        anchored.current = false;
      },
    },
  };
}
