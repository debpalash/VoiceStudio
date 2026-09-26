import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

import ChangelogViewer from './ChangelogViewer';

const RELEASES = [
  {
    version: '0.3.9',
    date: '2026-07-02',
    intro: 'The dictation release.',
    sections: [
      { title: 'Added', bullets: ['**Dictation, rebuilt.** Live waveform. (#123)'] },
      { title: 'Fixed', bullets: ['**CUDA works.** Compat libs install. (#827)'] },
    ],
  },
  {
    version: '0.3.8',
    date: '2026-07-01',
    intro: 'A stability-focused release.',
    sections: [{ title: 'Added', bullets: ['**Autofit.** Keeps the timing. (#838)'] }],
  },
];

describe('ChangelogViewer (Settings → Updates "What\'s new")', () => {
  it('renders every release with the newest expanded and older collapsed', () => {
    render(<ChangelogViewer releases={RELEASES} />);
    expect(screen.getByTestId('changelog-toggle-0.3.9')).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('changelog-toggle-0.3.8')).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('changelog-body-0.3.9')).toBeInTheDocument();
    expect(screen.queryByTestId('changelog-body-0.3.8')).not.toBeInTheDocument();
  });

  it('renders intro, section titles, and bullets with bold leads as text (no HTML)', () => {
    render(<ChangelogViewer releases={RELEASES} />);
    const body = screen.getByTestId('changelog-body-0.3.9');
    expect(body).toHaveTextContent('The dictation release.');
    expect(body).toHaveTextContent('Added');
    expect(body).toHaveTextContent('Fixed');
    // The **bold lead** renders as a <strong> element, not literal asterisks.
    const strong = body.querySelector('strong');
    expect(strong).not.toBeNull();
    expect(strong.textContent).toBe('Dictation, rebuilt.');
    expect(body.textContent).not.toContain('**');
    // Refs stay plain text — no links are ever emitted.
    expect(body).toHaveTextContent('(#123)');
    expect(body.querySelector('a')).toBeNull();
  });

  it('accordion: clicking an older release expands it; clicking again collapses', () => {
    render(<ChangelogViewer releases={RELEASES} />);
    fireEvent.click(screen.getByTestId('changelog-toggle-0.3.8'));
    expect(screen.getByTestId('changelog-body-0.3.8')).toBeInTheDocument();
    // Only one open at a time — the newest closed when the older opened.
    expect(screen.queryByTestId('changelog-body-0.3.9')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('changelog-toggle-0.3.8'));
    expect(screen.queryByTestId('changelog-body-0.3.8')).not.toBeInTheDocument();
  });

  it('renders nothing for empty input', () => {
    const { container } = render(<ChangelogViewer releases={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
