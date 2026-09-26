import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

// Regression guard for #595 — the dubbed-video PLAY button did nothing.
//
// WaveSurfer constructs its AudioContext at mount (before any user gesture), so
// on Windows WebView2 / Linux FF / Android Chrome it stays "suspended" and
// playPause() resolves silently with no sound. WaveformPlayer was already fixed
// for this in #510 by awaiting unlockAudio() on the click; WaveformTimeline (the
// dub editor's player) was missed — that's this bug.
//
// Driving WaveSurfer + a real AudioContext through jsdom is brittle, so this is
// a source-level contract guard: every playback entry point in WaveformTimeline
// must resume the AudioContext via unlockAudio() before it kicks off playback.
// It fails-before (no import / no await) and passes-after the fix, and pins the
// invariant so a future refactor can't quietly reintroduce a silent play path.

// Vitest runs with cwd = frontend/, so resolve the component from there.
const src = readFileSync(
  path.resolve(process.cwd(), 'src/components/WaveformTimeline.jsx'),
  'utf8',
);

describe('WaveformTimeline autoplay-unlock wiring (#595)', () => {
  it('imports the shared unlockAudio helper', () => {
    expect(src).toMatch(/import\s*\{\s*unlockAudio\s*\}\s*from\s*['"]\.\.\/utils\/audioUnlock['"]/);
  });

  it('awaits unlockAudio() before playing in every playback entry point', () => {
    // Each playback handler must resume the context first. Grab the body of
    // each handler and assert the unlock await precedes the play/playPause call.
    const handlers = {
      togglePlay: /const togglePlay = useCallback\(async \(\) => \{([\s\S]*?)\}, \[ready\]\);/.exec(
        src,
      )?.[1],
      playRange:
        /const playRange = useCallback\(async \(start, end\) => \{([\s\S]*?)\}, \[\]\);/.exec(
          src,
        )?.[1],
    };
    for (const [name, body] of Object.entries(handlers)) {
      expect(body, `${name} handler not found`).toBeTruthy();
      const unlockAt = body.indexOf('await unlockAudio()');
      expect(unlockAt, `${name} must await unlockAudio()`).toBeGreaterThanOrEqual(0);
      const playAt = body.search(/\.play(Pause)?\(/);
      expect(playAt, `${name} must call play`).toBeGreaterThanOrEqual(0);
      expect(unlockAt, `${name} must unlock before play`).toBeLessThan(playAt);
    }
  });
});
