import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// Regression guard for the "20617d ago" epoch bug: /history rows carry
// created_at as Unix SECONDS (backend time.time()). Projects/OmniDrive fed
// them to a ms-based diff, so every generation-history card rendered as
// ~1970 ("20617d ago") and sorted to the bottom. The fix funnels every
// timestamp through utils/relativeTime.toMillis().

vi.mock('../utils/media', () => ({ playBlobAudio: vi.fn() }));
vi.mock('../api/generate', () => ({ audioUrl: (f) => `http://test.local/audio/${f}` }));
vi.mock('../api/client', () => ({
  apiFetch: vi.fn(async () => ({ json: async () => ({ jobs: [] }) })),
}));

import Projects from '../pages/Projects';

describe('Projects — relative timestamps (seconds-vs-ms epoch class)', () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a seconds created_at from today as hours ago, not "20617d ago"', async () => {
    const history = [
      {
        id: 'h1',
        filename: 'gen1.wav',
        text: 'Hello from today',
        created_at: Date.now() / 1000 - 7200, // 2h ago, in Unix SECONDS
      },
    ];
    render(<Projects history={history} />);

    expect(await screen.findByText('2h ago')).toBeInTheDocument();
    // The literal pre-fix rendering: tens of thousands of days.
    expect(screen.queryByText(/\d{3,}d ago/)).toBeNull();
  });

  it('renders a dash for records with a missing timestamp', async () => {
    const history = [{ id: 'h2', filename: 'gen2.wav', text: 'No stamp', created_at: null }];
    render(<Projects history={history} />);

    expect(await screen.findByText('No stamp')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('sorts seconds-stamped records among ms-stamped ones by real recency', async () => {
    const nowS = Date.now() / 1000;
    render(
      <Projects
        history={[{ id: 'h3', filename: 'g3.wav', text: 'Newest gen', created_at: nowS - 60 }]}
        storyProjects={[{ id: 's1', name: 'Old story', updatedAt: Date.now() - 3 * 86400e3 }]}
      />,
    );

    const titles = (await screen.findAllByText(/Newest gen|Old story/)).map((el) => el.textContent);
    // Pre-fix, the seconds stamp sorted as ~0 and sank below the story.
    expect(titles).toEqual(['Newest gen', 'Old story']);
  });
});
