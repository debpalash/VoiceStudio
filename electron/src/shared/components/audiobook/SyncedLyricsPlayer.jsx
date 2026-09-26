import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { activeWordIndex, buildLyricsTimeline } from '../../utils/audiobookLyrics';

/**
 * Audio player with a synced-lyrics pane: the chapter text renders under the
 * native controls and the word under `audio.currentTime` is highlighted, so a
 * listener can follow the narration (and click any word to jump there).
 *
 * Timing comes from `buildLyricsTimeline`: per-chapter durations the render
 * stream already emitted when available, else a proportional even-split over
 * the file's own duration (the reload case). No ASR, nothing leaves the
 * machine. `timeupdate` alone fires ~4 times a second — too coarse for word
 * highlighting — so a requestAnimationFrame loop runs while (and only while)
 * the audio is actually playing.
 *
 * With no resolvable words (blank script) this degrades to exactly the bare
 * `<audio controls>` it replaced.
 */
export default function SyncedLyricsPlayer({ t, src, script, chapters }) {
  const audioRef = useRef(null);
  const paneRef = useRef(null);
  const rafRef = useRef(0);
  const [duration, setDuration] = useState(0);
  const [active, setActive] = useState(-1);

  const timeline = useMemo(
    () => buildLyricsTimeline(script, { chapters, duration }),
    [script, chapters, duration],
  );
  const words = timeline.words;

  const sync = useCallback(() => {
    const el = audioRef.current;
    if (el) setActive(activeWordIndex(words, el.currentTime));
  }, [words]);

  const stopLoop = useCallback(() => cancelAnimationFrame(rafRef.current), []);
  const onPlay = useCallback(() => {
    stopLoop();
    const tick = () => {
      sync();
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [sync, stopLoop]);
  const onPause = useCallback(() => {
    stopLoop();
    sync();
  }, [sync, stopLoop]);
  useEffect(() => stopLoop, [stopLoop]);

  // A new render under the same mounted player starts from scratch.
  useEffect(() => {
    setDuration(0);
    setActive(-1);
  }, [src]);

  // Keep the lit word in view. `scrollIntoView` on the word alone would also
  // scroll the page; scoping via `block:'nearest'` keeps it inside the pane.
  useEffect(() => {
    if (active < 0) return;
    paneRef.current?.querySelector('.is-active')?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);

  const seekTo = useCallback(
    (start) => {
      const el = audioRef.current;
      if (!el) return;
      el.currentTime = start;
      sync();
    },
    [sync],
  );

  return (
    <div className="synced-lyrics">
      <audio
        ref={audioRef}
        controls
        src={src}
        style={{ width: '100%' }}
        onLoadedMetadata={(e) => setDuration(Number(e.target.duration) || 0)}
        onTimeUpdate={sync}
        onSeeked={sync}
        onPlay={onPlay}
        onPause={onPause}
        onEnded={onPause}
      />
      {words.length > 0 && (
        <div
          ref={paneRef}
          className="synced-lyrics__pane"
          role="region"
          aria-label={t('audiobook.lyrics')}
        >
          {timeline.chapters.map((ch, ci) => (
            <LyricsChapter
              key={ci}
              chapter={ch}
              words={words}
              // Chapter rows are memoized so a word tick re-renders only the
              // chapter it lives in — a book runs to tens of thousands of words.
              activeInChapter={
                active >= ch.wordStart && active < ch.wordStart + ch.wordCount ? active : -1
              }
              past={active >= ch.wordStart + ch.wordCount}
              onSeek={seekTo}
              title={ch.title || t('audiobook.chapter_n', { n: ci + 1 })}
            />
          ))}
        </div>
      )}
    </div>
  );
}

const LyricsChapter = React.memo(function LyricsChapter({
  chapter,
  words,
  activeInChapter,
  past,
  onSeek,
  title,
}) {
  return (
    <div className="synced-lyrics__chapter">
      <button type="button" className="synced-lyrics__title" onClick={() => onSeek(chapter.start)}>
        {title}
      </button>
      <p className="synced-lyrics__text">
        {words.slice(chapter.wordStart, chapter.wordStart + chapter.wordCount).map((w, j) => {
          const gi = chapter.wordStart + j;
          const isPast = past || (activeInChapter >= 0 && gi < activeInChapter);
          return (
            <button
              key={gi}
              type="button"
              className={
                'synced-lyrics__word' +
                (gi === activeInChapter ? ' is-active' : '') +
                (isPast ? ' is-past' : '')
              }
              onClick={() => onSeek(w.start)}
              tabIndex={-1}
            >
              {w.text}
            </button>
          );
        })}
      </p>
    </div>
  );
});
