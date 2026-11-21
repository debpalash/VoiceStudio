import { createRef, useRef } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { TranscriptSegments } from './transcript-segments';

afterEach(() => vi.restoreAllMocks());

const segments = (count: number) =>
  Array.from({ length: count }, (_, index) => ({
    start: index,
    end: index + 1,
    text: `line ${index}`,
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

it('mounts only a window of a long transcript', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(300);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(300);
  function Harness() {
    const ref = useRef<HTMLDivElement>(null);
    return (
      <div ref={ref} style={{ height: 300, overflow: 'auto' }}>
        <TranscriptSegments segments={segments(2000)} scrollRef={ref} />
      </div>
    );
  }
  render(<Harness />);
  open();
  await waitFor(() => expect(screen.queryAllByText(/^line \d+$/).length).toBeGreaterThan(0));
  expect(screen.queryAllByText(/^line \d+$/).length).toBeLessThan(200);
});
