import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { TranscriptSegments } from './transcript-segments';

const segments = (count: number) =>
  Array.from({ length: count }, (_, index) => ({
    start: index,
    end: index + 1,
    text: `line ${index}`,
    speaker: null,
  }));

function open() {
  const summary = screen.getByText('transcriptions.segments_title');
  const details = summary.closest('details') as HTMLDetailsElement;
  details.open = true;
  fireEvent(details, new Event('toggle'));
}

it('mounts no segment rows while the section is closed', () => {
  render(<TranscriptSegments segments={segments(5)} scrollRef={createRef()} />);
  expect(screen.queryByText('line 0')).not.toBeInTheDocument();
  open();
  expect(screen.getByText('line 0')).toBeInTheDocument();
  expect(screen.getByText('line 4')).toBeInTheDocument();
});

it('mounts only a window of a long transcript', () => {
  render(<TranscriptSegments segments={segments(2000)} scrollRef={createRef()} />);
  open();
  expect(screen.queryAllByText(/^line \d+$/).length).toBeLessThan(200);
});
