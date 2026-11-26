import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { DubScriptComparison } from './dub-script-comparison';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
afterEach(cleanup);
const captions = [
  { id: 'a', start: 10, end: 12, text: 'Hello', text_original: 'Hola' },
  { id: 'b', start: 12, end: 15, text: 'world.', text_original: 'mundo.' },
];
const block = {
  id: 'phrase',
  source_ids: ['a', 'b'],
  start: 10,
  end: 15,
  text: 'Hello world.',
  text_original: 'Hola mundo.',
};
it('compares original SRT words with grouped speech without altering either', () => {
  const original = structuredClone(captions);
  render(<DubScriptComparison captions={captions} blocks={[block]} />);
  fireEvent.click(screen.getByRole('button', { name: 'dub.compare_title' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText('Hola')).toBeVisible();
  expect(dialog.getByText('mundo.')).toBeVisible();
  expect(dialog.getByText('Hello world.')).toBeVisible();
  expect(dialog.getByRole('status')).toHaveTextContent('dub.compare_match');
  expect(captions).toEqual(original);
});
it('marks shifted windows and locates only the chosen block', () => {
  const locate = vi.fn();
  render(
    <DubScriptComparison
      captions={captions}
      blocks={[{ ...block, start: 13, end: 18 }]}
      onLocate={locate}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'dub.compare_title' }));
  expect(screen.getByRole('status')).toHaveTextContent('dub.compare_mismatch');
  fireEvent.click(screen.getByRole('button', { name: 'dub.issue_locate' }));
  expect(locate).toHaveBeenCalledWith('phrase');
});
it('flags high speed even though the final track stays within the SRT window', () => {
  render(
    <DubScriptComparison
      captions={captions}
      blocks={[
        {
          ...block,
          fit_status: {
            status: 'fits',
            audio_rate: 1.8,
            placed_start: 10,
            placed_end: 15,
            drift_s: 0,
          },
        },
      ]}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'dub.compare_title' }));
  expect(screen.getByRole('status')).toHaveTextContent('dub.compare_match');
  expect(screen.getByText(/dub.issue_fast/)).toBeVisible();
});
