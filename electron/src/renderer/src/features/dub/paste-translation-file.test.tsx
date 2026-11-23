import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PasteTranslation } from './paste-translation';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
afterEach(cleanup);

function deferredFile(name: string) {
  const file = new File([], name);
  let resolve!: (value: ArrayBuffer) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<ArrayBuffer>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  Object.defineProperty(file, 'arrayBuffer', { value: () => promise });
  return { file, result: { resolve, reject } };
}

it('keeps newer typing and the most recently loaded subtitle file', async () => {
  render(<PasteTranslation segments={[]} onApply={vi.fn()} onClose={vi.fn()} />);
  const input = screen.getByRole<HTMLTextAreaElement>('textbox');

  const stale = deferredFile('stale.txt');
  fireEvent.drop(input, { dataTransfer: { files: [stale.file] } });
  fireEvent.change(input, { target: { value: 'New typing' } });
  await act(async () => stale.result.resolve(new TextEncoder().encode('Stale').buffer));
  expect(input.value).toBe('New typing');

  const older = deferredFile('older.txt');
  const latest = deferredFile('latest.txt');
  fireEvent.drop(input, { dataTransfer: { files: [older.file] } });
  fireEvent.drop(input, { dataTransfer: { files: [latest.file] } });
  await act(async () => latest.result.resolve(new TextEncoder().encode('Latest file').buffer));
  await act(async () => older.result.reject(new Error('Stale read failure')));
  expect(input.value).toBe('Latest file');
  expect(screen.queryByText('scriptEdit.import_failed')).toBeNull();
});
