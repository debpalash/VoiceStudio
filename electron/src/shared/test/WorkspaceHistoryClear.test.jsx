import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import WorkspaceHistory from '../components/WorkspaceHistory';

// #1032: the old left Sidebar had a "Clear History" button; the workspace UX
// overhaul (#374) moved history into the right-side WorkspaceHistory panels
// and dropped it — per-item delete was the only way to empty a long history.
// The panel must expose a clear-all affordance again (both variants), wired
// to the handler App.jsx passes in (which owns confirm + endpoint + reload).

const synthItem = (id, mode = 'clone') => ({
  id,
  mode,
  text: `take ${id}`,
  language: 'en',
  generation_time: 1.2,
  // no audio_path on purpose — keeps LazyWaveform (IntersectionObserver)
  // out of jsdom; the clear-all button lives in the header regardless.
});

describe('WorkspaceHistory clear-all (#1032)', () => {
  it('voice variant: shows Clear History and calls the handler', () => {
    const clearHistory = vi.fn();
    render(
      <WorkspaceHistory
        history={[synthItem('a'), synthItem('b', 'design')]}
        clearHistory={clearHistory}
        deleteHistory={vi.fn()}
        restoreHistory={vi.fn()}
        handleSaveHistoryAsProfile={vi.fn()}
        handleLockProfile={vi.fn()}
        handleNativeExport={vi.fn()}
      />,
    );
    const btn = screen.getByRole('button', { name: /clear history/i });
    fireEvent.click(btn);
    expect(clearHistory).toHaveBeenCalledTimes(1);
  });

  it('voice variant: hidden when history is empty', () => {
    render(<WorkspaceHistory history={[]} clearHistory={vi.fn()} deleteHistory={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /clear history/i })).toBeNull();
  });

  it('voice variant: hidden when no handler is provided (defensive)', () => {
    render(<WorkspaceHistory history={[synthItem('a')]} deleteHistory={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /clear history/i })).toBeNull();
  });

  it('dub variant: shows Clear History and calls the handler', () => {
    const clearHistory = vi.fn();
    render(
      <WorkspaceHistory
        variant="dub"
        dubHistory={[{ id: 'd1', filename: 'movie.mp4', segments_count: 3, duration: 12 }]}
        clearHistory={clearHistory}
        deleteHistory={vi.fn()}
        restoreDubHistory={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /clear history/i }));
    expect(clearHistory).toHaveBeenCalledTimes(1);
  });

  it('dub variant: hidden when dub history is empty', () => {
    render(
      <WorkspaceHistory
        variant="dub"
        dubHistory={[]}
        clearHistory={vi.fn()}
        deleteHistory={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /clear history/i })).toBeNull();
  });
});
