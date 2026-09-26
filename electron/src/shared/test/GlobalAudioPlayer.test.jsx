import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import GlobalAudioPlayer from '../components/GlobalAudioPlayer';
import { claimPlayback, claimTrackedPlayback, stopActivePlayback } from '../utils/playback';

// Global audio mini-player (successor of the #1032 PlaybackStopPill, whose
// contract it fully migrates): playBlobAudio plays through a bare
// Audio()/AudioContext with no on-screen player (source 'output') — the
// generate auto-play, profile/segment previews, story lines, gallery voices
// and Projects renders. The bar must appear for 'output' playback on any
// page with label + waveform + seek + time + play/pause + stop, disappear
// when playback stops/ends, and stay out of the way for playback that
// already has a visible player (WaveformPlayer sources, demos).

afterEach(() => {
  act(() => stopActivePlayback()); // never leak an active claim into the next test
});

const claimOutput = (overrides = {}) => {
  let session;
  act(() => {
    session = claimTrackedPlayback({
      stop: vi.fn(),
      source: 'output',
      label: 'Generated audio',
      seek: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      ...overrides,
    });
    session.update({ duration: 10, currentTime: 2, peaks: [0.2, 0.9, 0.5] });
  });
  return session;
};

describe('GlobalAudioPlayer', () => {
  it('renders nothing when idle', () => {
    render(<GlobalAudioPlayer />);
    expect(screen.queryByTestId('global-audio-player')).toBeNull();
  });

  it('appears for an "output" playback with label and time readout', () => {
    render(<GlobalAudioPlayer />);
    claimOutput();
    expect(screen.getByTestId('global-audio-player')).toBeInTheDocument();
    expect(screen.getByText('Generated audio')).toBeInTheDocument();
    expect(screen.getByText('0:02 / 0:10')).toBeInTheDocument();
  });

  it('stop button halts playback and the bar unmounts (pill parity)', () => {
    const stop = vi.fn();
    render(<GlobalAudioPlayer />);
    claimOutput({ stop });
    fireEvent.click(screen.getByRole('button', { name: /stop playback/i }));
    expect(stop).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('global-audio-player')).toBeNull();
  });

  it('disappears when playback ends on its own (release — pill parity)', () => {
    render(<GlobalAudioPlayer />);
    const session = claimOutput();
    expect(screen.getByTestId('global-audio-player')).toBeInTheDocument();
    act(() => session.release());
    expect(screen.queryByTestId('global-audio-player')).toBeNull();
  });

  it('ignores sources that already have visible player UI (pill parity)', () => {
    render(<GlobalAudioPlayer />);
    act(() => {
      claimPlayback(vi.fn(), 'design-preview');
    });
    expect(screen.queryByTestId('global-audio-player')).toBeNull();
    act(() => {
      claimPlayback(vi.fn(), 'demo-output');
    });
    expect(screen.queryByTestId('global-audio-player')).toBeNull();
  });

  it('click on the waveform seeks proportionally into the track', () => {
    const seek = vi.fn();
    render(<GlobalAudioPlayer />);
    claimOutput({ seek });
    const slider = screen.getByRole('slider');
    slider.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, height: 28 });
    fireEvent.pointerDown(slider, { clientX: 100, pointerId: 1 });
    expect(seek).toHaveBeenCalledTimes(1);
    expect(seek.mock.calls[0][0]).toBeCloseTo(5); // 50% of 10s
    // Drag continues the scrub…
    fireEvent.pointerMove(slider, { clientX: 150, pointerId: 1 });
    expect(seek.mock.calls[1][0]).toBeCloseTo(7.5);
    // …and pointer-up ends it (moves stop seeking).
    fireEvent.pointerUp(slider, { pointerId: 1 });
    fireEvent.pointerMove(slider, { clientX: 20, pointerId: 1 });
    expect(seek).toHaveBeenCalledTimes(2);
  });

  it('keyboard seeks: arrows nudge ±5s, Home/End jump', () => {
    const seek = vi.fn();
    render(<GlobalAudioPlayer />);
    claimOutput({ seek });
    const slider = screen.getByRole('slider');
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(seek).toHaveBeenLastCalledWith(7); // 2 + 5
    fireEvent.keyDown(slider, { key: 'ArrowLeft' });
    expect(seek).toHaveBeenLastCalledWith(0); // clamped at 0
    fireEvent.keyDown(slider, { key: 'End' });
    expect(seek).toHaveBeenLastCalledWith(10);
    fireEvent.keyDown(slider, { key: 'Home' });
    expect(seek).toHaveBeenLastCalledWith(0);
  });

  it('play/pause toggles through the manager transport', () => {
    const pause = vi.fn();
    const resume = vi.fn();
    render(<GlobalAudioPlayer />);
    const session = claimOutput({ pause, resume });
    fireEvent.click(screen.getByRole('button', { name: /pause/i }));
    expect(pause).toHaveBeenCalledTimes(1);
    // The owner reports the paused state (like an <audio> 'pause' event would).
    act(() => session.update({ paused: true }));
    fireEvent.click(screen.getByRole('button', { name: /^play$/i }));
    expect(resume).toHaveBeenCalledTimes(1);
  });

  it('legacy stop-only "output" claims still get a bar with a working stop', () => {
    // A claimPlayback('output') without transport metadata must never be
    // invisible — that would regress the #1032 stop affordance.
    const stop = vi.fn();
    render(<GlobalAudioPlayer />);
    act(() => {
      claimPlayback(stop, 'output');
    });
    expect(screen.getByTestId('global-audio-player')).toBeInTheDocument();
    // No transport → no play/pause button, seek disabled.
    expect(screen.queryByRole('button', { name: /pause/i })).toBeNull();
    expect(screen.getByRole('slider')).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByRole('button', { name: /stop playback/i }));
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it('publishes --audio-dock-height while visible so fixed overlays stack above', () => {
    render(<GlobalAudioPlayer />);
    const session = claimOutput();
    expect(document.documentElement.style.getPropertyValue('--audio-dock-height')).toBe('44px');
    act(() => session.release());
    expect(document.documentElement.style.getPropertyValue('--audio-dock-height')).toBe('0px');
  });
});
