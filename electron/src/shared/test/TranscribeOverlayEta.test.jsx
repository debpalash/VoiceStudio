import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import TranscribeOverlay from '../components/dub/TranscribeOverlay';

// #1127. The ETA used to be invented from the video's duration alone:
//
//     const est = Math.max(10, Math.ceil(duration / 60) * 3 + 8);
//
// — "3 seconds per minute of video", i.e. an assumption of ~20x-realtime
// transcription. On a Mac, WhisperX runs on the CPU at ~0.33x realtime. For a
// 16-minute video the old code predicted 56 seconds against a real ~48 minutes,
// then clamped to "~0s remaining" and froze the bar at 95% for three quarters of
// an hour. These pin the honest replacement: extrapolate from the rate we can
// actually SEE, and say nothing when we can't see one yet.

describe('TranscribeOverlay ETA', () => {
  it('says nothing about time remaining before the first chunk lands', () => {
    // The old code would already be claiming an ETA here, from thin air.
    render(<TranscribeOverlay elapsed={30} duration={960} progress={0} onAbort={() => {}} />);
    expect(screen.queryByText(/remaining/i)).toBeNull();
    expect(screen.queryByText(/~/)).toBeNull();
  });

  it('extrapolates from the observed rate, so a slow machine gets a big honest number', () => {
    // A 16-minute video, 10% done after 5 minutes => ~45 min left. The old code
    // would have said "~0s".
    render(<TranscribeOverlay elapsed={300} duration={960} progress={0.1} onAbort={() => {}} />);
    expect(screen.getByText(/45m/)).toBeInTheDocument();
    expect(screen.getByText('10%')).toBeInTheDocument();
  });

  it('a fast machine gets a correspondingly small number — same formula, no special case', () => {
    // 80% done after 60s => ~15s left.
    render(<TranscribeOverlay elapsed={60} duration={120} progress={0.8} onAbort={() => {}} />);
    expect(screen.getByText(/15s/)).toBeInTheDocument();
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  it('never shows a negative or NaN remaining', () => {
    render(<TranscribeOverlay elapsed={100} duration={60} progress={1} onAbort={() => {}} />);
    expect(screen.queryByText(/-/)).toBeNull();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it('still shows elapsed, which was always the one true number', () => {
    render(<TranscribeOverlay elapsed={125} duration={960} progress={0.2} onAbort={() => {}} />);
    expect(screen.getByText(/2:05/)).toBeInTheDocument();
  });
});
